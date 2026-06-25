<script lang="ts">
  import 'katex/dist/katex.min.css';
  import { tick } from 'svelte';
  import type { Source } from './api';
  import { t } from './i18n.svelte';
  import { renderMarkdown } from './markdown';
  import { splitReasoning } from './reasoning';
  import { theme } from './theme.svelte';

  // `sources`, when present, turns inline [n] markers into citation chips whose
  // hovercard shows the matching source's label, snippet, and (web) link.
  // `reasoning` enables <think>…</think> splitting — only for model output, so a
  // user message (or a note) that happens to contain those tags isn't collapsed.
  let {
    source,
    sources = [],
    reasoning = false,
    live = false
  }: { source: string; sources?: Source[]; reasoning?: boolean; live?: boolean } = $props();
  let html = $state('');           // the answer
  let reasoningHtml = $state('');  // reasoning model <think>…</think> block, if any
  let container: HTMLDivElement;

  // Reasoning models emit their chain-of-thought wrapped in <think>…</think>;
  // splitReasoning (shared with copy/TTS/voice) collapses every span into a
  // collapsible block instead of rendering it inline with the answer.
  let parts = $derived(
    reasoning ? splitReasoning(source) : { reasoning: null, answer: source, thinking: false }
  );

  let mermaidPromise: Promise<typeof import('mermaid')['default']> | null = null;
  function loadMermaid() {
    if (!mermaidPromise) {
      mermaidPromise = import('mermaid').then((m) => {
        m.default.initialize({
          startOnLoad: false,
          // 'loose' lets mermaid render in-place (avoids the sandbox iframe
          // which doesn't play well with svelte hydration). We've already
          // run user content through DOMPurify upstream, so this is safe.
          securityLevel: 'loose',
          theme: theme.effective === 'light' ? 'default' : 'dark'
        });
        return m.default;
      });
    }
    return mermaidPromise;
  }

  $effect(() => {
    const { reasoning: reasoningText, answer } = parts;
    let cancelled = false;
    renderMarkdown(answer).then((result) => {
      if (!cancelled) html = result;
    });
    if (reasoningText != null && reasoningText.trim()) {
      renderMarkdown(reasoningText).then((result) => {
        if (!cancelled) reasoningHtml = result;
      });
    } else {
      reasoningHtml = '';
    }
    return () => {
      cancelled = true;
    };
  });

  // Run mermaid on any new diagrams after each html update.
  $effect(() => {
    html;
    if (!container) return;
    (async () => {
      await tick();
      const nodes = container.querySelectorAll<HTMLElement>(
        'pre.mermaid:not([data-mermaid-done])'
      );
      if (!nodes.length) return;
      nodes.forEach((n) => n.setAttribute('data-mermaid-done', '1'));
      try {
        const mermaid = await loadMermaid();
        await mermaid.run({ nodes: Array.from(nodes) });
      } catch {
        // mermaid throws on syntax errors; leave the raw source visible
      }
    })();
  });

  // Turn [n] markers (1..sources.length) into citation chips with a hovercard.
  // Runs after each html/sources update; skips code/links/already-decorated text.
  $effect(() => {
    html;
    sources;
    if (!container) return;
    (async () => {
      await tick();
      if (container) decorateCitations(container, sources);
    })();
  });

  function decorateCitations(root: HTMLElement, srcs: Source[]) {
    if (!srcs.length) return;
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
      acceptNode(node: Node) {
        if (!node.nodeValue || !/\[\d+\]/.test(node.nodeValue)) return NodeFilter.FILTER_REJECT;
        let el = (node as Text).parentElement;
        while (el && el !== root) {
          const tag = el.tagName;
          // skip code, links, rendered math, and already-decorated citations
          if (
            tag === 'CODE' || tag === 'PRE' || tag === 'A' ||
            el.classList.contains('cite-wrap') || el.classList.contains('katex')
          )
            return NodeFilter.FILTER_REJECT;
          el = el.parentElement;
        }
        return NodeFilter.FILTER_ACCEPT;
      }
    });
    const targets: Text[] = [];
    let n: Node | null;
    while ((n = walker.nextNode())) targets.push(n as Text);
    for (const t of targets) replaceCitations(t, srcs);
  }

  function replaceCitations(textNode: Text, srcs: Source[]) {
    const text = textNode.nodeValue ?? '';
    const re = /\[(\d+)\]/g;
    const frag = document.createDocumentFragment();
    let last = 0;
    let any = false;
    let m: RegExpExecArray | null;
    while ((m = re.exec(text))) {
      const idx = parseInt(m[1], 10);
      if (idx < 1 || idx > srcs.length) continue;
      any = true;
      if (m.index > last) frag.appendChild(document.createTextNode(text.slice(last, m.index)));
      frag.appendChild(buildCite(idx, srcs[idx - 1]));
      last = m.index + m[0].length;
    }
    if (!any) return;
    if (last < text.length) frag.appendChild(document.createTextNode(text.slice(last)));
    textNode.parentNode?.replaceChild(frag, textNode);
  }

  // Built with DOM APIs + textContent so untrusted source text can never inject.
  function buildCite(n: number, src: Source): HTMLElement {
    const wrap = document.createElement('span');
    wrap.className = 'cite-wrap';
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'cite';
    btn.textContent = String(n);
    const card = document.createElement('span');
    card.className = 'cite-card';
    card.setAttribute('role', 'tooltip');
    const label = document.createElement('span');
    label.className = 'cite-card-label';
    label.textContent = (src.kind === 'web' ? '🌐 ' : '📄 ') + src.label;
    card.appendChild(label);
    if (src.snippet) {
      const snip = document.createElement('span');
      snip.className = 'cite-card-snip';
      snip.textContent = src.snippet;
      card.appendChild(snip);
    }
    if (src.kind === 'web' && src.detail && /^https?:\/\//i.test(src.detail)) {
      const a = document.createElement('a');
      a.className = 'cite-card-link';
      a.href = src.detail;
      a.target = '_blank';
      a.rel = 'noreferrer noopener';
      a.textContent = src.detail;
      card.appendChild(a);
    }
    wrap.appendChild(btn);
    wrap.appendChild(card);
    return wrap;
  }

  // Flip the hovercard below / leftward when the chip is near a viewport edge,
  // so the overflow scroller (top) and right edge don't clip it.
  function positionCite(e: Event) {
    const wrap = (e.target as HTMLElement).closest?.('.cite-wrap') as HTMLElement | null;
    if (!wrap) return;
    const card = wrap.querySelector('.cite-card') as HTMLElement | null;
    if (!card) return;
    const r = wrap.getBoundingClientRect();
    card.classList.toggle('below', r.top < 180);
    card.classList.toggle('flip-left', r.left > window.innerWidth - 340);
  }

  function onClick(e: MouseEvent) {
    const target = e.target as HTMLElement;
    if (!target.matches('[data-copy]')) return;
    const block = target.closest('.code-block');
    const code = block?.querySelector('code');
    const text = code?.textContent ?? '';
    navigator.clipboard.writeText(text).then(() => {
      const orig = target.textContent;
      target.textContent = 'copied!';
      setTimeout(() => {
        target.textContent = orig;
      }, 1200);
    });
  }
</script>

{#if reasoningHtml}
  <details class="reasoning" open={parts.thinking}>
    <summary>{parts.thinking ? t('chat.thinking') : t('chat.reasoning')}</summary>
    <div class="md reasoning-body">{@html reasoningHtml}</div>
  </details>
{/if}
<div
  class="md"
  class:live
  bind:this={container}
  onclick={onClick}
  onpointerover={positionCite}
  onfocusin={positionCite}
  role="presentation"
>{@html html}</div>

<style>
  .md :global(p) { margin: 0.5em 0; }
  .md :global(p:first-child) { margin-top: 0; }
  .md :global(p:last-child) { margin-bottom: 0; }

  .md :global(pre) {
    margin: 0;
    padding: 0.85rem 1rem;
    background: var(--bg-code);
    border: 1px solid var(--border-soft);
    border-radius: 6px;
    overflow-x: auto;
    font-size: 0.85rem;
    line-height: 1.5;
  }
  .md :global(pre code) { background: transparent; padding: 0; }
  .md :global(code) {
    font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
    font-size: 0.85em;
  }
  .md :global(:not(pre) > code) {
    background: var(--bg-hover);
    padding: 0.1em 0.4em;
    border-radius: 4px;
    color: var(--text);
  }

  /* shiki dual-theme: dark by default, light via [data-theme="light"] */
  .md :global(.shiki),
  .md :global(.shiki span) { color: var(--shiki-dark); }
  .md :global(.shiki) { background-color: var(--shiki-dark-bg) !important; }
  :global([data-theme='light']) .md :global(.shiki),
  :global([data-theme='light']) .md :global(.shiki span) { color: var(--shiki-light); }
  :global([data-theme='light']) .md :global(.shiki) {
    background-color: var(--shiki-light-bg) !important;
  }

  .md :global(.code-block) {
    position: relative;
    margin: 0.75rem 0;
  }
  .md :global(.code-lang) {
    position: absolute;
    top: 0.55rem;
    left: 0.75rem;
    font-family: ui-monospace, monospace;
    font-size: 0.7rem;
    color: var(--text-faint);
    text-transform: lowercase;
    pointer-events: none;
  }
  .md :global(.code-copy) {
    position: absolute;
    top: 0.4rem;
    right: 0.4rem;
    background: var(--bg-hover);
    border: 1px solid var(--border);
    color: var(--text-dim);
    padding: 0.25rem 0.55rem;
    font: inherit;
    font-size: 0.7rem;
    border-radius: 4px;
    cursor: pointer;
    opacity: 0;
    transition: opacity 0.15s;
  }
  .md :global(.code-block:hover .code-copy) { opacity: 1; }
  /* while streaming, the markdown re-renders every token and auto-scrolls under
     the cursor — which made the hover-revealed copy button flash. It's not
     useful mid-stream, so hide it until the reply settles. */
  .md.live :global(.code-copy) { display: none; }
  .md :global(.code-copy:hover) { color: var(--text); background: var(--border); }

  .md :global(table) {
    border-collapse: collapse;
    margin: 0.75rem 0;
    font-size: 0.9rem;
  }
  .md :global(th), .md :global(td) {
    padding: 0.4rem 0.75rem;
    border: 1px solid var(--border);
    text-align: left;
  }
  .md :global(th) { background: var(--bg-hover); }

  .md :global(blockquote) {
    margin: 0.5rem 0;
    padding: 0.1rem 1rem;
    border-left: 3px solid var(--text-faint);
    color: var(--text-dim);
  }
  .md :global(a) { color: var(--accent); }
  .md :global(a:hover) { text-decoration: underline; }

  .md :global(ul), .md :global(ol) { padding-left: 1.5rem; margin: 0.5rem 0; }
  .md :global(li) { margin: 0.25rem 0; }

  .md :global(h1) { font-size: 1.5rem; margin: 1rem 0 0.5rem; }
  .md :global(h2) { font-size: 1.3rem; margin: 1rem 0 0.5rem; }
  .md :global(h3) { font-size: 1.1rem; margin: 0.75rem 0 0.5rem; }
  .md :global(h4), .md :global(h5), .md :global(h6) { font-size: 1rem; margin: 0.75rem 0 0.5rem; }
  .md :global(hr) { border: 0; border-top: 1px solid var(--border-soft); margin: 1rem 0; }

  /* mermaid diagrams */
  .md :global(pre.mermaid) {
    background: var(--bg-elev);
    padding: 0.85rem 1rem;
    overflow-x: auto;
    text-align: center;
  }
  .md :global(pre.mermaid svg) { max-width: 100%; height: auto; }

  /* KaTeX rendered math: keep slightly inset from text */
  .md :global(.katex-display) { margin: 0.65rem 0; overflow-x: auto; }

  /* inline citation chips + hovercard (built by decorateCitations) */
  .md :global(.cite-wrap) { position: relative; display: inline-block; vertical-align: baseline; }
  .md :global(.cite) {
    font: inherit;
    font-size: 0.68em;
    line-height: 1;
    vertical-align: super;
    margin: 0 0.06em;
    padding: 0.05em 0.32em;
    border: 1px solid color-mix(in srgb, var(--accent) 40%, transparent);
    border-radius: 4px;
    background: color-mix(in srgb, var(--accent) 12%, transparent);
    color: var(--accent);
    cursor: pointer;
  }
  .md :global(.cite:hover),
  .md :global(.cite:focus-visible) {
    background: color-mix(in srgb, var(--accent) 24%, transparent);
  }
  .md :global(.cite-card) {
    position: absolute;
    bottom: calc(100% + 6px);
    left: 0;
    z-index: 20;
    width: max-content;
    max-width: min(320px, 80vw);
    padding: 0.5rem 0.6rem;
    background: var(--bg-elev);
    border: 1px solid var(--border);
    border-radius: 8px;
    box-shadow: 0 6px 24px rgba(0, 0, 0, 0.25);
    display: none;
    flex-direction: column;
    gap: 0.3rem;
    white-space: normal;
    text-align: left;
    cursor: default;
  }
  .md :global(.cite-card.below) { bottom: auto; top: calc(100% + 6px); }
  .md :global(.cite-card.flip-left) { left: auto; right: 0; }
  .md :global(.cite-wrap:hover .cite-card),
  .md :global(.cite-wrap:focus-within .cite-card) { display: flex; }
  .md :global(.cite-card-label) { font-weight: 600; font-size: 0.82rem; color: var(--text); }
  .md :global(.cite-card-snip) { font-size: 0.8rem; color: var(--text-dim); line-height: 1.4; }
  .md :global(.cite-card-link) { font-size: 0.75rem; color: var(--accent); word-break: break-all; }

  /* collapsible reasoning (<think>…</think>) block */
  .reasoning {
    margin: 0 0 0.6rem;
    border: 1px solid var(--border-soft);
    border-radius: 8px;
    background: var(--bg-elev);
  }
  .reasoning > summary {
    cursor: pointer;
    padding: 0.4rem 0.7rem;
    font-size: 0.72rem;
    color: var(--text-muted);
    text-transform: uppercase;
    letter-spacing: 0.04em;
    user-select: none;
  }
  .reasoning > summary:hover { color: var(--text-dim); }
  .reasoning .reasoning-body {
    padding: 0.1rem 0.7rem 0.4rem;
    color: var(--text-dim);
    font-size: 0.92em;
    border-top: 1px solid var(--border-soft);
  }
</style>

<script lang="ts">
  import 'katex/dist/katex.min.css';
  import { tick } from 'svelte';
  import { renderMarkdown } from './markdown';
  import { theme } from './theme.svelte';

  let { source }: { source: string } = $props();
  let html = $state('');
  let container: HTMLDivElement;

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
    const src = source;
    let cancelled = false;
    renderMarkdown(src).then((result) => {
      if (!cancelled) html = result;
    });
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

<div class="md" bind:this={container} onclick={onClick} role="presentation">{@html html}</div>

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
</style>

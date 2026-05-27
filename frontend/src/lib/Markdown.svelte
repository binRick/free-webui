<script lang="ts">
  import { renderMarkdown } from './markdown';

  let { source }: { source: string } = $props();
  let html = $state('');

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

<div class="md" onclick={onClick} role="presentation">{@html html}</div>

<style>
  .md :global(p) { margin: 0.5em 0; }
  .md :global(p:first-child) { margin-top: 0; }
  .md :global(p:last-child) { margin-bottom: 0; }

  .md :global(pre) {
    margin: 0;
    padding: 0.85rem 1rem;
    background: #0d1117;
    border: 1px solid #1e293b;
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
    background: #1e293b;
    padding: 0.1em 0.4em;
    border-radius: 4px;
    color: #cbd5e1;
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
    color: #475569;
    text-transform: lowercase;
    pointer-events: none;
  }
  .md :global(.code-copy) {
    position: absolute;
    top: 0.4rem;
    right: 0.4rem;
    background: #1e293b;
    border: 1px solid #334155;
    color: #94a3b8;
    padding: 0.25rem 0.55rem;
    font: inherit;
    font-size: 0.7rem;
    border-radius: 4px;
    cursor: pointer;
    opacity: 0;
    transition: opacity 0.15s;
  }
  .md :global(.code-block:hover .code-copy) { opacity: 1; }
  .md :global(.code-copy:hover) { color: #fff; background: #334155; }

  .md :global(table) {
    border-collapse: collapse;
    margin: 0.75rem 0;
    font-size: 0.9rem;
  }
  .md :global(th), .md :global(td) {
    padding: 0.4rem 0.75rem;
    border: 1px solid #334155;
    text-align: left;
  }
  .md :global(th) { background: #1e293b; }

  .md :global(blockquote) {
    margin: 0.5rem 0;
    padding: 0.1rem 1rem;
    border-left: 3px solid #475569;
    color: #94a3b8;
  }
  .md :global(a) { color: #22d3ee; }
  .md :global(a:hover) { text-decoration: underline; }

  .md :global(ul), .md :global(ol) { padding-left: 1.5rem; margin: 0.5rem 0; }
  .md :global(li) { margin: 0.25rem 0; }

  .md :global(h1) { font-size: 1.5rem; margin: 1rem 0 0.5rem; }
  .md :global(h2) { font-size: 1.3rem; margin: 1rem 0 0.5rem; }
  .md :global(h3) { font-size: 1.1rem; margin: 0.75rem 0 0.5rem; }
  .md :global(h4), .md :global(h5), .md :global(h6) { font-size: 1rem; margin: 0.75rem 0 0.5rem; }
  .md :global(hr) { border: 0; border-top: 1px solid #1e293b; margin: 1rem 0; }
</style>

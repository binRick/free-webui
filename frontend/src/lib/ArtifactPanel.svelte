<script lang="ts">
  import { buildSrcdoc, type Artifact } from './artifacts';

  let { artifacts, onClose }: { artifacts: Artifact[]; onClose: () => void } = $props();

  let idx = $state(0);
  let full = $state(false);
  const current = $derived(artifacts[Math.min(idx, artifacts.length - 1)]);
  // SECURITY: see artifacts.ts — rendered in a sandboxed, same-origin-less iframe.
  const srcdoc = $derived(current ? buildSrcdoc(current) : '');
</script>

<aside class="artifact-panel" class:full aria-label="artifact preview">
  <header>
    <span class="title" title={current?.title}>{current?.title ?? 'artifact'}</span>
    <span class="spacer"></span>
    {#if artifacts.length > 1}
      <span class="ver">
        <button onclick={() => (idx = Math.max(0, idx - 1))} disabled={idx === 0} aria-label="previous version">◀</button>
        <span class="vc">{idx + 1}/{artifacts.length}</span>
        <button onclick={() => (idx = Math.min(artifacts.length - 1, idx + 1))} disabled={idx === artifacts.length - 1} aria-label="next version">▶</button>
      </span>
    {/if}
    <button class="icon" onclick={() => (full = !full)} title={full ? 'restore' : 'fullscreen'} aria-label="toggle fullscreen">{full ? '🗗' : '⛶'}</button>
    <button class="icon" onclick={onClose} title="close" aria-label="close artifact">✕</button>
  </header>
  <!-- sandbox WITHOUT allow-same-origin: opaque origin, no access to our cookies/API/DOM -->
  <iframe class="frame" title={current?.title ?? 'artifact'} sandbox="allow-scripts" srcdoc={srcdoc}></iframe>
</aside>

<style>
  .artifact-panel {
    display: flex;
    flex-direction: column;
    position: fixed;
    top: 0;
    right: 0;
    bottom: 0;
    z-index: 50;
    width: min(46vw, 640px);
    border-left: 1px solid var(--border);
    background: var(--bg-elev);
    box-shadow: -8px 0 24px rgba(0, 0, 0, 0.18);
  }
  .artifact-panel.full {
    inset: 0;
    z-index: 60;
    width: 100vw;
    border-left: 0;
  }
  header {
    display: flex;
    align-items: center;
    gap: 0.4rem;
    padding: 0.4rem 0.6rem;
    border-bottom: 1px solid var(--border-soft);
    font-size: 0.8rem;
  }
  .title { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-weight: 600; }
  .spacer { flex: 1; }
  .ver { display: inline-flex; align-items: center; gap: 0.25rem; }
  .vc { font-variant-numeric: tabular-nums; color: var(--text-dim); }
  header button {
    background: transparent;
    border: 1px solid var(--border-soft);
    color: var(--text-dim);
    border-radius: 5px;
    padding: 0.1rem 0.4rem;
    cursor: pointer;
    font: inherit;
    font-size: 0.78rem;
  }
  header button:hover:not(:disabled) { color: var(--text); background: var(--bg-hover); }
  header button:disabled { opacity: 0.4; cursor: not-allowed; }
  .frame {
    flex: 1;
    width: 100%;
    border: 0;
    background: #fff;
  }
  @media (max-width: 768px) {
    .artifact-panel { position: fixed; inset: 0; z-index: 60; width: 100vw; border-left: 0; }
  }
</style>

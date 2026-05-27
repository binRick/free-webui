<script lang="ts">
  import { onMount } from 'svelte';
  import Sidebar from '$lib/Sidebar.svelte';
  import { sidebar } from '$lib/sidebarState.svelte';
  import { theme } from '$lib/theme.svelte';

  let { children } = $props();

  onMount(() => theme.init());
</script>

<div class="shell" class:sidebar-open={sidebar.open}>
  <Sidebar />
  {#if sidebar.open}
    <button class="backdrop" aria-label="close sidebar" onclick={() => sidebar.close()}></button>
  {/if}
  <section class="main">
    {@render children()}
  </section>
</div>

<style>
  :global(:root),
  :global(:root[data-theme='dark']) {
    --bg: #0b1020;
    --bg-elev: #0f172a;
    --bg-sidebar: #07091a;
    --bg-hover: #1e293b;
    --bg-code: #0d1117;
    --border: #334155;
    --border-soft: #1e293b;
    --text: #e2e8f0;
    --text-dim: #94a3b8;
    --text-muted: #64748b;
    --text-faint: #475569;
    --accent: #22d3ee;
    --accent-2: #a78bfa;
    --danger: #ef4444;
    --backdrop: rgba(0, 0, 0, 0.55);
    color-scheme: dark;
  }

  :global(:root[data-theme='light']) {
    --bg: #ffffff;
    --bg-elev: #f8fafc;
    --bg-sidebar: #f1f5f9;
    --bg-hover: #e2e8f0;
    --bg-code: #f8fafc;
    --border: #cbd5e1;
    --border-soft: #e2e8f0;
    --text: #0f172a;
    --text-dim: #475569;
    --text-muted: #64748b;
    --text-faint: #94a3b8;
    --accent: #0891b2;
    --accent-2: #7c3aed;
    --danger: #dc2626;
    --backdrop: rgba(15, 23, 42, 0.35);
    color-scheme: light;
  }

  :global(html, body) {
    margin: 0;
    padding: 0;
    height: 100%;
    background: var(--bg);
    color: var(--text);
    font-family: ui-sans-serif, system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif;
  }

  .shell {
    display: flex;
    height: 100vh;
    width: 100%;
    position: relative;
  }
  .main {
    flex: 1;
    display: flex;
    flex-direction: column;
    min-width: 0;
  }

  .backdrop {
    display: none;
    position: fixed;
    inset: 0;
    background: var(--backdrop);
    z-index: 10;
    border: 0;
    cursor: pointer;
  }

  @media (max-width: 768px) {
    .backdrop { display: block; }
  }
</style>

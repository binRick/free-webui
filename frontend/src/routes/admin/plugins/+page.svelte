<script lang="ts">
  import { onMount } from 'svelte';
  import { goto } from '$app/navigation';
  import { auth } from '$lib/auth.svelte';
  import { getPlugins, type PluginRecord } from '$lib/api';

  let plugins = $state<PluginRecord[]>([]);
  let loadError = $state<string | null>(null);
  let loaded = $state(false);

  onMount(async () => {
    if (!auth.loaded) await auth.refresh();
    if (!auth.user) {
      await goto('/login', { replaceState: true });
      return;
    }
    if (auth.user.role !== 'admin') {
      await goto('/', { replaceState: true });
      return;
    }
    await refresh();
  });

  async function refresh() {
    try {
      plugins = await getPlugins();
      loadError = null;
    } catch (e) {
      loadError = (e as Error).message;
    } finally {
      loaded = true;
    }
  }

  function hooks(p: PluginRecord): string {
    const h: string[] = [];
    if (p.has_inlet) h.push('inlet');
    if (p.has_outlet) h.push('outlet');
    return h.length ? h.join(' + ') : '—';
  }
</script>

<header>
  <a href="/" class="back">← back to chat</a>
  <h1>plugins</h1>
</header>

<main>
  <p class="lede">
    Read-only view of plugins loaded from <code>FREE_WEBUI_PLUGINS_DIR</code>. Plugins are
    trusted, operator-installed in-process code; edits to the directory take effect on backend
    restart.
  </p>

  {#if loadError}
    <div class="err">{loadError}</div>
  {:else if loaded && plugins.length === 0}
    <div class="empty">
      no plugins loaded — set <code>FREE_WEBUI_PLUGINS_DIR</code> to a directory of
      <code>*.py</code> modules and restart the backend
    </div>
  {:else if plugins.length > 0}
    <table>
      <thead>
        <tr><th>name</th><th>priority</th><th>hooks</th><th>status</th></tr>
      </thead>
      <tbody>
        {#each plugins as p (p.name)}
          <tr>
            <td class="name">{p.name}</td>
            <td class="num">{p.priority}</td>
            <td>{hooks(p)}</td>
            <td>
              {#if p.error}
                <span class="badge-err" title={p.error}>error</span>
              {:else}
                <span class="badge-ok">loaded</span>
              {/if}
            </td>
          </tr>
          {#if p.error}
            <tr class="errrow"><td colspan="4"><code>{p.error}</code></td></tr>
          {/if}
        {/each}
      </tbody>
    </table>
  {/if}
</main>

<style>
  header {
    display: flex;
    align-items: center;
    gap: 1rem;
    padding: 0.75rem 1.25rem;
    border-bottom: 1px solid var(--border-soft);
  }
  .back { color: var(--text-dim); text-decoration: none; font-size: 0.85rem; }
  .back:hover { color: var(--text); }
  h1 { margin: 0; font-size: 1.1rem; font-weight: 500; color: var(--text); }

  main {
    flex: 1;
    overflow-y: auto;
    padding: 1.25rem;
    max-width: 920px;
    width: 100%;
    margin: 0 auto;
    display: flex;
    flex-direction: column;
    gap: 1.25rem;
  }
  .lede { color: var(--text-dim); font-size: 0.85rem; margin: 0; line-height: 1.5; }
  .lede code, .empty code { font-family: ui-monospace, monospace; color: var(--accent); }

  .err {
    color: var(--danger);
    background: color-mix(in srgb, var(--danger) 12%, transparent);
    padding: 0.55rem 0.75rem;
    border-radius: 6px;
    font-size: 0.85rem;
  }
  .empty {
    color: var(--text-muted);
    text-align: center;
    padding: 2rem;
    line-height: 1.6;
  }
  table { width: 100%; border-collapse: collapse; }
  th, td {
    text-align: left;
    padding: 0.55rem 0.75rem;
    border-bottom: 1px solid var(--border-soft);
    font-size: 0.9rem;
  }
  th { color: var(--text-muted); font-weight: 500; font-size: 0.72rem; text-transform: uppercase; }
  .name { font-family: ui-monospace, monospace; color: var(--accent); }
  .num { font-variant-numeric: tabular-nums; color: var(--text-dim); }
  .badge-ok {
    font-size: 0.72rem;
    color: var(--accent);
    background: color-mix(in srgb, var(--accent) 14%, transparent);
    padding: 0.15rem 0.5rem;
    border-radius: 999px;
  }
  .badge-err {
    font-size: 0.72rem;
    color: var(--danger);
    background: color-mix(in srgb, var(--danger) 14%, transparent);
    padding: 0.15rem 0.5rem;
    border-radius: 999px;
  }
  .errrow td { border-bottom: 1px solid var(--border-soft); padding-top: 0; }
  .errrow code {
    font-family: ui-monospace, monospace;
    font-size: 0.78rem;
    color: var(--danger);
    white-space: pre-wrap;
    word-break: break-word;
  }
</style>

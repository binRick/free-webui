<script lang="ts">
  import { onMount } from 'svelte';
  import { goto } from '$app/navigation';
  import { auth } from '$lib/auth.svelte';
  import {
    createConnection,
    deleteConnection,
    listConnections,
    patchConnection,
    testConnection,
    type Connection
  } from '$lib/api';

  let conns = $state<Connection[]>([]);
  let loadError = $state<string | null>(null);

  let name = $state('');
  let baseUrl = $state('');
  let apiKey = $state('');
  let busy = $state(false);
  let testResult = $state<{ ok: boolean; error: string | null; models: string[] } | null>(null);

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
      conns = await listConnections();
      loadError = null;
    } catch (e) {
      loadError = (e as Error).message;
    }
  }

  async function doTest() {
    if (!baseUrl.trim() || busy) return;
    busy = true;
    testResult = null;
    try {
      testResult = await testConnection({
        name: name.trim() || 'test',
        base_url: baseUrl.trim(),
        api_key: apiKey || null
      });
    } catch (e) {
      testResult = { ok: false, error: (e as Error).message, models: [] };
    } finally {
      busy = false;
    }
  }

  async function doCreate(e: SubmitEvent) {
    e.preventDefault();
    if (!name.trim() || !baseUrl.trim() || busy) return;
    busy = true;
    try {
      await createConnection({
        name: name.trim(),
        base_url: baseUrl.trim(),
        api_key: apiKey || null
      });
      name = '';
      baseUrl = '';
      apiKey = '';
      testResult = null;
      await refresh();
    } catch (e) {
      loadError = (e as Error).message;
    } finally {
      busy = false;
    }
  }

  async function toggleEnabled(c: Connection) {
    if (busy) return;
    busy = true;
    try {
      await patchConnection(c.id, { enabled: !c.enabled });
      await refresh();
    } catch (e) {
      loadError = (e as Error).message;
    } finally {
      busy = false;
    }
  }

  async function remove(c: Connection) {
    if (!confirm(`delete connection "${c.name}"?`) || busy) return;
    busy = true;
    try {
      await deleteConnection(c.id);
      await refresh();
    } catch (e) {
      loadError = (e as Error).message;
    } finally {
      busy = false;
    }
  }
</script>

<header>
  <a class="back" href="/">← chat</a>
  <h1>upstream connections</h1>
  <a class="tabs" href="/admin/models">installed models →</a>
</header>

<main>
  {#if loadError}<p class="error">{loadError}</p>{/if}

  <section class="card">
    <h2>add a connection</h2>
    <p class="hint">The server's env-configured upstream is always active as the
      <code>default</code> connection. Add more OpenAI-compatible upstreams here;
      a chat request is routed to whichever connection serves its model.</p>
    <form onsubmit={doCreate}>
      <div class="grid">
        <input placeholder="name (e.g. vLLM prod)" bind:value={name} maxlength="120" />
        <input placeholder="base URL (e.g. http://host:8000/v1)" bind:value={baseUrl} maxlength="500" />
        <input placeholder="API key (optional)" type="password" bind:value={apiKey} />
      </div>
      <div class="actions">
        <button type="button" onclick={doTest} disabled={!baseUrl.trim() || busy}>test</button>
        <button type="submit" disabled={!name.trim() || !baseUrl.trim() || busy}>+ add</button>
      </div>
      {#if testResult}
        <p class="test {testResult.ok ? 'ok' : 'bad'}">
          {#if testResult.ok}
            ✓ reachable — {testResult.models.length} model{testResult.models.length === 1 ? '' : 's'}{#if testResult.models.length}: <span class="mono">{testResult.models.slice(0, 8).join(', ')}{testResult.models.length > 8 ? '…' : ''}</span>{/if}
          {:else}
            ✗ {testResult.error}
          {/if}
        </p>
      {/if}
    </form>
  </section>

  <section class="card">
    <h2>connections</h2>
    <div class="item default">
      <span class="name">default <span class="muted">· env-configured</span></span>
      <span class="badge on">always on</span>
    </div>
    {#each conns as c (c.id)}
      <div class="item">
        <span class="name">{c.name}</span>
        <span class="url mono">{c.base_url}</span>
        {#if c.has_api_key}<span class="badge key">key</span>{/if}
        <span class="badge {c.enabled ? 'on' : 'off'}">{c.enabled ? 'enabled' : 'disabled'}</span>
        <button class="small" onclick={() => toggleEnabled(c)} disabled={busy}>{c.enabled ? 'disable' : 'enable'}</button>
        <button class="small del" onclick={() => remove(c)} disabled={busy}>delete</button>
      </div>
    {:else}
      <p class="empty">no extra connections — only the default upstream is active</p>
    {/each}
  </section>
</main>

<style>
  header {
    display: flex;
    align-items: center;
    gap: 1rem;
    padding: 0.75rem 1.25rem;
    border-bottom: 1px solid var(--border-soft);
  }
  header h1 { font-size: 1rem; margin: 0; }
  .back { color: var(--text-dim); text-decoration: none; font-size: 0.85rem; }
  .back:hover { color: var(--text); }
  .tabs { margin-left: auto; color: var(--accent); text-decoration: none; font-size: 0.85rem; }
  .tabs:hover { text-decoration: underline; }
  main { max-width: 760px; margin: 0 auto; padding: 1.25rem; display: flex; flex-direction: column; gap: 1.25rem; }
  .card { border: 1px solid var(--border-soft); border-radius: 8px; padding: 1rem 1.25rem; background: var(--bg-elev); }
  .card h2 { margin: 0 0 0.75rem; font-size: 0.95rem; }
  .hint { color: var(--text-dim); font-size: 0.82rem; margin: 0 0 0.75rem; }
  .grid { display: flex; flex-direction: column; gap: 0.5rem; }
  .grid input {
    background: var(--bg);
    color: var(--text);
    border: 1px solid var(--border-soft);
    border-radius: 6px;
    padding: 0.45rem 0.6rem;
    font: inherit;
    font-size: 0.85rem;
  }
  .actions { display: flex; gap: 0.5rem; margin-top: 0.6rem; }
  button {
    background: var(--bg);
    color: var(--text);
    border: 1px solid var(--border);
    border-radius: 6px;
    padding: 0.4rem 0.7rem;
    font: inherit;
    font-size: 0.82rem;
    cursor: pointer;
  }
  button:hover:not(:disabled) { background: var(--bg-hover); }
  button:disabled { opacity: 0.5; cursor: default; }
  button.small { padding: 0.25rem 0.55rem; font-size: 0.76rem; }
  button.del:hover:not(:disabled) { color: var(--danger); border-color: var(--danger); }
  .test { font-size: 0.82rem; margin: 0.6rem 0 0; }
  .test.ok { color: var(--accent); }
  .test.bad { color: var(--danger); }
  .item {
    display: flex;
    align-items: center;
    gap: 0.6rem;
    padding: 0.5rem 0;
    border-top: 1px solid var(--border-soft);
  }
  .item.default { color: var(--text-dim); }
  .item .name { font-weight: 500; white-space: nowrap; }
  .item .url { color: var(--text-muted); font-size: 0.8rem; overflow: hidden; text-overflow: ellipsis; }
  .mono { font-family: ui-monospace, monospace; }
  .muted { color: var(--text-muted); font-weight: 400; }
  .badge { font-size: 0.7rem; padding: 0.1rem 0.45rem; border-radius: 999px; border: 1px solid var(--border-soft); }
  .badge.on { color: var(--accent); border-color: color-mix(in srgb, var(--accent) 40%, transparent); }
  .badge.off { color: var(--text-muted); }
  .badge.key { color: var(--text-dim); }
  .item button { margin-left: auto; }
  .item button + button { margin-left: 0.4rem; }
  .item .url { margin-left: 0; }
  .empty { color: var(--text-muted); font-size: 0.85rem; padding: 0.5rem 0 0; }
  .error { color: var(--danger); font-size: 0.85rem; }
</style>

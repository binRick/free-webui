<script lang="ts">
  import { onMount } from 'svelte';
  import { goto } from '$app/navigation';
  import { auth } from '$lib/auth.svelte';
  import {
    createOpenApiServer,
    deleteOpenApiServer,
    listOpenApiServers,
    patchOpenApiServer,
    type OpenApiServer
  } from '$lib/api';

  let servers = $state<OpenApiServer[]>([]);
  let loadError = $state<string | null>(null);

  let newName = $state('');
  let newUrl = $state('');
  let newHeadersRaw = $state('');
  let createBusy = $state(false);
  let createError = $state<string | null>(null);

  onMount(async () => {
    if (!auth.loaded) await auth.refresh();
    if (!auth.user) {
      await goto('/login', { replaceState: true });
      return;
    }
    await refresh();
  });

  async function refresh() {
    try {
      servers = await listOpenApiServers();
      loadError = null;
    } catch (e) {
      loadError = (e as Error).message;
    }
  }

  function parseHeaders(raw: string): Record<string, string> | null {
    const trimmed = raw.trim();
    if (!trimmed) return null;
    try {
      const parsed = JSON.parse(trimmed);
      if (typeof parsed === 'object' && parsed && !Array.isArray(parsed)) {
        return parsed as Record<string, string>;
      }
    } catch {
      // also accept "k: v" lines
    }
    const out: Record<string, string> = {};
    for (const line of trimmed.split('\n')) {
      const idx = line.indexOf(':');
      if (idx === -1) continue;
      out[line.slice(0, idx).trim()] = line.slice(idx + 1).trim();
    }
    return Object.keys(out).length ? out : null;
  }

  async function create(e: SubmitEvent) {
    e.preventDefault();
    if (createBusy) return;
    createBusy = true;
    createError = null;
    try {
      await createOpenApiServer({
        name: newName.trim(),
        url: newUrl.trim(),
        headers: parseHeaders(newHeadersRaw)
      });
      newName = '';
      newUrl = '';
      newHeadersRaw = '';
      await refresh();
    } catch (err) {
      createError = (err as Error).message;
    } finally {
      createBusy = false;
    }
  }

  async function toggleEnabled(s: OpenApiServer) {
    await patchOpenApiServer(s.id, { enabled: !s.enabled });
    await refresh();
  }

  async function remove(s: OpenApiServer) {
    if (!confirm(`remove OpenAPI server "${s.name}"?`)) return;
    await deleteOpenApiServer(s.id);
    await refresh();
  }
</script>

<header>
  <a class="back" href="/">← back to chat</a>
  <h1>account · OpenAPI tool servers</h1>
</header>

<main>
  <section class="card">
    <h2>add a server</h2>
    <p class="hint">
      A URL to an OpenAPI (3.x, JSON) spec — e.g. <code>https://api.example.com/openapi.json</code>.
      Its operations are offered to the model as tools named
      <code>openapi_&lt;id&gt;_&lt;operationId&gt;</code> whenever the conversation has
      tools enabled, and called over HTTP with the params the model supplies.
    </p>
    <form onsubmit={create}>
      <input bind:value={newName} placeholder="name (e.g. weather-api)" required />
      <input bind:value={newUrl} placeholder="https://api.example.com/openapi.json" required />
      <textarea
        bind:value={newHeadersRaw}
        rows="2"
        placeholder='optional headers — JSON &lbrace;"Authorization":"Bearer …"&rbrace; or one "Key: value" per line'
      ></textarea>
      <button type="submit" disabled={createBusy || !newName.trim() || !newUrl.trim()}>
        {createBusy ? 'adding…' : 'add'}
      </button>
    </form>
    {#if createError}<div class="err">{createError}</div>{/if}
  </section>

  <section class="card">
    <h2>your servers</h2>
    {#if loadError}<div class="err">{loadError}</div>{/if}
    {#if servers.length === 0}
      <div class="empty">no OpenAPI tool servers configured</div>
    {:else}
      <ul>
        {#each servers as s (s.id)}
          <li>
            <div class="row">
              <span class="sname">{s.name}</span>
              <code class="surl">{s.url}</code>
              <span class="pill" class:on={s.enabled}>{s.enabled ? 'enabled' : 'disabled'}</span>
            </div>
            <div class="actions">
              <button onclick={() => toggleEnabled(s)}>{s.enabled ? 'disable' : 'enable'}</button>
              <button class="del" onclick={() => remove(s)}>delete</button>
            </div>
          </li>
        {/each}
      </ul>
    {/if}
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
  .back { color: var(--text-dim); text-decoration: none; font-size: 0.85rem; }
  .back:hover { color: var(--text); }
  h1 { margin: 0; font-size: 1.05rem; font-weight: 500; color: var(--text); }
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
  .card {
    background: var(--bg-elev);
    border: 1px solid var(--border-soft);
    border-radius: 8px;
    padding: 1rem 1.25rem;
    display: flex;
    flex-direction: column;
    gap: 0.6rem;
  }
  h2 { margin: 0; font-size: 0.85rem; text-transform: uppercase; letter-spacing: 0.05em; color: var(--text-muted); font-weight: 500; }
  .hint { color: var(--text-dim); font-size: 0.85rem; margin: 0; }
  code { font-family: ui-monospace, monospace; background: var(--bg-hover); padding: 0.05em 0.3em; border-radius: 4px; }
  form { display: flex; flex-direction: column; gap: 0.5rem; }
  form input, form textarea {
    background: var(--bg);
    color: var(--text);
    border: 1px solid var(--border);
    border-radius: 6px;
    padding: 0.5rem 0.75rem;
    font: inherit;
  }
  form button {
    align-self: flex-start;
    background: var(--bg-hover);
    color: var(--text);
    border: 1px solid var(--border);
    border-radius: 6px;
    padding: 0.5rem 1rem;
    font: inherit;
    cursor: pointer;
  }
  form button:disabled { opacity: 0.5; cursor: not-allowed; }
  ul { list-style: none; padding: 0; margin: 0; display: flex; flex-direction: column; gap: 0.5rem; }
  li {
    border: 1px solid var(--border-soft);
    border-radius: 6px;
    padding: 0.65rem 0.8rem;
    background: var(--bg);
    display: flex;
    flex-direction: column;
    gap: 0.4rem;
  }
  .row { display: flex; gap: 0.5rem; align-items: center; flex-wrap: wrap; }
  .sname { color: var(--accent); font-family: ui-monospace, monospace; }
  .surl { color: var(--text-dim); font-size: 0.78rem; flex: 1; overflow: hidden; text-overflow: ellipsis; }
  .pill {
    font-size: 0.7rem;
    padding: 0.1rem 0.5rem;
    border-radius: 999px;
    background: var(--bg-hover);
    color: var(--text-muted);
    border: 1px solid var(--border-soft);
  }
  .pill.on {
    background: color-mix(in srgb, var(--accent-2) 18%, transparent);
    color: var(--accent-2);
    border-color: color-mix(in srgb, var(--accent-2) 40%, transparent);
  }
  .actions { display: flex; gap: 0.35rem; }
  .actions button {
    background: transparent;
    border: 1px solid var(--border);
    color: var(--text-dim);
    padding: 0.2rem 0.6rem;
    font: inherit;
    font-size: 0.75rem;
    border-radius: 4px;
    cursor: pointer;
  }
  .actions button:hover { color: var(--text); background: var(--bg-hover); }
  .actions .del:hover { color: var(--danger); border-color: var(--danger); }
  .empty { color: var(--text-muted); font-size: 0.9rem; padding: 0.5rem 0; }
  .err {
    color: var(--danger);
    background: color-mix(in srgb, var(--danger) 12%, transparent);
    padding: 0.5rem 0.75rem;
    border-radius: 6px;
    font-size: 0.85rem;
  }
</style>

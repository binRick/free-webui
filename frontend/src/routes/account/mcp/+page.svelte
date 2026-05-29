<script lang="ts">
  import { onMount } from 'svelte';
  import { goto } from '$app/navigation';
  import { auth } from '$lib/auth.svelte';
  import {
    createMcpServer,
    deleteMcpServer,
    listMcpServers,
    patchMcpServer,
    probeMcpServer,
    type McpServer
  } from '$lib/api';

  let servers = $state<McpServer[]>([]);
  let loadError = $state<string | null>(null);

  let newName = $state('');
  let newUrl = $state('');
  let newHeadersRaw = $state('');
  let createBusy = $state(false);
  let createError = $state<string | null>(null);

  let probe = $state<Record<number, { ok: boolean; msg: string }>>({});

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
      servers = await listMcpServers();
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
      await createMcpServer({
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

  async function toggleEnabled(s: McpServer) {
    await patchMcpServer(s.id, { enabled: !s.enabled });
    await refresh();
  }

  async function doProbe(s: McpServer) {
    probe[s.id] = { ok: false, msg: 'probing…' };
    try {
      const result = await probeMcpServer(s.id);
      const names = (result.tools ?? []).map((t) => t.name).join(', ') || '(no tools)';
      probe[s.id] = { ok: true, msg: `${result.tools?.length ?? 0} tools: ${names}` };
    } catch (e) {
      probe[s.id] = { ok: false, msg: (e as Error).message };
    }
  }

  async function remove(s: McpServer) {
    if (!confirm(`remove MCP server "${s.name}"?`)) return;
    await deleteMcpServer(s.id);
    await refresh();
  }
</script>

<header>
  <a class="back" href="/">← back to chat</a>
  <h1>account · MCP servers</h1>
</header>

<main>
  <section class="card">
    <h2>add a server</h2>
    <p class="hint">
      Any Model Context Protocol server reachable over HTTP/JSON-RPC. Its
      tools are namespaced as <code>mcp_&lt;id&gt;_&lt;tool&gt;</code> and
      offered to the model whenever the conversation has tools enabled.
    </p>
    <form onsubmit={create}>
      <input bind:value={newName} placeholder="name (e.g. weather)" required />
      <input
        bind:value={newUrl}
        placeholder="https://your-mcp.example.com/jsonrpc"
        required
      />
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
      <div class="empty">no MCP servers configured</div>
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
              <button onclick={() => doProbe(s)}>probe</button>
              <button class="del" onclick={() => remove(s)}>delete</button>
            </div>
            {#if probe[s.id]}
              <div class="probe" class:ok={probe[s.id].ok}>{probe[s.id].msg}</div>
            {/if}
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
  .probe {
    color: var(--danger);
    background: color-mix(in srgb, var(--danger) 10%, transparent);
    padding: 0.4rem 0.6rem;
    border-radius: 4px;
    font-size: 0.78rem;
  }
  .probe.ok { color: var(--accent-2); background: color-mix(in srgb, var(--accent-2) 12%, transparent); }
  .empty { color: var(--text-muted); font-size: 0.9rem; padding: 0.5rem 0; }
  .err {
    color: var(--danger);
    background: color-mix(in srgb, var(--danger) 12%, transparent);
    padding: 0.5rem 0.75rem;
    border-radius: 6px;
    font-size: 0.85rem;
  }
</style>

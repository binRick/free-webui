<script lang="ts">
  import { onMount } from 'svelte';
  import { goto } from '$app/navigation';
  import { auth } from '$lib/auth.svelte';
  import {
    listApiKeys,
    mintApiKey,
    revokeApiKey,
    deleteAccount,
    ACCOUNT_EXPORT_URL,
    type ApiKey
  } from '$lib/api';

  let keys = $state<ApiKey[]>([]);
  let newName = $state('');
  let busy = $state(false);
  let mintedRaw = $state<string | null>(null);
  let mintedName = $state<string>('');

  let deletePw = $state('');
  let deleteBusy = $state(false);
  let deleteErr = $state('');

  async function removeAccount() {
    if (deleteBusy || !deletePw) return;
    if (!confirm('Permanently delete your account and ALL your data? This cannot be undone.')) return;
    deleteBusy = true;
    deleteErr = '';
    const res = await deleteAccount(deletePw);
    deleteBusy = false;
    if (res === true) {
      auth.user = null;
      await goto('/login', { replaceState: true });
    } else {
      deleteErr = res;
    }
  }

  onMount(async () => {
    if (!auth.loaded) await auth.refresh();
    if (!auth.user) {
      await goto('/login', { replaceState: true });
      return;
    }
    keys = await listApiKeys();
  });

  async function mint() {
    if (busy || !newName.trim()) return;
    busy = true;
    try {
      const k = await mintApiKey(newName.trim());
      mintedRaw = k.key ?? null;
      mintedName = k.name;
      newName = '';
      keys = await listApiKeys();
    } finally {
      busy = false;
    }
  }

  async function revoke(id: number) {
    if (!confirm('revoke this key? clients using it will start failing.')) return;
    await revokeApiKey(id);
    keys = await listApiKeys();
  }

  function copy(text: string) {
    navigator.clipboard.writeText(text);
  }

  function fmtDate(ts: number | null): string {
    if (!ts) return '—';
    return new Date(ts * 1000).toISOString().replace('T', ' ').slice(0, 19);
  }
</script>

<header>
  <a class="back" href="/">← back to chat</a>
  <h1>account · api keys</h1>
  <a class="tabs" href="/account/mcp">MCP servers →</a>
  <a class="tabs" href="/account/openapi">OpenAPI tools →</a>
</header>

<main>
  <section class="card">
    <h2>mint a new key</h2>
    <p class="hint">
      Use this to talk to free-webui from external clients via the
      OpenAI-compatible <code>/v1/chat/completions</code> and
      <code>/v1/models</code> endpoints.
    </p>
    <div class="row">
      <input bind:value={newName} placeholder="key name (e.g. shell-cli)" disabled={busy} />
      <button onclick={mint} disabled={busy || !newName.trim()}>
        {busy ? 'minting…' : 'mint key'}
      </button>
    </div>
    {#if mintedRaw}
      <div class="minted">
        <div class="minted-head">
          new key <code>{mintedName}</code> — <strong>copy it now, it won't be shown again</strong>
        </div>
        <pre>{mintedRaw}</pre>
        <button class="copy" onclick={() => copy(mintedRaw!)}>copy</button>
        <button class="copy" onclick={() => (mintedRaw = null)}>dismiss</button>
      </div>
    {/if}
  </section>

  <section class="card">
    <h2>your keys</h2>
    {#if keys.length === 0}
      <div class="empty">no keys yet</div>
    {:else}
      <table>
        <thead>
          <tr><th>name</th><th>prefix</th><th>created</th><th>last used</th><th></th></tr>
        </thead>
        <tbody>
          {#each keys as k (k.id)}
            <tr>
              <td>{k.name}</td>
              <td><code>{k.key_prefix}</code></td>
              <td class="ts">{fmtDate(k.created_at)}</td>
              <td class="ts">{fmtDate(k.last_used_at)}</td>
              <td><button class="del" onclick={() => revoke(k.id)}>revoke</button></td>
            </tr>
          {/each}
        </tbody>
      </table>
    {/if}
  </section>

  <section class="card">
    <h2>sessions</h2>
    <p class="muted">Sign out of every device. Other sessions are revoked on their next request.</p>
    <button class="danger" onclick={() => auth.logoutEverywhere()}>log out everywhere</button>
  </section>

  <section class="card">
    <h2>your data</h2>
    <p class="muted">Download everything in your account — conversations, prompts, notes, memories,
      collections and more — as a single JSON file.</p>
    <a class="action" href={ACCOUNT_EXPORT_URL} download>↓ export my data (JSON)</a>
  </section>

  <section class="card">
    <h2>delete account</h2>
    <p class="muted">Permanently delete your account and all associated data. This cannot be undone.</p>
    <div class="del-row">
      <input
        type="password"
        placeholder="confirm your password"
        bind:value={deletePw}
        autocomplete="current-password"
      />
      <button class="danger" disabled={deleteBusy || !deletePw} onclick={removeAccount}>
        {deleteBusy ? 'deleting…' : 'delete my account'}
      </button>
    </div>
    {#if deleteErr}<p class="del-err">{deleteErr}</p>{/if}
  </section>

  <section class="card">
    <h2>example usage</h2>
    <pre>curl http://localhost:8000/v1/chat/completions \
  -H "Authorization: Bearer YOUR_KEY" \
  -H "Content-Type: application/json" \
  -d '{`{`}
    "model": "llama3.2",
    "messages": [{`{`}"role": "user", "content": "hello"{`}`}],
    "stream": true
  {`}`}'</pre>
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
  .tabs {
    margin-left: auto;
    color: var(--accent);
    text-decoration: none;
    font-size: 0.85rem;
  }
  .tabs:hover { text-decoration: underline; }
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
  .row { display: flex; gap: 0.5rem; }
  .row input {
    flex: 1;
    background: var(--bg);
    color: var(--text);
    border: 1px solid var(--border);
    border-radius: 6px;
    padding: 0.5rem 0.75rem;
    font: inherit;
  }
  .row button, .copy, .del {
    background: var(--bg-hover);
    color: var(--text);
    border: 1px solid var(--border);
    border-radius: 6px;
    padding: 0.4rem 0.85rem;
    font: inherit;
    cursor: pointer;
  }
  .row button:disabled { opacity: 0.5; cursor: not-allowed; }
  .minted {
    background: color-mix(in srgb, var(--accent) 12%, transparent);
    border: 1px solid color-mix(in srgb, var(--accent) 50%, transparent);
    padding: 0.85rem;
    border-radius: 6px;
    display: flex;
    flex-direction: column;
    gap: 0.5rem;
  }
  .minted-head { color: var(--text); font-size: 0.9rem; }
  .minted pre {
    margin: 0;
    background: var(--bg);
    padding: 0.5rem 0.75rem;
    border-radius: 4px;
    font-family: ui-monospace, monospace;
    overflow-x: auto;
  }
  .minted .copy { width: max-content; }
  table { width: 100%; border-collapse: collapse; }
  th, td { text-align: left; padding: 0.5rem 0.75rem; border-bottom: 1px solid var(--border-soft); font-size: 0.9rem; }
  th { font-size: 0.72rem; color: var(--text-muted); font-weight: 500; text-transform: uppercase; }
  .ts { color: var(--text-muted); font-size: 0.78rem; font-family: ui-monospace, monospace; }
  .del { padding: 0.25rem 0.6rem; font-size: 0.78rem; }
  .del:hover { color: var(--danger); border-color: var(--danger); }
  .empty { color: var(--text-muted); font-size: 0.9rem; padding: 0.5rem 0; }
  .muted { color: var(--text-dim); font-size: 0.85rem; margin: 0 0 0.6rem; }
  .danger {
    background: color-mix(in srgb, var(--danger) 14%, transparent);
    color: var(--danger);
    border: 1px solid color-mix(in srgb, var(--danger) 40%, transparent);
    border-radius: 6px;
    padding: 0.45rem 0.8rem;
    font: inherit;
    font-size: 0.85rem;
    cursor: pointer;
  }
  .danger:hover { background: color-mix(in srgb, var(--danger) 22%, transparent); }
  .danger:disabled { opacity: 0.5; cursor: default; }
  .del-row { display: flex; gap: 0.5rem; flex-wrap: wrap; align-items: center; }
  .del-row input {
    flex: 1; min-width: 12rem; padding: 0.45rem 0.7rem; font: inherit;
    background: var(--bg); color: var(--text);
    border: 1px solid var(--border); border-radius: 6px;
  }
  .del-err { color: var(--danger); font-size: 0.85rem; margin: 0.5rem 0 0; }
  pre {
    background: var(--bg);
    padding: 0.75rem 1rem;
    border-radius: 6px;
    overflow-x: auto;
    font-size: 0.85rem;
    color: var(--text);
  }
  code {
    background: var(--bg-hover);
    padding: 0.1em 0.35em;
    border-radius: 4px;
    font-size: 0.85em;
    font-family: ui-monospace, monospace;
  }
</style>

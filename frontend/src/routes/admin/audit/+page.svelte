<script lang="ts">
  import { onMount } from 'svelte';
  import { goto } from '$app/navigation';
  import { auth } from '$lib/auth.svelte';
  import { listAudit, type AuditEntry } from '$lib/api';

  let entries = $state<AuditEntry[]>([]);
  let loadError = $state<string | null>(null);

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
    try {
      entries = await listAudit(200);
    } catch (e) {
      loadError = (e as Error).message;
    }
  });

  function when(ts: number): string {
    return new Date(ts * 1000).toLocaleString();
  }
</script>

<header>
  <a class="back" href="/">← chat</a>
  <h1>audit log</h1>
  <a class="tabs" href="/admin/users">users →</a>
</header>

<main>
  {#if loadError}<p class="error">{loadError}</p>{/if}
  <section class="card">
    {#if entries.length === 0}
      <p class="empty">no audit entries yet</p>
    {:else}
      <table>
        <thead>
          <tr><th>when</th><th>admin</th><th>action</th><th>detail</th></tr>
        </thead>
        <tbody>
          {#each entries as e (e.id)}
            <tr>
              <td class="ts">{when(e.created_at)}</td>
              <td>{e.username ?? '—'}</td>
              <td><code>{e.action}</code></td>
              <td class="detail">{e.detail ?? ''}</td>
            </tr>
          {/each}
        </tbody>
      </table>
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
  header h1 { font-size: 1rem; margin: 0; }
  .back { color: var(--text-dim); text-decoration: none; font-size: 0.85rem; }
  .back:hover { color: var(--text); }
  .tabs { margin-left: auto; color: var(--accent); text-decoration: none; font-size: 0.85rem; }
  .tabs:hover { text-decoration: underline; }
  main { max-width: 860px; margin: 0 auto; padding: 1.25rem; }
  .card { border: 1px solid var(--border-soft); border-radius: 8px; padding: 0.5rem 1rem; background: var(--bg-elev); overflow-x: auto; }
  table { width: 100%; border-collapse: collapse; font-size: 0.85rem; }
  th { text-align: left; color: var(--text-muted); font-weight: 500; font-size: 0.72rem; text-transform: uppercase; padding: 0.5rem 0.6rem; }
  td { padding: 0.45rem 0.6rem; border-top: 1px solid var(--border-soft); vertical-align: top; }
  .ts { color: var(--text-muted); white-space: nowrap; font-family: ui-monospace, monospace; font-size: 0.78rem; }
  code { color: var(--accent); }
  .detail { color: var(--text-dim); word-break: break-word; }
  .empty { color: var(--text-muted); font-size: 0.85rem; padding: 0.5rem; }
  .error { color: var(--danger); font-size: 0.85rem; }
</style>

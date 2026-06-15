<script lang="ts">
  import { onMount } from 'svelte';
  import { goto } from '$app/navigation';
  import { auth } from '$lib/auth.svelte';
  import { listFeedback, type FeedbackRow } from '$lib/api';

  let rows = $state<FeedbackRow[]>([]);
  let filter = $state<0 | 1 | -1>(0);
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
    await load();
  });

  async function load() {
    try {
      rows = await listFeedback(filter || undefined);
      loadError = null;
    } catch (e) {
      loadError = (e as Error).message;
    }
  }

  async function setFilter(f: 0 | 1 | -1) {
    filter = f;
    await load();
  }

  function when(ts: number): string {
    return new Date(ts * 1000).toLocaleString();
  }
</script>

<header>
  <a class="back" href="/">← chat</a>
  <h1>feedback</h1>
  <div class="filters">
    <button class:on={filter === 0} onclick={() => setFilter(0)}>all</button>
    <button class:on={filter === 1} onclick={() => setFilter(1)}>👍</button>
    <button class:on={filter === -1} onclick={() => setFilter(-1)}>👎</button>
  </div>
</header>

<main>
  {#if loadError}<p class="error">{loadError}</p>{/if}
  <section class="card">
    {#if rows.length === 0}
      <p class="empty">no feedback yet</p>
    {:else}
      {#each rows as r (r.id)}
        <div class="item">
          <span class="rating">{r.rating === 1 ? '👍' : '👎'}</span>
          <div class="body">
            <div class="meta">
              <span>{r.username ?? '—'}</span>
              {#if r.conversation_id}
                <a href="/chat/{r.conversation_id}">{r.conversation_title || 'conversation'}</a>
              {/if}
              <span class="ts">{when(r.created_at)}</span>
            </div>
            <div class="snippet">{r.snippet}</div>
            {#if r.comment}<div class="comment">“{r.comment}”</div>{/if}
          </div>
        </div>
      {/each}
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
  .filters { margin-left: auto; display: flex; gap: 0.35rem; }
  .filters button {
    background: var(--bg); color: var(--text-dim); border: 1px solid var(--border-soft);
    border-radius: 6px; padding: 0.25rem 0.55rem; font: inherit; font-size: 0.8rem; cursor: pointer;
  }
  .filters button.on { color: var(--text); border-color: var(--accent); }
  main { max-width: 760px; margin: 0 auto; padding: 1.25rem; }
  .card { border: 1px solid var(--border-soft); border-radius: 8px; padding: 0.5rem 1rem; background: var(--bg-elev); }
  .item { display: flex; gap: 0.75rem; padding: 0.65rem 0; border-top: 1px solid var(--border-soft); }
  .item:first-child { border-top: 0; }
  .rating { font-size: 1.1rem; }
  .body { flex: 1; min-width: 0; }
  .meta { display: flex; gap: 0.75rem; font-size: 0.78rem; color: var(--text-muted); align-items: baseline; }
  .meta a { color: var(--accent); text-decoration: none; }
  .meta a:hover { text-decoration: underline; }
  .ts { margin-left: auto; font-family: ui-monospace, monospace; }
  .snippet { font-size: 0.85rem; color: var(--text-dim); margin-top: 0.25rem; }
  .comment { font-size: 0.85rem; color: var(--text); margin-top: 0.25rem; font-style: italic; }
  .empty { color: var(--text-muted); font-size: 0.85rem; padding: 0.5rem; }
  .error { color: var(--danger); font-size: 0.85rem; }
</style>

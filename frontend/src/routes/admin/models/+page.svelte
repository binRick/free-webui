<script lang="ts">
  import { onMount } from 'svelte';
  import { goto } from '$app/navigation';
  import { auth } from '$lib/auth.svelte';
  import {
    deleteInstalledModel,
    listInstalledModels,
    pullModel,
    type InstalledModel
  } from '$lib/api';

  let models = $state<InstalledModel[]>([]);
  let loadError = $state<string | null>(null);
  let pullName = $state('');
  let pulling = $state(false);
  let pullStatus = $state<string>('');
  let pullProgress = $state<{ total: number; completed: number } | null>(null);
  let pullError = $state<string | null>(null);

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
      models = await listInstalledModels();
      loadError = null;
    } catch (e) {
      loadError = (e as Error).message;
    }
  }

  async function doPull() {
    const name = pullName.trim();
    if (!name || pulling) return;
    pulling = true;
    pullStatus = 'starting…';
    pullProgress = null;
    pullError = null;
    try {
      await pullModel(name, {
        onEvent: (e) => {
          if (typeof e.status === 'string') pullStatus = e.status;
          if (typeof e.total === 'number' && typeof e.completed === 'number') {
            pullProgress = { total: e.total, completed: e.completed };
          }
          if (typeof e.error === 'string') {
            pullError = e.error;
          }
        }
      });
      await refresh();
      pullName = '';
    } catch (e) {
      pullError = (e as Error).message;
    } finally {
      pulling = false;
    }
  }

  async function remove(name: string) {
    if (!confirm(`delete ${name}? this removes the model files locally.`)) return;
    try {
      await deleteInstalledModel(name);
      await refresh();
    } catch (e) {
      loadError = (e as Error).message;
    }
  }

  function formatBytes(n: number | null): string {
    if (!n) return '—';
    if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
    if (n < 1024 * 1024 * 1024) return `${(n / 1024 / 1024).toFixed(1)} MB`;
    return `${(n / 1024 / 1024 / 1024).toFixed(2)} GB`;
  }

  let pullPercent = $derived(
    pullProgress && pullProgress.total > 0
      ? Math.round((pullProgress.completed / pullProgress.total) * 100)
      : null
  );
</script>

<header>
  <a href="/" class="back">← back to chat</a>
  <h1>installed models</h1>
</header>

<main>
  <section class="pull">
    <div class="row">
      <input
        bind:value={pullName}
        placeholder="model name to pull (e.g. llama3.2, nomic-embed-text)"
        disabled={pulling}
      />
      <button onclick={doPull} disabled={pulling || !pullName.trim()}>
        {pulling ? 'pulling…' : 'pull'}
      </button>
    </div>
    {#if pulling || pullStatus || pullError}
      <div class="status">
        {#if pullError}
          <div class="err">error: {pullError}</div>
        {:else}
          <div class="msg">{pullStatus}</div>
          {#if pullPercent != null}
            <div class="bar"><div class="bar-fill" style="width: {pullPercent}%"></div></div>
            <div class="pct">{pullPercent}% — {formatBytes(pullProgress?.completed ?? 0)} / {formatBytes(pullProgress?.total ?? 0)}</div>
          {/if}
        {/if}
      </div>
    {/if}
  </section>

  {#if loadError}
    <div class="err">{loadError}</div>
  {:else if models.length === 0}
    <div class="empty">no models installed — pull one above to get started</div>
  {:else}
    <table>
      <thead>
        <tr><th>name</th><th>size</th><th>modified</th><th></th></tr>
      </thead>
      <tbody>
        {#each models as m (m.name)}
          <tr>
            <td class="name">{m.name}</td>
            <td>{formatBytes(m.size)}</td>
            <td class="ts">{m.modified_at ?? '—'}</td>
            <td><button class="del" onclick={() => remove(m.name)}>delete</button></td>
          </tr>
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

  .pull {
    background: var(--bg-elev);
    border: 1px solid var(--border-soft);
    border-radius: 8px;
    padding: 1rem;
    display: flex;
    flex-direction: column;
    gap: 0.65rem;
  }
  .pull .row { display: flex; gap: 0.5rem; }
  .pull input {
    flex: 1;
    background: var(--bg);
    color: var(--text);
    border: 1px solid var(--border);
    border-radius: 6px;
    padding: 0.5rem 0.75rem;
    font: inherit;
  }
  .pull button {
    background: var(--bg-hover);
    color: var(--text);
    border: 1px solid var(--border);
    border-radius: 6px;
    padding: 0.5rem 1rem;
    font: inherit;
    cursor: pointer;
  }
  .pull button:disabled { opacity: 0.5; cursor: not-allowed; }
  .status .msg { color: var(--text-dim); font-size: 0.85rem; }
  .bar {
    height: 6px;
    background: var(--bg-hover);
    border-radius: 3px;
    overflow: hidden;
    margin-top: 0.35rem;
  }
  .bar-fill { height: 100%; background: var(--accent); transition: width 0.2s ease; }
  .pct { font-size: 0.75rem; color: var(--text-muted); margin-top: 0.25rem; }
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
  }
  table {
    width: 100%;
    border-collapse: collapse;
  }
  th, td {
    text-align: left;
    padding: 0.55rem 0.75rem;
    border-bottom: 1px solid var(--border-soft);
    font-size: 0.9rem;
  }
  th { color: var(--text-muted); font-weight: 500; font-size: 0.72rem; text-transform: uppercase; }
  .name { font-family: ui-monospace, monospace; color: var(--accent); }
  .ts { color: var(--text-muted); font-size: 0.8rem; }
  .del {
    background: transparent;
    border: 1px solid var(--border);
    color: var(--text-dim);
    padding: 0.25rem 0.6rem;
    border-radius: 4px;
    font: inherit;
    font-size: 0.78rem;
    cursor: pointer;
  }
  .del:hover { color: var(--danger); border-color: var(--danger); }
</style>

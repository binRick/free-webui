<script lang="ts">
  import { onMount } from 'svelte';
  import { goto } from '$app/navigation';
  import { listChannels, createChannel, type Channel } from '$lib/api';
  import { toasts } from '$lib/toastStore.svelte';

  let channels = $state<Channel[]>([]);
  let creating = $state(false);

  onMount(async () => {
    channels = await listChannels();
  });

  async function newChannel() {
    const name = window.prompt('channel name')?.trim();
    if (!name || creating) return;
    creating = true;
    try {
      const c = await createChannel(name);
      goto(`/channels/${c.id}`);
    } catch (e) {
      toasts.error((e as Error).message);
    } finally {
      creating = false;
    }
  }
</script>

<svelte:head><title>channels · free-webui</title></svelte:head>

<div class="channels">
  <header>
    <a class="back" href="/">← chat</a>
    <h1>channels</h1>
    <button class="new" onclick={newChannel} disabled={creating}>+ channel</button>
  </header>

  {#if channels.length === 0}
    <div class="empty">no channels yet — create one to start a real-time room</div>
  {:else}
    <ul class="list">
      {#each channels as c (c.id)}
        <li>
          <a href="/channels/{c.id}">
            <span class="hash">#</span>
            <span class="name">{c.name}</span>
            {#if c.description}<span class="desc">{c.description}</span>{/if}
          </a>
        </li>
      {/each}
    </ul>
  {/if}
</div>

<style>
  .channels {
    max-width: 720px;
    margin: 0 auto;
    width: 100%;
    padding: 1rem;
    display: flex;
    flex-direction: column;
    min-height: 0;
  }
  header {
    display: flex;
    align-items: center;
    gap: 1rem;
    margin-bottom: 1rem;
  }
  header h1 {
    flex: 1;
    font-size: 1.2rem;
    margin: 0;
  }
  .back {
    color: var(--text-dim);
    text-decoration: none;
    font-size: 0.85rem;
  }
  .back:hover { color: var(--text); }
  .new {
    background: var(--accent);
    color: #fff;
    border: none;
    border-radius: 6px;
    padding: 0.4rem 0.8rem;
    font: inherit;
    cursor: pointer;
  }
  .new:disabled { opacity: 0.6; cursor: default; }
  .empty {
    color: var(--text-muted);
    padding: 2rem 1rem;
    text-align: center;
  }
  .list {
    list-style: none;
    margin: 0;
    padding: 0;
    display: flex;
    flex-direction: column;
    gap: 0.4rem;
  }
  .list a {
    display: flex;
    align-items: baseline;
    gap: 0.5rem;
    padding: 0.7rem 0.9rem;
    border: 1px solid var(--border-soft);
    border-radius: 8px;
    background: var(--bg-elev);
    color: var(--text);
    text-decoration: none;
  }
  .list a:hover { border-color: var(--accent); }
  .hash { color: var(--text-muted); }
  .name { font-weight: 600; }
  .desc {
    color: var(--text-dim);
    font-size: 0.85rem;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
</style>

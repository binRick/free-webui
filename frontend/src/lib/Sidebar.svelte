<script lang="ts">
  import { onMount } from 'svelte';
  import { goto } from '$app/navigation';
  import { page } from '$app/state';
  import { convs } from './conversations.svelte';
  import { deleteConversation } from './api';

  onMount(() => convs.refresh());

  async function del(id: string, e: Event) {
    e.preventDefault();
    e.stopPropagation();
    if (!confirm('delete this chat?')) return;
    await deleteConversation(id);
    await convs.refresh();
    if (page.params.id === id) goto('/');
  }
</script>

<aside>
  <header>
    <a href="/" class="brand">free-webui</a>
  </header>
  <a href="/" class="new" data-sveltekit-reload>+ new chat</a>
  <nav>
    {#each convs.list as c (c.id)}
      <a class="row" class:active={page.params.id === c.id} href="/chat/{c.id}">
        <span class="title">{c.title}</span>
        <button class="del" aria-label="delete" onclick={(e) => del(c.id, e)}>×</button>
      </a>
    {:else}
      <div class="empty">no chats yet</div>
    {/each}
  </nav>
</aside>

<style>
  aside {
    width: 240px;
    background: #07091a;
    border-right: 1px solid #1e293b;
    display: flex;
    flex-direction: column;
    overflow: hidden;
  }
  header {
    padding: 0.75rem 1rem;
    border-bottom: 1px solid #1e293b;
  }
  .brand {
    color: #22d3ee;
    font-family: ui-monospace, SFMono-Regular, monospace;
    font-weight: 600;
    text-decoration: none;
  }
  .new {
    display: block;
    margin: 0.75rem;
    padding: 0.5rem 0.75rem;
    background: #0f172a;
    color: #e2e8f0;
    border: 1px solid #334155;
    border-radius: 6px;
    text-align: center;
    text-decoration: none;
    font-size: 0.9rem;
  }
  .new:hover { background: #1e293b; }
  nav {
    flex: 1;
    overflow-y: auto;
    padding: 0 0.5rem 0.75rem;
  }
  .row {
    display: flex;
    align-items: center;
    gap: 0.25rem;
    padding: 0.45rem 0.5rem;
    border-radius: 6px;
    color: #cbd5e1;
    text-decoration: none;
    font-size: 0.9rem;
  }
  .row:hover { background: #0f172a; }
  .row.active { background: #1e293b; color: #fff; }
  .title {
    flex: 1;
    overflow: hidden;
    white-space: nowrap;
    text-overflow: ellipsis;
  }
  .del {
    background: transparent;
    border: 0;
    color: #64748b;
    cursor: pointer;
    font-size: 1rem;
    line-height: 1;
    padding: 0 0.35rem;
    opacity: 0;
    border-radius: 4px;
  }
  .row:hover .del,
  .row.active .del { opacity: 1; }
  .del:hover { color: #ef4444; background: rgba(239, 68, 68, 0.1); }
  .empty {
    color: #64748b;
    padding: 1rem;
    font-size: 0.85rem;
    text-align: center;
  }
</style>

<script lang="ts">
  import { onMount } from 'svelte';
  import { goto } from '$app/navigation';
  import { page } from '$app/state';
  import { auth } from './auth.svelte';
  import { convs } from './conversations.svelte';
  import { deleteConversation } from './api';
  import { sidebar } from './sidebarState.svelte';
  import { theme, type ThemeMode } from './theme.svelte';

  onMount(() => convs.refresh());

  async function del(id: string, e: Event) {
    e.preventDefault();
    e.stopPropagation();
    if (!confirm('delete this chat?')) return;
    await deleteConversation(id);
    await convs.refresh();
    if (page.params.id === id) goto('/');
  }

  function openChat() {
    sidebar.close();
  }

  const THEME_LABEL: Record<ThemeMode, string> = {
    system: '◐ system',
    light: '☼ light',
    dark: '☾ dark'
  };
</script>

<aside class:open={sidebar.open}>
  <header>
    <a href="/" class="brand" onclick={openChat}>free-webui</a>
    <button
      class="theme-toggle"
      aria-label="cycle theme"
      title="theme: {theme.mode}"
      onclick={() => theme.cycle()}
    >{THEME_LABEL[theme.mode]}</button>
  </header>
  <a href="/" class="new" data-sveltekit-reload onclick={openChat}>+ new chat</a>
  <nav>
    {#each convs.list as c (c.id)}
      <a
        class="row"
        class:active={page.params.id === c.id}
        href="/chat/{c.id}"
        onclick={openChat}
      >
        <span class="title">{c.title}</span>
        <button class="del" aria-label="delete" onclick={(e) => del(c.id, e)}>×</button>
      </a>
    {:else}
      <div class="empty">no chats yet</div>
    {/each}
  </nav>
  {#if auth.user}
    <footer>
      <span class="user" title={auth.user.role}>{auth.user.username}</span>
      <div class="footer-actions">
        <a class="admin-link" href="/account" title="api keys">🔑 api</a>
        {#if auth.user.role === 'admin'}
          <a class="admin-link" href="/admin/models" title="manage installed models">⚙ models</a>
        {/if}
        <button class="logout" onclick={() => auth.logout()}>log out</button>
      </div>
    </footer>
  {/if}
</aside>

<style>
  aside {
    width: 240px;
    background: var(--bg-sidebar);
    border-right: 1px solid var(--border-soft);
    display: flex;
    flex-direction: column;
    overflow: hidden;
    flex-shrink: 0;
  }
  header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 0.5rem;
    padding: 0.75rem 1rem;
    border-bottom: 1px solid var(--border-soft);
  }
  .brand {
    color: var(--accent);
    font-family: ui-monospace, SFMono-Regular, monospace;
    font-weight: 600;
    text-decoration: none;
  }
  .theme-toggle {
    background: var(--bg-elev);
    color: var(--text-dim);
    border: 1px solid var(--border-soft);
    border-radius: 4px;
    padding: 0.2rem 0.45rem;
    font-size: 0.7rem;
    font-family: inherit;
    cursor: pointer;
    white-space: nowrap;
  }
  .theme-toggle:hover { color: var(--text); background: var(--bg-hover); }
  .new {
    display: block;
    margin: 0.75rem;
    padding: 0.5rem 0.75rem;
    background: var(--bg-elev);
    color: var(--text);
    border: 1px solid var(--border);
    border-radius: 6px;
    text-align: center;
    text-decoration: none;
    font-size: 0.9rem;
  }
  .new:hover { background: var(--bg-hover); }
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
    color: var(--text-dim);
    text-decoration: none;
    font-size: 0.9rem;
  }
  .row:hover { background: var(--bg-elev); color: var(--text); }
  .row.active { background: var(--bg-hover); color: var(--text); }
  .title {
    flex: 1;
    overflow: hidden;
    white-space: nowrap;
    text-overflow: ellipsis;
  }
  .del {
    background: transparent;
    border: 0;
    color: var(--text-muted);
    cursor: pointer;
    font-size: 1rem;
    line-height: 1;
    padding: 0 0.35rem;
    opacity: 0;
    border-radius: 4px;
  }
  .row:hover .del,
  .row.active .del { opacity: 1; }
  .del:hover { color: var(--danger); background: color-mix(in srgb, var(--danger) 10%, transparent); }
  .empty {
    color: var(--text-muted);
    padding: 1rem;
    font-size: 0.85rem;
    text-align: center;
  }
  footer {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 0.5rem;
    padding: 0.6rem 0.75rem;
    border-top: 1px solid var(--border-soft);
    font-size: 0.8rem;
  }
  .user {
    color: var(--text-dim);
    overflow: hidden;
    white-space: nowrap;
    text-overflow: ellipsis;
  }
  .footer-actions { display: flex; gap: 0.35rem; align-items: center; }
  .admin-link {
    color: var(--text-muted);
    text-decoration: none;
    font-size: 0.72rem;
    padding: 0.2rem 0.5rem;
    border: 1px solid var(--border-soft);
    border-radius: 4px;
  }
  .admin-link:hover { color: var(--text); background: var(--bg-hover); }
  .logout {
    background: transparent;
    border: 1px solid var(--border-soft);
    color: var(--text-muted);
    padding: 0.2rem 0.5rem;
    border-radius: 4px;
    font: inherit;
    font-size: 0.72rem;
    cursor: pointer;
  }
  .logout:hover { color: var(--text); background: var(--bg-hover); }

  @media (max-width: 768px) {
    aside {
      position: fixed;
      top: 0;
      bottom: 0;
      left: 0;
      z-index: 20;
      transform: translateX(-100%);
      transition: transform 0.2s ease;
      box-shadow: 4px 0 24px rgba(0, 0, 0, 0.4);
    }
    aside.open { transform: translateX(0); }
  }
</style>

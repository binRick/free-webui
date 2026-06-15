<script lang="ts">
  import { onMount } from 'svelte';
  import { goto } from '$app/navigation';
  import { page } from '$app/state';
  import { auth } from './auth.svelte';
  import { convs } from './conversations.svelte';
  import { deleteConversation, renameConversation, type ConversationSummary } from './api';
  import { sidebar } from './sidebarState.svelte';
  import { theme, type ThemeMode } from './theme.svelte';

  onMount(() => convs.refresh());

  let query = $state('');
  let searchTimer: ReturnType<typeof setTimeout> | undefined;
  function onSearch() {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => convs.refresh(query), 200);
  }

  async function del(id: string, e: Event) {
    e.preventDefault();
    e.stopPropagation();
    if (!confirm('delete this chat?')) return;
    await deleteConversation(id);
    await convs.refresh(query);
    if (page.params.id === id) goto('/');
  }

  let renamingId = $state<string | null>(null);
  let renameText = $state('');
  function startRename(c: ConversationSummary, e: Event) {
    e.preventDefault();
    e.stopPropagation();
    renamingId = c.id;
    renameText = c.title;
  }
  function cancelRename() {
    renamingId = null;
  }
  async function commitRename(id: string) {
    if (renamingId !== id) return; // already committed/cancelled (e.g. blur after Enter)
    const t = renameText.trim();
    renamingId = null;
    if (t) {
      await renameConversation(id, t);
      await convs.refresh(query);
    }
  }
  function focusOnMount(node: HTMLInputElement) {
    node.focus();
    node.select();
  }

  // Group conversations (already sorted newest-first) into date buckets.
  const ORDER = ['Today', 'Yesterday', 'Previous 7 days', 'Previous 30 days', 'Older'];
  function bucketOf(ts: number): string {
    const start = new Date();
    start.setHours(0, 0, 0, 0);
    const today = start.getTime() / 1000;
    const day = 86400;
    if (ts >= today) return 'Today';
    if (ts >= today - day) return 'Yesterday';
    if (ts >= today - 7 * day) return 'Previous 7 days';
    if (ts >= today - 30 * day) return 'Previous 30 days';
    return 'Older';
  }
  const grouped = $derived.by(() => {
    const map = new Map<string, ConversationSummary[]>();
    for (const c of convs.list) {
      const b = bucketOf(c.updated_at);
      const arr = map.get(b);
      if (arr) arr.push(c);
      else map.set(b, [c]);
    }
    return ORDER.filter((b) => map.has(b)).map((b) => ({ label: b, items: map.get(b)! }));
  });

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
  <div class="search">
    <input
      type="search"
      placeholder="search chats…"
      bind:value={query}
      oninput={onSearch}
      aria-label="search conversations"
    />
  </div>
  <nav>
    {#each grouped as group (group.label)}
      <div class="group-label">{group.label}</div>
      {#each group.items as c (c.id)}
        <div class="row" class:active={page.params.id === c.id}>
          {#if renamingId === c.id}
            <input
              class="rename"
              bind:value={renameText}
              use:focusOnMount
              onkeydown={(e) => {
                if (e.key === 'Enter') commitRename(c.id);
                else if (e.key === 'Escape') cancelRename();
              }}
              onblur={() => commitRename(c.id)}
              aria-label="rename conversation"
            />
          {:else}
            <a class="link" href="/chat/{c.id}" onclick={openChat} title={c.title}>{c.title}</a>
            <button class="act" aria-label="rename chat" title="rename" onclick={(e) => startRename(c, e)}>✎</button>
            <button class="act del" aria-label="delete chat" title="delete" onclick={(e) => del(c.id, e)}>×</button>
          {/if}
        </div>
      {/each}
    {:else}
      <div class="empty">{query.trim() ? 'no matches' : 'no chats yet'}</div>
    {/each}
  </nav>
  {#if auth.user}
    <footer>
      <span class="user" title={auth.user.role}>{auth.user.username}</span>
      <div class="footer-actions">
        <a class="admin-link" href="/account" title="api keys">🔑 api</a>
        {#if auth.user.role === 'admin'}
          <a class="admin-link" href="/admin/users" title="manage users">👥 users</a>
          <a class="admin-link" href="/admin/access" title="groups & model access">🔒 access</a>
          <a class="admin-link" href="/admin/models" title="manage installed models">⚙ models</a>
          <a class="admin-link" href="/admin/connections" title="upstream connections">🔌 conns</a>
          <a class="admin-link" href="/admin/plugins" title="loaded plugins">🧩 plugins</a>
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
    margin: 0.75rem 0.75rem 0.5rem;
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
  .search {
    padding: 0 0.75rem 0.5rem;
  }
  .search input {
    width: 100%;
    box-sizing: border-box;
    background: var(--bg-elev);
    color: var(--text);
    border: 1px solid var(--border-soft);
    border-radius: 6px;
    padding: 0.4rem 0.6rem;
    font: inherit;
    font-size: 0.85rem;
  }
  .search input::placeholder { color: var(--text-muted); }
  .search input:focus { outline: none; border-color: var(--accent); }
  nav {
    flex: 1;
    overflow-y: auto;
    padding: 0 0.5rem 0.75rem;
  }
  .group-label {
    color: var(--text-muted);
    font-size: 0.68rem;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    padding: 0.6rem 0.5rem 0.25rem;
  }
  .row {
    display: flex;
    align-items: center;
    gap: 0.25rem;
    padding: 0.45rem 0.5rem;
    border-radius: 6px;
    color: var(--text-dim);
    font-size: 0.9rem;
  }
  .row:hover { background: var(--bg-elev); color: var(--text); }
  .row.active { background: var(--bg-hover); color: var(--text); }
  .link {
    flex: 1;
    min-width: 0;
    overflow: hidden;
    white-space: nowrap;
    text-overflow: ellipsis;
    color: inherit;
    text-decoration: none;
  }
  .rename {
    flex: 1;
    min-width: 0;
    background: var(--bg);
    color: var(--text);
    border: 1px solid var(--accent);
    border-radius: 4px;
    padding: 0.15rem 0.35rem;
    font: inherit;
    font-size: 0.85rem;
  }
  .rename:focus { outline: none; }
  .act {
    background: transparent;
    border: 0;
    color: var(--text-muted);
    cursor: pointer;
    font-size: 0.85rem;
    line-height: 1;
    padding: 0 0.3rem;
    opacity: 0;
    border-radius: 4px;
  }
  .act.del { font-size: 1rem; }
  .row:hover .act,
  .row.active .act { opacity: 1; }
  .act:hover { color: var(--text); background: var(--bg-hover); }
  .act.del:hover { color: var(--danger); background: color-mix(in srgb, var(--danger) 10%, transparent); }
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

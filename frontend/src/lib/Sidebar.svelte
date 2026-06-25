<script lang="ts">
  import { onMount } from 'svelte';
  import { goto } from '$app/navigation';
  import { page } from '$app/state';
  import { appConfig } from './appConfig.svelte';
  import { auth } from './auth.svelte';
  import { convs } from './conversations.svelte';
  import {
    cloneConversation,
    createFolder,
    deleteConversation,
    deleteFolder,
    importConversation,
    listFolders,
    renameConversation,
    renameFolder,
    setArchived,
    setPinned,
    type ConversationSummary,
    type Folder
  } from './api';
  import { i18n, LOCALES, t } from './i18n.svelte';
  import { sidebar } from './sidebarState.svelte';
  import { theme, type ThemeMode } from './theme.svelte';
  import { toasts } from './toastStore.svelte';

  let importInput: HTMLInputElement;

  async function onImport(e: Event) {
    const target = e.target as HTMLInputElement;
    const file = target.files?.[0];
    target.value = '';
    if (!file) return;
    try {
      const data = JSON.parse(await file.text());
      const created = await importConversation(data);
      convs.refresh();
      sidebar.close();
      goto(`/chat/${created.id}`);
    } catch (err) {
      toasts.error((err as Error).message || 'could not import that file');
    }
  }

  // Map the (English) date-bucket / pinned labels to translation keys.
  const GROUP_KEYS: Record<string, string> = {
    Today: 'dateGroup.today',
    Yesterday: 'dateGroup.yesterday',
    'Previous 7 days': 'dateGroup.previous7',
    'Previous 30 days': 'dateGroup.previous30',
    Older: 'dateGroup.older',
    '📌 Pinned': 'sidebar.pinned'
  };
  function groupLabel(label: string): string {
    return GROUP_KEYS[label] ? t(GROUP_KEYS[label]) : label;
  }

  onMount(async () => {
    await convs.refresh();
    folders = await listFolders();
  });

  let query = $state('');
  let showArchived = $state(false);
  let tagFilter = $state<string | null>(null);
  let folders = $state<Folder[]>([]);
  let folderFilter = $state<number | null>(null);
  let searchTimer: ReturnType<typeof setTimeout> | undefined;
  function refreshList() {
    convs.refresh(query, showArchived, tagFilter ?? undefined, folderFilter);
  }
  async function refreshFolders() {
    folders = await listFolders();
  }
  function selectFolder(id: number | null) {
    folderFilter = id;
    refreshList();
  }
  async function newFolder() {
    const name = window.prompt('folder name')?.trim();
    if (!name) return;
    const f = await createFolder(name);
    await refreshFolders();
    selectFolder(f.id);
  }
  async function editFolder(f: Folder, e: Event) {
    e.preventDefault();
    e.stopPropagation();
    const name = window.prompt('rename folder', f.name)?.trim();
    if (!name || name === f.name) return;
    await renameFolder(f.id, name);
    await refreshFolders();
  }
  async function removeFolder(f: Folder, e: Event) {
    e.preventDefault();
    e.stopPropagation();
    if (!confirm(`delete folder "${f.name}"? chats inside are kept (just un-filed).`)) return;
    await deleteFolder(f.id);
    if (folderFilter === f.id) folderFilter = null;
    await refreshFolders();
    refreshList();
  }
  function onSearch() {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(refreshList, 200);
  }
  function toggleArchivedView() {
    showArchived = !showArchived;
    refreshList();
  }
  function selectTag(t: string | null) {
    tagFilter = t;
    refreshList();
  }
  const allTags = $derived.by(() => {
    const set = new Set<string>();
    for (const c of convs.list) for (const t of c.tags) set.add(t);
    return [...set].sort();
  });

  async function pin(c: ConversationSummary, e: Event) {
    e.preventDefault();
    e.stopPropagation();
    await setPinned(c.id, !c.pinned);
    refreshList();
  }
  async function archive(c: ConversationSummary, e: Event) {
    e.preventDefault();
    e.stopPropagation();
    await setArchived(c.id, !c.archived);
    refreshList();
  }

  async function del(id: string, e: Event) {
    e.preventDefault();
    e.stopPropagation();
    if (!confirm('delete this chat?')) return;
    await deleteConversation(id);
    refreshList();
    if (page.params.id === id) goto('/');
  }

  async function clone(c: ConversationSummary, e: Event) {
    e.preventDefault();
    e.stopPropagation();
    const created = await cloneConversation(c.id);
    refreshList();
    goto(`/chat/${created.id}`);
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
      refreshList();
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
    const pinned = convs.list.filter((c) => c.pinned);
    const map = new Map<string, ConversationSummary[]>();
    for (const c of convs.list) {
      if (c.pinned) continue;
      const b = bucketOf(c.updated_at);
      const arr = map.get(b);
      if (arr) arr.push(c);
      else map.set(b, [c]);
    }
    const groups = ORDER.filter((b) => map.has(b)).map((b) => ({ label: b, items: map.get(b)! }));
    if (pinned.length) groups.unshift({ label: '📌 Pinned', items: pinned });
    return groups;
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
    <a href="/" class="brand" onclick={openChat}>{appConfig.instanceName}</a>
    <select
      class="lang"
      aria-label={t('settings.language')}
      title={t('settings.language')}
      value={i18n.locale}
      onchange={(e) => i18n.set(e.currentTarget.value)}
    >
      {#each LOCALES as l (l.code)}
        <option value={l.code}>{l.code.toUpperCase()}</option>
      {/each}
    </select>
    <button
      class="theme-toggle"
      aria-label="cycle theme"
      title="theme: {theme.mode}"
      onclick={() => theme.cycle()}
    >{THEME_LABEL[theme.mode]}</button>
  </header>
  <a href="/" class="new" data-sveltekit-reload onclick={openChat}>{t('sidebar.newChat')}</a>
  <input
    bind:this={importInput}
    type="file"
    accept="application/json,.json"
    hidden
    onchange={onImport}
  />
  <button class="temp-link import-btn" onclick={() => importInput.click()}>{t('sidebar.import')}</button>
  <a href="/temporary" class="temp-link" onclick={openChat}>{t('sidebar.temporaryChat')}</a>
  <a href="/compare" class="temp-link" onclick={openChat}>{t('sidebar.compareModels')}</a>
  <a href="/arena" class="temp-link" onclick={openChat}>{t('sidebar.arena')}</a>
  <a href="/call" class="temp-link" onclick={openChat}>{t('sidebar.call')}</a>
  <div class="search">
    <input
      type="search"
      placeholder={t('sidebar.searchPlaceholder')}
      bind:value={query}
      oninput={onSearch}
      aria-label="search conversations"
    />
  </div>
  <div class="folder-bar">
    <button class="folder-chip" class:on={folderFilter === null} onclick={() => selectFolder(null)}>{t('sidebar.allTags')}</button>
    {#each folders as f (f.id)}
      <span class="folder-group" class:on={folderFilter === f.id}>
        <button class="folder-chip" class:on={folderFilter === f.id} onclick={() => selectFolder(f.id)} title={f.name}>📁 {f.name}</button>
        {#if folderFilter === f.id}
          <button class="folder-act" aria-label="rename folder" title="rename" onclick={(e) => editFolder(f, e)}>✎</button>
          <button class="folder-act" aria-label="delete folder" title="delete" onclick={(e) => removeFolder(f, e)}>×</button>
        {/if}
      </span>
    {/each}
    <button class="folder-chip add" onclick={newFolder} title="new folder">＋</button>
  </div>
  {#if tagFilter}
    <div class="tag-bar">
      <button class="tag-chip on" onclick={() => selectTag(null)} title="clear tag filter">🏷 {tagFilter} ✕</button>
    </div>
  {:else if allTags.length}
    <div class="tag-bar">
      {#each allTags as tag (tag)}
        <button class="tag-chip" onclick={() => selectTag(tag)}>{tag}</button>
      {/each}
    </div>
  {/if}
  <nav>
    {#each grouped as group (group.label)}
      <div class="group-label">{groupLabel(group.label)}</div>
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
            <button class="act" class:on={c.pinned} aria-label="pin chat" title={c.pinned ? 'unpin' : 'pin'} onclick={(e) => pin(c, e)}>📌</button>
            <button class="act" aria-label="rename chat" title="rename" onclick={(e) => startRename(c, e)}>✎</button>
            <button class="act" aria-label="clone chat" title="clone" onclick={(e) => clone(c, e)}>⎘</button>
            <button class="act" aria-label={c.archived ? 'unarchive' : 'archive'} title={c.archived ? 'unarchive' : 'archive'} onclick={(e) => archive(c, e)}>🗄</button>
            <button class="act del" aria-label="delete chat" title="delete" onclick={(e) => del(c.id, e)}>×</button>
          {/if}
        </div>
      {/each}
    {:else}
      <div class="empty">{query.trim() ? t('sidebar.noMatches') : showArchived ? t('sidebar.noArchived') : t('sidebar.noChats')}</div>
    {/each}
    <button class="archived-toggle" onclick={toggleArchivedView}>
      {showArchived ? t('sidebar.backToChats') : t('sidebar.archived')}
    </button>
  </nav>
  {#if auth.user}
    <footer>
      <span class="user" title={auth.user.role}>{auth.user.username}</span>
      <div class="footer-actions">
        <a class="admin-link" href="/account" title="api keys">{t('nav.apiKeys')}</a>
        <a class="admin-link" href="/channels" title="real-time channels">{t('nav.channels')}</a>
        <a class="admin-link" href="/notes" title="notes">{t('nav.notes')}</a>
        <a class="admin-link" href="/collections" title="knowledge bases">{t('nav.knowledge')}</a>
        <a class="admin-link" href="/evaluations" title="model leaderboard">{t('nav.leaderboard')}</a>
        {#if auth.user.role === 'admin'}
          <a class="admin-link" href="/admin/analytics" title="usage analytics">📊 analytics</a>
          <a class="admin-link" href="/admin/banners" title="broadcast banners">📢 banners</a>
          <a class="admin-link" href="/admin/users" title="manage users">👥 users</a>
          <a class="admin-link" href="/admin/access" title="groups & model access">🔒 access</a>
          <a class="admin-link" href="/admin/permissions" title="per-feature permissions">🎛 perms</a>
          <a class="admin-link" href="/admin/audit" title="admin audit log">📜 audit</a>
          <a class="admin-link" href="/admin/feedback" title="message feedback">💬 feedback</a>
          <a class="admin-link" href="/admin/models" title="manage installed models">⚙ models</a>
          <a class="admin-link" href="/admin/connections" title="upstream connections">🔌 conns</a>
          <a class="admin-link" href="/admin/plugins" title="loaded plugins">🧩 plugins</a>
          <a class="admin-link" href="/admin/appearance" title="branding & custom CSS">🎨 appearance</a>
        {/if}
        <button class="logout" onclick={() => auth.logout()}>{t('common.logout')}</button>
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
    flex: 1;
  }
  .lang {
    background: var(--bg-elev);
    color: var(--text-dim);
    border: 1px solid var(--border-soft);
    border-radius: 4px;
    padding: 0.15rem 0.25rem;
    font: inherit;
    font-size: 0.7rem;
    cursor: pointer;
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
  .temp-link {
    display: block;
    margin: 0 0.75rem 0.5rem;
    padding: 0.3rem 0.75rem;
    color: var(--text-dim);
    text-decoration: none;
    font-size: 0.8rem;
    text-align: center;
  }
  .temp-link:hover { color: var(--text); }
  /* the import control is a <button> styled like the temp-links */
  .import-btn {
    width: calc(100% - 1.5rem);
    background: none;
    border: 0;
    cursor: pointer;
    font: inherit;
    font-size: 0.8rem;
  }
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
  .tag-bar {
    display: flex;
    flex-wrap: wrap;
    gap: 0.25rem;
    padding: 0 0.75rem 0.5rem;
  }
  .tag-chip {
    background: var(--bg-elev);
    color: var(--text-dim);
    border: 1px solid var(--border-soft);
    border-radius: 999px;
    padding: 0.1rem 0.5rem;
    font: inherit;
    font-size: 0.72rem;
    cursor: pointer;
  }
  .tag-chip:hover { color: var(--text); border-color: var(--accent); }
  .tag-chip.on { color: var(--accent); border-color: var(--accent); }
  .folder-bar {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 0.25rem;
    padding: 0.5rem 0.75rem 0.25rem;
  }
  .folder-group {
    display: inline-flex;
    align-items: center;
    gap: 0.15rem;
  }
  .folder-chip {
    background: var(--bg-elev);
    color: var(--text-dim);
    border: 1px solid var(--border-soft);
    border-radius: 999px;
    padding: 0.1rem 0.5rem;
    font: inherit;
    font-size: 0.72rem;
    cursor: pointer;
    max-width: 130px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .folder-chip:hover { color: var(--text); border-color: var(--accent); }
  .folder-chip.on { color: var(--accent); border-color: var(--accent); }
  .folder-chip.add { font-size: 0.85rem; line-height: 1; padding: 0.1rem 0.45rem; }
  .folder-act {
    background: transparent;
    border: none;
    color: var(--text-dim);
    cursor: pointer;
    font-size: 0.72rem;
    padding: 0 0.1rem;
    line-height: 1;
  }
  .folder-act:hover { color: var(--accent); }
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
  .act.on { opacity: 1; }
  .archived-toggle {
    width: 100%;
    margin-top: 0.5rem;
    background: transparent;
    border: 0;
    color: var(--text-muted);
    font: inherit;
    font-size: 0.78rem;
    text-align: left;
    padding: 0.4rem 0.5rem;
    cursor: pointer;
    border-radius: 6px;
  }
  .archived-toggle:hover { background: var(--bg-elev); color: var(--text); }
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
    flex-direction: column;
    align-items: stretch;
    gap: 0.4rem;
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
  /* wrap the nav chips onto multiple rows instead of clipping them off the
     right edge; cap the height + scroll so a long admin list can't dominate. */
  .footer-actions {
    display: flex;
    flex-wrap: wrap;
    gap: 0.35rem;
    align-items: center;
    max-height: 38vh;
    overflow-y: auto;
  }
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

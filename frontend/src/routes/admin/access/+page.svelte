<script lang="ts">
  import { onMount } from 'svelte';
  import { goto } from '$app/navigation';
  import { auth } from '$lib/auth.svelte';
  import {
    adminListUsers,
    createGroup,
    deleteGroup,
    getGroupMembers,
    listGroups,
    listModelAccess,
    listModels,
    setGroupMembers,
    setModelAccess,
    type AdminUser,
    type Group,
    type ModelAccessEntry
  } from '$lib/api';

  let users = $state<AdminUser[]>([]);
  let groups = $state<Group[]>([]);
  let models = $state<string[]>([]);
  let access = $state<Record<string, ModelAccessEntry>>({});
  let loadError = $state<string | null>(null);

  let newGroup = $state('');

  let editGroupId = $state<number | null>(null);
  let memberDraft = $state<Set<number>>(new Set());

  let editModel = $state<string | null>(null);
  let groupDraft = $state<Set<number>>(new Set());
  let userDraft = $state<Set<number>>(new Set());
  let saving = $state(false);

  // Include models that have grant rows but are no longer reported by the
  // upstream, so orphaned restrictions stay visible and editable.
  const allModels = $derived(
    [...new Set([...models, ...Object.keys(access)])].sort()
  );

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
      [users, groups, models, access] = await Promise.all([
        adminListUsers(),
        listGroups(),
        listModels(),
        listModelAccess()
      ]);
      loadError = null;
    } catch (e) {
      loadError = (e as Error).message;
    }
  }

  function userName(id: number): string {
    return users.find((u) => u.id === id)?.username ?? `#${id}`;
  }
  function groupName(id: number): string {
    return groups.find((g) => g.id === id)?.name ?? `#${id}`;
  }

  async function addGroup(e: SubmitEvent) {
    e.preventDefault();
    const name = newGroup.trim();
    if (!name) return;
    try {
      await createGroup(name);
      newGroup = '';
      await refresh();
    } catch (err) {
      loadError = (err as Error).message;
    }
  }

  async function removeGroup(id: number) {
    if (!confirm('delete this group?') || saving) return;
    saving = true;
    try {
      await deleteGroup(id);
      if (editGroupId === id) editGroupId = null;
      await refresh();
    } catch (err) {
      loadError = (err as Error).message;
    } finally {
      saving = false;
    }
  }

  async function openMembers(g: Group) {
    editModel = null;
    editGroupId = g.id;
    memberDraft = new Set(await getGroupMembers(g.id));
  }
  function toggleMember(uid: number) {
    const next = new Set(memberDraft);
    if (next.has(uid)) next.delete(uid);
    else next.add(uid);
    memberDraft = next;
  }
  async function saveMembers() {
    if (editGroupId == null || saving) return;
    saving = true;
    try {
      await setGroupMembers(editGroupId, [...memberDraft]);
      editGroupId = null;
      await refresh();
    } catch (err) {
      loadError = (err as Error).message;
    } finally {
      saving = false;
    }
  }

  function isPublic(model: string): boolean {
    const a = access[model];
    return !a || (a.group_ids.length === 0 && a.user_ids.length === 0);
  }
  function openAccess(model: string) {
    editGroupId = null;
    editModel = model;
    const a = access[model] ?? { group_ids: [], user_ids: [] };
    groupDraft = new Set(a.group_ids);
    userDraft = new Set(a.user_ids);
  }
  function toggleGroup(id: number) {
    const next = new Set(groupDraft);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    groupDraft = next;
  }
  function toggleUser(id: number) {
    const next = new Set(userDraft);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    userDraft = next;
  }
  async function saveAccess() {
    if (editModel == null || saving) return;
    saving = true;
    try {
      await setModelAccess(editModel, [...groupDraft], [...userDraft]);
      editModel = null;
      await refresh();
    } catch (err) {
      loadError = (err as Error).message;
    } finally {
      saving = false;
    }
  }
</script>

<header>
  <a class="back" href="/">← chat</a>
  <h1>user access</h1>
  <a class="tabs" href="/admin/users">users →</a>
</header>

<main>
  {#if loadError}<p class="error">{loadError}</p>{/if}

  <section class="card">
    <h2>groups</h2>
    <form class="row" onsubmit={addGroup}>
      <input placeholder="new group name" bind:value={newGroup} maxlength="80" />
      <button type="submit" disabled={!newGroup.trim()}>+ create</button>
    </form>

    {#each groups as g (g.id)}
      <div class="item">
        <span class="name">{g.name}</span>
        <span class="meta">{g.member_count} member{g.member_count === 1 ? '' : 's'}</span>
        <button class="small" onclick={() => openMembers(g)}>members</button>
        <button class="small del" onclick={() => removeGroup(g.id)}>delete</button>
      </div>
      {#if editGroupId === g.id}
        <div class="editor">
          {#each users as u (u.id)}
            <label class="check">
              <input type="checkbox" checked={memberDraft.has(u.id)} onchange={() => toggleMember(u.id)} />
              {u.username}<span class="muted"> · {u.role}</span>
            </label>
          {/each}
          <div class="editor-actions">
            <button class="small" onclick={() => (editGroupId = null)}>cancel</button>
            <button class="small primary" onclick={saveMembers} disabled={saving}>save members</button>
          </div>
        </div>
      {/if}
    {:else}
      <p class="empty">no groups yet</p>
    {/each}
  </section>

  <section class="card">
    <h2>model access</h2>
    <p class="hint">A model is visible to everyone unless you restrict it. Restricted models are shown only to the selected groups/users (and admins).</p>
    {#each allModels as m (m)}
      <div class="item">
        <span class="name mono">{m}</span>
        {#if isPublic(m)}
          <span class="badge public">public</span>
        {:else}
          <span class="badge restricted">
            restricted{#if (access[m]?.group_ids.length)} · {access[m].group_ids.map(groupName).join(', ')}{/if}{#if (access[m]?.user_ids.length)} · {access[m].user_ids.map(userName).join(', ')}{/if}
          </span>
        {/if}
        <button class="small" onclick={() => openAccess(m)}>edit</button>
      </div>
      {#if editModel === m}
        <div class="editor">
          <div class="editor-label">groups</div>
          {#if groups.length}
            {#each groups as g (g.id)}
              <label class="check">
                <input type="checkbox" checked={groupDraft.has(g.id)} onchange={() => toggleGroup(g.id)} />
                {g.name}
              </label>
            {/each}
          {:else}
            <p class="muted">no groups defined</p>
          {/if}
          <div class="editor-label">users</div>
          {#each users as u (u.id)}
            <label class="check">
              <input type="checkbox" checked={userDraft.has(u.id)} onchange={() => toggleUser(u.id)} />
              {u.username}<span class="muted"> · {u.role}</span>
            </label>
          {/each}
          <div class="editor-actions">
            <button class="small" onclick={() => (editModel = null)}>cancel</button>
            <button class="small primary" onclick={saveAccess} disabled={saving}>save access</button>
          </div>
        </div>
      {/if}
    {:else}
      <p class="empty">no models reported by the upstream</p>
    {/each}
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
  main { max-width: 760px; margin: 0 auto; padding: 1.25rem; display: flex; flex-direction: column; gap: 1.25rem; }
  .card {
    border: 1px solid var(--border-soft);
    border-radius: 8px;
    padding: 1rem 1.25rem;
    background: var(--bg-elev);
  }
  .card h2 { margin: 0 0 0.75rem; font-size: 0.95rem; }
  .hint { color: var(--text-dim); font-size: 0.82rem; margin: 0 0 0.75rem; }
  .row { display: flex; gap: 0.5rem; margin-bottom: 0.75rem; }
  .row input {
    flex: 1;
    background: var(--bg);
    color: var(--text);
    border: 1px solid var(--border-soft);
    border-radius: 6px;
    padding: 0.4rem 0.6rem;
    font: inherit;
    font-size: 0.85rem;
  }
  button {
    background: var(--bg);
    color: var(--text);
    border: 1px solid var(--border);
    border-radius: 6px;
    padding: 0.4rem 0.7rem;
    font: inherit;
    font-size: 0.82rem;
    cursor: pointer;
  }
  button:hover:not(:disabled) { background: var(--bg-hover); }
  button:disabled { opacity: 0.5; cursor: default; }
  button.small { padding: 0.25rem 0.55rem; font-size: 0.76rem; }
  button.primary { border-color: var(--accent); color: var(--accent); }
  button.del:hover { color: var(--danger); border-color: var(--danger); }
  .item {
    display: flex;
    align-items: center;
    gap: 0.6rem;
    padding: 0.45rem 0;
    border-top: 1px solid var(--border-soft);
  }
  .item .name { font-weight: 500; }
  .item .name.mono { font-family: ui-monospace, monospace; font-weight: 400; font-size: 0.85rem; }
  .meta { color: var(--text-muted); font-size: 0.78rem; }
  .item button { margin-left: auto; }
  .item button + button { margin-left: 0.4rem; }
  .badge { font-size: 0.72rem; padding: 0.1rem 0.45rem; border-radius: 999px; }
  .badge.public { color: var(--text-muted); border: 1px solid var(--border-soft); }
  .badge.restricted { color: var(--accent); border: 1px solid color-mix(in srgb, var(--accent) 40%, transparent); }
  .editor {
    padding: 0.5rem 0 0.75rem 0.5rem;
    display: flex;
    flex-direction: column;
    gap: 0.25rem;
  }
  .editor-label { color: var(--text-muted); font-size: 0.72rem; text-transform: uppercase; letter-spacing: 0.04em; margin-top: 0.35rem; }
  .check { display: flex; align-items: center; gap: 0.45rem; font-size: 0.85rem; }
  .muted { color: var(--text-muted); }
  .editor-actions { display: flex; gap: 0.5rem; margin-top: 0.5rem; }
  .empty { color: var(--text-muted); font-size: 0.85rem; padding: 0.5rem 0 0; }
  .error { color: var(--danger); font-size: 0.85rem; }
</style>

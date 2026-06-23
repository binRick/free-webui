<script lang="ts">
  import { onMount } from 'svelte';
  import { goto } from '$app/navigation';
  import { auth } from '$lib/auth.svelte';
  import {
    adminCreateUser,
    adminDeleteUser,
    adminListUsers,
    adminPatchUser,
    type AdminUser
  } from '$lib/api';

  let users = $state<AdminUser[]>([]);
  let loadError = $state<string | null>(null);

  let newUsername = $state('');
  let newPassword = $state('');
  let newRole = $state<'admin' | 'user'>('user');
  let createBusy = $state(false);
  let createError = $state<string | null>(null);

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
      users = await adminListUsers();
      loadError = null;
    } catch (e) {
      loadError = (e as Error).message;
    }
  }

  async function doCreate(e: SubmitEvent) {
    e.preventDefault();
    if (createBusy) return;
    createBusy = true;
    createError = null;
    try {
      await adminCreateUser(newUsername.trim(), newPassword, newRole);
      newUsername = '';
      newPassword = '';
      newRole = 'user';
      await refresh();
    } catch (err) {
      createError = (err as Error).message;
    } finally {
      createBusy = false;
    }
  }

  async function toggleRole(u: AdminUser) {
    const next = u.role === 'admin' ? 'user' : 'admin';
    try {
      await adminPatchUser(u.id, { role: next });
      await refresh();
    } catch (e) {
      loadError = (e as Error).message;
    }
  }

  async function toggleDisabled(u: AdminUser) {
    if (
      !u.disabled &&
      !confirm(`disable ${u.username}? they'll be signed out and can't log in until re-enabled. their data is kept.`)
    )
      return;
    try {
      await adminPatchUser(u.id, { disabled: !u.disabled });
      await refresh();
    } catch (e) {
      loadError = (e as Error).message;
    }
  }

  async function resetPassword(u: AdminUser) {
    const pw = window.prompt(`new password for ${u.username}? (min 6 chars)`);
    if (!pw || pw.length < 6) return;
    try {
      await adminPatchUser(u.id, { password: pw });
      alert(`password updated for ${u.username}`);
    } catch (e) {
      loadError = (e as Error).message;
    }
  }

  async function remove(u: AdminUser) {
    if (!confirm(`delete ${u.username}? all their conversations will be wiped too.`)) return;
    try {
      await adminDeleteUser(u.id);
      await refresh();
    } catch (e) {
      loadError = (e as Error).message;
    }
  }

  function fmtDate(ts: number): string {
    return new Date(ts * 1000).toISOString().replace('T', ' ').slice(0, 19);
  }
</script>

<header>
  <a class="back" href="/">← back to chat</a>
  <h1>users</h1>
</header>

<main>
  <section class="card">
    <h2>create a user</h2>
    <form onsubmit={doCreate}>
      <input bind:value={newUsername} placeholder="username" required />
      <input
        bind:value={newPassword}
        placeholder="password (≥ 6 chars)"
        type="password"
        required
        minlength="6"
      />
      <select bind:value={newRole}>
        <option value="user">user</option>
        <option value="admin">admin</option>
      </select>
      <button type="submit" disabled={createBusy || !newUsername.trim() || newPassword.length < 6}>
        {createBusy ? 'creating…' : 'create'}
      </button>
    </form>
    {#if createError}<div class="err">{createError}</div>{/if}
  </section>

  <section class="card">
    <h2>all users</h2>
    {#if loadError}<div class="err">{loadError}</div>{/if}
    <table>
      <thead>
        <tr><th>username</th><th>role</th><th>created</th><th></th></tr>
      </thead>
      <tbody>
        {#each users as u (u.id)}
          <tr class:disabled-row={u.disabled}>
            <td class="uname">
              {u.username}{u.id === auth.user?.id ? ' (you)' : ''}
              {#if u.disabled}<span class="dis-pill">disabled</span>{/if}
            </td>
            <td>
              <span class="role-pill" class:admin={u.role === 'admin'}>{u.role}</span>
            </td>
            <td class="ts">{fmtDate(u.created_at)}</td>
            <td class="actions">
              <button onclick={() => toggleRole(u)}>{u.role === 'admin' ? '↓ user' : '↑ admin'}</button>
              <button onclick={() => resetPassword(u)}>reset pw</button>
              <button
                class:enable={u.disabled}
                disabled={u.id === auth.user?.id}
                onclick={() => toggleDisabled(u)}
              >{u.disabled ? 'enable' : 'disable'}</button>
              <button class="del" disabled={u.id === auth.user?.id} onclick={() => remove(u)}>delete</button>
            </td>
          </tr>
        {/each}
      </tbody>
    </table>
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
  h1 { margin: 0; font-size: 1.05rem; font-weight: 500; color: var(--text); }

  main {
    flex: 1;
    overflow-y: auto;
    padding: 1.25rem;
    max-width: 960px;
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
  form { display: flex; gap: 0.5rem; flex-wrap: wrap; }
  form input, form select {
    flex: 1 1 140px;
    background: var(--bg);
    color: var(--text);
    border: 1px solid var(--border);
    border-radius: 6px;
    padding: 0.5rem 0.75rem;
    font: inherit;
  }
  form button {
    background: var(--bg-hover);
    color: var(--text);
    border: 1px solid var(--border);
    border-radius: 6px;
    padding: 0.5rem 1rem;
    font: inherit;
    cursor: pointer;
  }
  form button:disabled { opacity: 0.5; cursor: not-allowed; }
  table { width: 100%; border-collapse: collapse; }
  th, td { padding: 0.55rem 0.75rem; border-bottom: 1px solid var(--border-soft); font-size: 0.9rem; text-align: left; }
  th { font-size: 0.72rem; color: var(--text-muted); text-transform: uppercase; font-weight: 500; }
  .uname { font-family: ui-monospace, monospace; color: var(--accent); }
  .ts { color: var(--text-muted); font-size: 0.78rem; font-family: ui-monospace, monospace; }
  .role-pill {
    display: inline-block;
    padding: 0.1rem 0.5rem;
    border-radius: 999px;
    font-size: 0.7rem;
    background: var(--bg-hover);
    color: var(--text-dim);
    border: 1px solid var(--border-soft);
  }
  .role-pill.admin {
    background: color-mix(in srgb, var(--accent) 18%, transparent);
    color: var(--accent);
    border-color: color-mix(in srgb, var(--accent) 40%, transparent);
  }
  td.actions {
    text-align: right;
    display: flex;
    gap: 0.35rem;
    justify-content: flex-end;
  }
  td.actions button {
    background: transparent;
    border: 1px solid var(--border);
    color: var(--text-dim);
    padding: 0.2rem 0.55rem;
    font: inherit;
    font-size: 0.75rem;
    border-radius: 4px;
    cursor: pointer;
  }
  td.actions button:hover { color: var(--text); background: var(--bg-hover); }
  td.actions .del:hover { color: var(--danger); border-color: var(--danger); }
  td.actions .enable { color: var(--accent); border-color: color-mix(in srgb, var(--accent) 40%, transparent); }
  td.actions button:disabled { opacity: 0.4; cursor: not-allowed; }
  tr.disabled-row .uname { opacity: 0.6; }
  .dis-pill {
    margin-left: 0.4rem;
    padding: 0.05rem 0.4rem;
    border-radius: 999px;
    font-size: 0.62rem;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    background: color-mix(in srgb, var(--danger) 16%, transparent);
    color: var(--danger);
    border: 1px solid color-mix(in srgb, var(--danger) 40%, transparent);
    vertical-align: middle;
  }
  .err {
    color: var(--danger);
    background: color-mix(in srgb, var(--danger) 12%, transparent);
    padding: 0.5rem 0.75rem;
    border-radius: 6px;
    font-size: 0.85rem;
  }
</style>

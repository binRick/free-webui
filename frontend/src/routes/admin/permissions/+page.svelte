<script lang="ts">
  import { onMount } from 'svelte';
  import { goto } from '$app/navigation';
  import { auth } from '$lib/auth.svelte';
  import {
    getPermissionMatrix,
    setGroupPermissions,
    setPermissionDefaults,
    type PermissionMatrix
  } from '$lib/api';

  let matrix = $state<PermissionMatrix | null>(null);
  let loadError = $state<string | null>(null);
  let saving = $state(false);
  let saved = $state(false);

  // editable drafts
  let defaults = $state<Record<string, boolean>>({});
  let groupKeys = $state<Record<number, Set<string>>>({});

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
      matrix = await getPermissionMatrix();
      defaults = { ...matrix.defaults };
      groupKeys = Object.fromEntries(matrix.groups.map((g) => [g.id, new Set(g.keys)]));
      loadError = null;
    } catch (e) {
      loadError = (e as Error).message;
    }
  }

  function toggleDefault(key: string) {
    defaults = { ...defaults, [key]: !defaults[key] };
    saved = false;
  }

  function toggleGroup(gid: number, key: string) {
    const next = new Set(groupKeys[gid] ?? []);
    if (next.has(key)) next.delete(key);
    else next.add(key);
    groupKeys = { ...groupKeys, [gid]: next };
    saved = false;
  }

  async function save() {
    if (!matrix || saving) return;
    saving = true;
    saved = false;
    try {
      await setPermissionDefaults(defaults);
      for (const g of matrix.groups) {
        await setGroupPermissions(g.id, [...(groupKeys[g.id] ?? [])]);
      }
      await refresh();
      saved = true;
    } catch (e) {
      // Writes are sequential (defaults, then per group); on a mid-flight
      // failure re-sync the on-screen matrix with server truth, then report.
      await refresh().catch(() => {});
      loadError = (e as Error).message;
    } finally {
      saving = false;
    }
  }
</script>

<header>
  <a class="back" href="/">← chat</a>
  <h1>feature permissions</h1>
  <a class="tabs" href="/admin/access">groups & model access →</a>
</header>

<main>
  {#if loadError}<p class="error">{loadError}</p>{/if}

  {#if matrix}
    <section class="card">
      <p class="hint">
        Each capability defaults to <strong>allowed</strong>. Turn a default off to restrict it for
        all non-admin users; grant it back to specific groups in the columns. A user's effective
        permission is the default <em>or</em> any grant from a group they belong to. Admins always
        have every capability.
      </p>

      <div class="scroll">
        <table>
          <thead>
            <tr>
              <th class="feat">feature</th>
              <th class="col">default</th>
              {#each matrix.groups as g (g.id)}
                <th class="col">{g.name}</th>
              {/each}
            </tr>
          </thead>
          <tbody>
            {#each matrix.permissions as p (p.key)}
              <tr>
                <td class="feat">{p.label}<span class="key">{p.key}</span></td>
                <td class="col">
                  <input
                    type="checkbox"
                    checked={defaults[p.key]}
                    onchange={() => toggleDefault(p.key)}
                    aria-label={`default ${p.label}`}
                  />
                </td>
                {#each matrix.groups as g (g.id)}
                  <td class="col">
                    {#if defaults[p.key]}
                      <span class="implied" title="already allowed by default">✓</span>
                    {:else}
                      <input
                        type="checkbox"
                        checked={(groupKeys[g.id] ?? new Set()).has(p.key)}
                        onchange={() => toggleGroup(g.id, p.key)}
                        aria-label={`grant ${p.label} to ${g.name}`}
                      />
                    {/if}
                  </td>
                {/each}
              </tr>
            {/each}
          </tbody>
        </table>
      </div>

      {#if !matrix.groups.length}
        <p class="muted">
          No groups yet — create groups in <a href="/admin/access">user access</a> to grant
          restricted features to specific users.
        </p>
      {/if}

      <div class="actions">
        {#if saved}<span class="ok">saved</span>{/if}
        <button class="primary" onclick={save} disabled={saving}>
          {saving ? 'saving…' : 'save changes'}
        </button>
      </div>
    </section>
  {:else if !loadError}
    <p class="muted">loading…</p>
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
  header h1 { font-size: 1rem; margin: 0; }
  .back { color: var(--text-dim); text-decoration: none; font-size: 0.85rem; }
  .back:hover { color: var(--text); }
  .tabs { margin-left: auto; color: var(--accent); text-decoration: none; font-size: 0.85rem; }
  .tabs:hover { text-decoration: underline; }
  main { max-width: 860px; margin: 0 auto; padding: 1.25rem; }
  .card {
    border: 1px solid var(--border-soft);
    border-radius: 8px;
    padding: 1rem 1.25rem;
    background: var(--bg-elev);
  }
  .hint { color: var(--text-dim); font-size: 0.82rem; margin: 0 0 1rem; line-height: 1.45; }
  .scroll { overflow-x: auto; }
  table { border-collapse: collapse; width: 100%; font-size: 0.85rem; }
  th, td { padding: 0.45rem 0.6rem; border-top: 1px solid var(--border-soft); text-align: left; }
  thead th { border-top: none; color: var(--text-muted); font-weight: 500; font-size: 0.78rem; }
  th.col, td.col { text-align: center; width: 5.5rem; }
  td.feat { font-weight: 500; }
  td.feat .key {
    display: block;
    font-family: ui-monospace, monospace;
    font-weight: 400;
    font-size: 0.72rem;
    color: var(--text-muted);
  }
  input[type='checkbox'] { width: 1rem; height: 1rem; cursor: pointer; }
  .implied { color: var(--text-muted); opacity: 0.6; }
  .muted { color: var(--text-muted); font-size: 0.82rem; margin: 0.75rem 0 0; }
  .muted a { color: var(--accent); }
  .actions { display: flex; align-items: center; justify-content: flex-end; gap: 0.75rem; margin-top: 1rem; }
  .ok { color: var(--accent); font-size: 0.82rem; }
  button {
    background: var(--bg);
    color: var(--text);
    border: 1px solid var(--border);
    border-radius: 6px;
    padding: 0.45rem 0.9rem;
    font: inherit;
    font-size: 0.85rem;
    cursor: pointer;
  }
  button:hover:not(:disabled) { background: var(--bg-hover); }
  button:disabled { opacity: 0.5; cursor: default; }
  button.primary { border-color: var(--accent); color: var(--accent); }
  .error { color: var(--danger); font-size: 0.85rem; }
</style>

<script lang="ts">
  import { goto } from '$app/navigation';
  import { onMount } from 'svelte';
  import { appConfig } from '$lib/appConfig.svelte';
  import { auth } from '$lib/auth.svelte';

  let username = $state('');
  let password = $state('');
  let confirm = $state('');
  let busy = $state(false);
  let error = $state<string | null>(null);

  onMount(async () => {
    const s = await auth.refresh();
    if (!s.setup_required) {
      goto(s.user ? '/' : '/login', { replaceState: true });
    }
  });

  async function submit(e: SubmitEvent) {
    e.preventDefault();
    if (busy) return;
    if (password.length < 6) {
      error = 'password must be at least 6 characters';
      return;
    }
    if (password !== confirm) {
      error = 'passwords do not match';
      return;
    }
    busy = true;
    error = null;
    try {
      await auth.setup(username, password);
      await goto('/', { replaceState: true });
    } catch (err) {
      error = (err as Error).message;
    } finally {
      busy = false;
    }
  }
</script>

<div class="wrap">
  <form class="card" onsubmit={submit}>
    <h1>{appConfig.instanceName}</h1>
    <p class="sub">first-time setup — create your admin account</p>
    {#if error}<div class="err">{error}</div>{/if}
    <label>
      <span>username</span>
      <input bind:value={username} autocomplete="username" required autofocus />
    </label>
    <label>
      <span>password</span>
      <input bind:value={password} type="password" autocomplete="new-password" required />
    </label>
    <label>
      <span>confirm password</span>
      <input bind:value={confirm} type="password" autocomplete="new-password" required />
    </label>
    <button type="submit" disabled={busy || !username || !password}>
      {busy ? 'creating…' : 'create admin account'}
    </button>
  </form>
</div>

<style>
  .wrap {
    flex: 1;
    display: flex;
    align-items: center;
    justify-content: center;
    padding: 1rem;
  }
  .card {
    width: 100%;
    max-width: 360px;
    background: var(--bg-elev);
    border: 1px solid var(--border-soft);
    border-radius: 10px;
    padding: 1.5rem;
    display: flex;
    flex-direction: column;
    gap: 0.75rem;
  }
  h1 {
    margin: 0;
    color: var(--accent);
    font-family: ui-monospace, monospace;
    font-size: 1.4rem;
    text-align: center;
  }
  .sub {
    margin: 0 0 0.5rem;
    color: var(--text-muted);
    text-align: center;
    font-size: 0.85rem;
  }
  label { display: flex; flex-direction: column; gap: 0.3rem; }
  label span {
    font-size: 0.72rem;
    text-transform: uppercase;
    color: var(--text-muted);
  }
  input {
    background: var(--bg);
    color: var(--text);
    border: 1px solid var(--border);
    border-radius: 6px;
    padding: 0.55rem 0.7rem;
    font: inherit;
  }
  button {
    margin-top: 0.5rem;
    background: var(--bg-hover);
    color: var(--text);
    border: 1px solid var(--border);
    border-radius: 6px;
    padding: 0.6rem;
    font: inherit;
    cursor: pointer;
  }
  button:disabled { opacity: 0.5; cursor: not-allowed; }
  .err {
    background: color-mix(in srgb, var(--danger) 15%, transparent);
    color: var(--danger);
    padding: 0.5rem 0.75rem;
    border-radius: 6px;
    font-size: 0.85rem;
  }
</style>

<script lang="ts">
  import { goto } from '$app/navigation';
  import { onMount } from 'svelte';
  import { auth } from '$lib/auth.svelte';
  import { t } from '$lib/i18n.svelte';

  let username = $state('');
  let password = $state('');
  let busy = $state(false);
  let error = $state<string | null>(null);

  // Where to land after sign-in: the `next` param (set by the 401 interceptor),
  // validated to a local path so it can't be used as an open redirect.
  function nextTarget(): string {
    const raw = new URLSearchParams(window.location.search).get('next');
    if (raw && raw.startsWith('/') && !raw.startsWith('//')) return raw;
    return '/';
  }

  onMount(async () => {
    const ssoError = new URLSearchParams(window.location.search).get('sso_error');
    if (ssoError) error = `SSO sign-in failed: ${ssoError}`;
    const s = await auth.refresh();
    if (s.setup_required) goto('/setup', { replaceState: true });
    else if (s.user) goto(nextTarget(), { replaceState: true });
  });

  async function submit(e: SubmitEvent) {
    e.preventDefault();
    if (busy) return;
    busy = true;
    error = null;
    try {
      await auth.login(username, password);
      await goto(nextTarget(), { replaceState: true });
    } catch (err) {
      error = (err as Error).message;
    } finally {
      busy = false;
    }
  }
</script>

<div class="wrap">
  <form class="card" onsubmit={submit}>
    <h1>free-webui</h1>
    <p class="sub">{t('login.signIn')}</p>
    {#if error}<div class="err">{error}</div>{/if}
    <label>
      <span>{t('login.username')}</span>
      <input bind:value={username} autocomplete="username" required autofocus />
    </label>
    <label>
      <span>{t('login.password')}</span>
      <input bind:value={password} type="password" autocomplete="current-password" required />
    </label>
    <button type="submit" disabled={busy || !username || !password}>
      {busy ? t('login.signingIn') : t('login.signIn')}
    </button>
    {#if auth.oidcEnabled}
      <div class="divider"><span>or</span></div>
      <a class="sso" href="/api/auth/oidc/login">sign in with {auth.oidcName}</a>
    {/if}
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
  .divider {
    display: flex;
    align-items: center;
    gap: 0.6rem;
    color: var(--text-muted);
    font-size: 0.72rem;
    margin: 0.25rem 0;
  }
  .divider::before,
  .divider::after {
    content: '';
    flex: 1;
    height: 1px;
    background: var(--border-soft);
  }
  .sso {
    display: block;
    text-align: center;
    background: var(--bg);
    color: var(--text);
    border: 1px solid var(--accent);
    border-radius: 6px;
    padding: 0.6rem;
    text-decoration: none;
    font-size: 0.9rem;
  }
  .sso:hover { background: var(--bg-hover); }
</style>

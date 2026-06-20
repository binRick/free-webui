<script lang="ts">
  import { onMount } from 'svelte';
  import { goto } from '$app/navigation';
  import { appConfig } from '$lib/appConfig.svelte';
  import { auth } from '$lib/auth.svelte';
  import { getAppearance, setAppearance } from '$lib/api';

  let instanceName = $state('');
  let customCss = $state('');
  let loadError = $state<string | null>(null);
  let saving = $state(false);
  let saved = $state(false);

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
    try {
      const a = await getAppearance();
      instanceName = a.instance_name;
      customCss = a.custom_css;
    } catch (e) {
      loadError = (e as Error).message;
    }
  });

  async function save() {
    if (!instanceName.trim() || saving) return;
    saving = true;
    saved = false;
    try {
      const a = await setAppearance(instanceName.trim(), customCss);
      instanceName = a.instance_name;
      customCss = a.custom_css;
      // reflect immediately for this admin without a reload.
      appConfig.instanceName = a.instance_name;
      appConfig.customCss = a.custom_css;
      saved = true;
    } catch (e) {
      loadError = (e as Error).message;
    } finally {
      saving = false;
    }
  }
</script>

<header>
  <a class="back" href="/">← chat</a>
  <h1>appearance</h1>
</header>

<main>
  {#if loadError}<p class="error">{loadError}</p>{/if}

  <section class="card">
    <h2>branding</h2>
    <p class="hint">The instance name shown in the sidebar, setup, and share pages.</p>
    <input
      class="name"
      bind:value={instanceName}
      maxlength="80"
      placeholder="instance name"
      oninput={() => (saved = false)}
    />
  </section>

  <section class="card">
    <h2>custom CSS</h2>
    <p class="hint">
      Applied site-wide for every user (including logged-out login / setup / share
      pages). Override theme variables like <code>--accent</code>, <code>--bg</code>,
      <code>--text</code>, or any selector. Operator-authored — keep it trusted.
    </p>
    <textarea
      class="css"
      bind:value={customCss}
      spellcheck="false"
      placeholder={':root { --accent: #e11d48; }'}
      oninput={() => (saved = false)}
    ></textarea>
  </section>

  <div class="actions">
    {#if saved}<span class="ok">saved</span>{/if}
    <button class="primary" onclick={save} disabled={saving || !instanceName.trim()}>
      {saving ? 'saving…' : 'save'}
    </button>
  </div>
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
  main { max-width: 760px; margin: 0 auto; padding: 1.25rem; display: flex; flex-direction: column; gap: 1.25rem; }
  .card {
    border: 1px solid var(--border-soft);
    border-radius: 8px;
    padding: 1rem 1.25rem;
    background: var(--bg-elev);
  }
  .card h2 { margin: 0 0 0.5rem; font-size: 0.95rem; }
  .hint { color: var(--text-dim); font-size: 0.82rem; margin: 0 0 0.75rem; line-height: 1.45; }
  .name, .css {
    width: 100%;
    box-sizing: border-box;
    background: var(--bg);
    color: var(--text);
    border: 1px solid var(--border-soft);
    border-radius: 6px;
    padding: 0.5rem 0.6rem;
    font: inherit;
    font-size: 0.85rem;
  }
  .css {
    min-height: 14rem;
    resize: vertical;
    font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
    line-height: 1.5;
  }
  .actions { display: flex; align-items: center; justify-content: flex-end; gap: 0.75rem; }
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

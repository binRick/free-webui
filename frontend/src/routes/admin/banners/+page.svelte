<script lang="ts">
  import { onMount } from 'svelte';
  import { goto } from '$app/navigation';
  import { auth } from '$lib/auth.svelte';
  import { toasts } from '$lib/toastStore.svelte';
  import { listBanners, createBanner, deleteBanner, type Banner, type BannerType } from '$lib/api';

  let banners = $state<Banner[]>([]);
  let content = $state('');
  let type = $state<BannerType>('info');
  let dismissible = $state(true);
  let saving = $state(false);

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
    banners = await listBanners();
  });

  async function add() {
    const c = content.trim();
    if (!c || saving) return;
    saving = true;
    try {
      await createBanner(c, type, dismissible);
      content = '';
      banners = await listBanners();
    } catch (e) {
      toasts.error((e as Error).message);
    } finally {
      saving = false;
    }
  }

  async function remove(id: number) {
    await deleteBanner(id);
    banners = banners.filter((b) => b.id !== id);
  }

  function when(ts: number): string {
    return new Date(ts * 1000).toLocaleString();
  }
</script>

<svelte:head><title>banners · free-webui</title></svelte:head>

<div class="banners-admin">
  <header>
    <a class="back" href="/">← chat</a>
    <h1>banners</h1>
  </header>

  <section class="new">
    <textarea bind:value={content} rows="2" placeholder="announcement shown to every user…"></textarea>
    <div class="row">
      <label>type
        <select bind:value={type}>
          <option value="info">info</option>
          <option value="success">success</option>
          <option value="warning">warning</option>
          <option value="error">error</option>
        </select>
      </label>
      <label class="chk"><input type="checkbox" bind:checked={dismissible} /> dismissible</label>
      <button class="primary" onclick={add} disabled={!content.trim() || saving}>
        {saving ? 'posting…' : 'post banner'}
      </button>
    </div>
  </section>

  {#if banners.length === 0}
    <div class="empty">no active banners</div>
  {:else}
    <ul class="list">
      {#each banners as b (b.id)}
        <li class="item {b.type}">
          <span class="tag">{b.type}</span>
          <span class="content">{b.content}</span>
          <span class="meta">{when(b.created_at)}{b.dismissible ? '' : ' · sticky'}</span>
          <button class="del" aria-label="delete" onclick={() => remove(b.id)}>×</button>
        </li>
      {/each}
    </ul>
  {/if}
</div>

<style>
  .banners-admin {
    max-width: 760px;
    margin: 0 auto;
    width: 100%;
    padding: 1rem;
    overflow-y: auto;
  }
  header {
    display: flex;
    align-items: center;
    gap: 1rem;
    margin-bottom: 1rem;
  }
  header h1 { flex: 1; font-size: 1.2rem; margin: 0; }
  .back { color: var(--text-dim); text-decoration: none; font-size: 0.85rem; }
  .back:hover { color: var(--text); }
  .new {
    background: var(--bg-elev);
    border: 1px solid var(--border-soft);
    border-radius: 8px;
    padding: 0.75rem;
    margin-bottom: 1.5rem;
  }
  .new textarea {
    width: 100%;
    box-sizing: border-box;
    resize: vertical;
    background: var(--bg);
    color: var(--text);
    border: 1px solid var(--border);
    border-radius: 6px;
    padding: 0.5rem;
    font: inherit;
  }
  .new .row {
    display: flex;
    align-items: center;
    gap: 1rem;
    margin-top: 0.6rem;
  }
  .new label { font-size: 0.85rem; color: var(--text-dim); display: flex; align-items: center; gap: 0.4rem; }
  .new select {
    background: var(--bg);
    color: var(--text);
    border: 1px solid var(--border);
    border-radius: 6px;
    padding: 0.3rem;
    font: inherit;
  }
  .new .primary {
    margin-left: auto;
    background: var(--accent);
    color: #fff;
    border: none;
    border-radius: 6px;
    padding: 0.45rem 0.9rem;
    font: inherit;
    cursor: pointer;
  }
  .new .primary:disabled { opacity: 0.5; cursor: default; }
  .empty { color: var(--text-muted); padding: 1rem 0; }
  .list { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 0.5rem; }
  .item {
    display: grid;
    grid-template-columns: auto 1fr auto auto;
    align-items: center;
    gap: 0.6rem;
    padding: 0.6rem 0.8rem;
    border: 1px solid var(--border-soft);
    border-radius: 8px;
    background: var(--bg-elev);
  }
  .tag {
    font-size: 0.7rem;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    padding: 0.1rem 0.4rem;
    border-radius: 999px;
    border: 1px solid currentColor;
  }
  .item.info .tag { color: var(--accent); }
  .item.success .tag { color: #3fa66a; }
  .item.warning .tag { color: #e0a300; }
  .item.error .tag { color: #d8584a; }
  .item .content { overflow-wrap: anywhere; }
  .item .meta { color: var(--text-muted); font-size: 0.72rem; white-space: nowrap; }
  .item .del {
    background: transparent;
    border: none;
    color: var(--text-dim);
    cursor: pointer;
    font-size: 1.1rem;
  }
  .item .del:hover { color: #d8584a; }
</style>

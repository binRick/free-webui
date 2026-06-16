<script lang="ts">
  import { onMount } from 'svelte';
  import { listBanners, type Banner } from './api';

  const KEY = 'fw_dismissed_banners';

  let banners = $state<Banner[]>([]);
  let dismissed = $state<Set<number>>(new Set());

  function loadDismissed(): Set<number> {
    try {
      return new Set(JSON.parse(localStorage.getItem(KEY) || '[]'));
    } catch {
      return new Set();
    }
  }

  onMount(async () => {
    dismissed = loadDismissed();
    banners = await listBanners();
  });

  const visible = $derived(banners.filter((b) => !(b.dismissible && dismissed.has(b.id))));

  function dismiss(id: number) {
    const next = new Set(dismissed);
    next.add(id);
    dismissed = next;
    localStorage.setItem(KEY, JSON.stringify([...next]));
  }
</script>

{#if visible.length}
  <div class="banners">
    {#each visible as b (b.id)}
      <div class="banner {b.type}" role="status">
        <span class="msg">{b.content}</span>
        {#if b.dismissible}
          <button class="x" aria-label="dismiss" onclick={() => dismiss(b.id)}>×</button>
        {/if}
      </div>
    {/each}
  </div>
{/if}

<style>
  .banners {
    display: flex;
    flex-direction: column;
  }
  .banner {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    padding: 0.45rem 1rem;
    font-size: 0.85rem;
    border-bottom: 1px solid var(--border-soft);
  }
  .banner .msg {
    flex: 1;
    overflow-wrap: anywhere;
  }
  .banner.info {
    background: color-mix(in srgb, var(--accent) 14%, var(--bg-elev));
    color: var(--text);
  }
  .banner.success {
    background: color-mix(in srgb, #3fa66a 16%, var(--bg-elev));
  }
  .banner.warning {
    background: color-mix(in srgb, #e0a300 22%, var(--bg-elev));
  }
  .banner.error {
    background: color-mix(in srgb, #d8584a 18%, var(--bg-elev));
  }
  .banner .x {
    background: transparent;
    border: none;
    color: inherit;
    cursor: pointer;
    font-size: 1.1rem;
    line-height: 1;
    opacity: 0.7;
  }
  .banner .x:hover {
    opacity: 1;
  }
</style>

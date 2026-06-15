<script lang="ts">
  import { toasts } from './toastStore.svelte';
</script>

<div class="toasts" aria-live="polite">
  {#each toasts.items as t (t.id)}
    <div class="toast {t.kind}" role="status">
      <span class="msg">{t.message}</span>
      <button class="x" aria-label="dismiss" onclick={() => toasts.dismiss(t.id)}>×</button>
    </div>
  {/each}
</div>

<style>
  .toasts {
    position: fixed;
    bottom: 1rem;
    right: 1rem;
    z-index: 1000;
    display: flex;
    flex-direction: column;
    gap: 0.5rem;
    max-width: min(360px, 90vw);
  }
  .toast {
    display: flex;
    align-items: flex-start;
    gap: 0.5rem;
    padding: 0.6rem 0.75rem;
    border-radius: 8px;
    border: 1px solid var(--border-soft);
    background: var(--bg-elev);
    color: var(--text);
    box-shadow: 0 6px 20px rgba(0, 0, 0, 0.25);
    font-size: 0.85rem;
  }
  .toast.error {
    border-color: #d8584a;
    background: color-mix(in srgb, #d8584a 14%, var(--bg-elev));
  }
  .toast.success {
    border-color: #3fa66a;
    background: color-mix(in srgb, #3fa66a 14%, var(--bg-elev));
  }
  .msg {
    flex: 1;
    overflow-wrap: anywhere;
  }
  .x {
    flex-shrink: 0;
    background: transparent;
    border: none;
    color: var(--text-dim);
    cursor: pointer;
    font-size: 1rem;
    line-height: 1;
    padding: 0;
  }
  .x:hover {
    color: var(--text);
  }
</style>

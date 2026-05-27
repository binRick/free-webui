<script lang="ts">
  import { onMount } from 'svelte';
  import { goto } from '$app/navigation';
  import { createConversation } from '$lib/api';

  let error = $state<string | null>(null);

  onMount(async () => {
    try {
      const conv = await createConversation(null);
      await goto(`/chat/${conv.id}`, { replaceState: true });
    } catch (err) {
      error = (err as Error).message;
    }
  });
</script>

<div class="status">
  {#if error}
    <p class="err">couldn't start a new chat: {error}</p>
    <p class="hint">is the backend running on :8000?</p>
  {:else}
    <p>opening new chat…</p>
  {/if}
</div>

<style>
  .status {
    flex: 1;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    color: var(--text-muted);
    gap: 0.5rem;
  }
  .err { color: var(--danger); }
  .hint { font-size: 0.85rem; }
</style>

<script lang="ts">
  // A searchable model selector. Replaces a plain <select> so a long, merged
  // model list (many upstream connections) stays navigable: click to open a
  // filterable popover, type to narrow, click / Enter to choose, Esc to close.
  interface Props {
    models: string[];
    value: string | null;
    disabled?: boolean;
    onSelect: (model: string) => void;
  }
  let { models, value, disabled = false, onSelect }: Props = $props();

  let open = $state(false);
  let query = $state('');
  let highlight = $state(0);
  let root = $state<HTMLDivElement | null>(null);
  let input = $state<HTMLInputElement | null>(null);

  const filtered = $derived.by(() => {
    const q = query.trim().toLowerCase();
    if (!q) return models;
    return models.filter((m) => m.toLowerCase().includes(q));
  });

  function openMenu() {
    if (disabled) return;
    open = true;
    query = '';
    highlight = Math.max(0, models.indexOf(value ?? ''));
    queueMicrotask(() => input?.focus());
  }
  function close() {
    open = false;
  }
  function pick(m: string) {
    onSelect(m);
    close();
  }
  function onKey(e: KeyboardEvent) {
    if (e.key === 'Escape') {
      close();
    } else if (e.key === 'ArrowDown') {
      e.preventDefault();
      highlight = Math.min(filtered.length - 1, highlight + 1);
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      highlight = Math.max(0, highlight - 1);
    } else if (e.key === 'Enter') {
      e.preventDefault();
      const m = filtered[highlight];
      if (m) pick(m);
    }
  }
  function onWindowClick(e: MouseEvent) {
    if (open && root && !root.contains(e.target as Node)) close();
  }
  $effect(() => {
    // re-clamp the highlight whenever the filtered set shrinks
    if (highlight >= filtered.length) highlight = Math.max(0, filtered.length - 1);
  });
</script>

<svelte:window onclick={onWindowClick} />

<div class="picker" bind:this={root}>
  <button
    type="button"
    class="trigger"
    {disabled}
    aria-haspopup="listbox"
    aria-expanded={open}
    title={value ?? 'select a model'}
    onclick={() => (open ? close() : openMenu())}
  >
    <span class="current">{value ?? 'no model'}</span>
    <span class="caret">▾</span>
  </button>

  {#if open}
    <div class="menu" role="listbox">
      <input
        bind:this={input}
        bind:value={query}
        class="filter"
        type="text"
        placeholder="search {models.length} models…"
        onkeydown={onKey}
        aria-label="filter models"
      />
      <div class="options">
        {#each filtered as m, i (m)}
          <button
            type="button"
            role="option"
            aria-selected={m === value}
            class="option"
            class:active={i === highlight}
            class:selected={m === value}
            onmouseenter={() => (highlight = i)}
            onclick={() => pick(m)}
          >
            {m}
          </button>
        {:else}
          <div class="no-match">no models match “{query}”</div>
        {/each}
      </div>
    </div>
  {/if}
</div>

<style>
  .picker {
    position: relative;
  }
  .trigger {
    display: inline-flex;
    align-items: center;
    gap: 0.4rem;
    max-width: 220px;
    background: var(--bg-elev);
    color: var(--text);
    border: 1px solid var(--border-soft);
    border-radius: 6px;
    padding: 0.35rem 0.6rem;
    font: inherit;
    font-size: 0.85rem;
    cursor: pointer;
  }
  .trigger:disabled {
    opacity: 0.5;
    cursor: not-allowed;
  }
  .current {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .caret {
    color: var(--text-dim);
    font-size: 0.7rem;
  }
  .menu {
    position: absolute;
    top: calc(100% + 4px);
    right: 0;
    z-index: 30;
    width: 280px;
    max-width: 80vw;
    background: var(--bg-elev);
    border: 1px solid var(--border-soft);
    border-radius: 8px;
    box-shadow: 0 8px 24px rgba(0, 0, 0, 0.25);
    padding: 0.4rem;
    display: flex;
    flex-direction: column;
    gap: 0.3rem;
  }
  .filter {
    background: var(--bg-sidebar);
    color: var(--text);
    border: 1px solid var(--border-soft);
    border-radius: 6px;
    padding: 0.35rem 0.5rem;
    font: inherit;
    font-size: 0.85rem;
  }
  .filter:focus {
    outline: none;
    border-color: var(--accent);
  }
  .options {
    max-height: 280px;
    overflow-y: auto;
    display: flex;
    flex-direction: column;
  }
  .option {
    text-align: left;
    background: transparent;
    color: var(--text);
    border: none;
    border-radius: 6px;
    padding: 0.4rem 0.5rem;
    font: inherit;
    font-size: 0.85rem;
    cursor: pointer;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .option.active {
    background: var(--bg-sidebar);
  }
  .option.selected {
    color: var(--accent);
    font-weight: 600;
  }
  .no-match {
    color: var(--text-dim);
    font-size: 0.8rem;
    padding: 0.4rem 0.5rem;
  }
</style>

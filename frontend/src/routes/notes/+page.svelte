<script lang="ts">
  import { onMount } from 'svelte';
  import Markdown from '$lib/Markdown.svelte';
  import { toasts } from '$lib/toastStore.svelte';
  import {
    listNotes,
    createNote,
    updateNote,
    deleteNote,
    type Note
  } from '$lib/api';

  let notes = $state<Note[]>([]);
  let selectedId = $state<number | null>(null);
  let title = $state('');
  let content = $state('');
  let dirty = $state(false);
  let saving = $state(false);
  let preview = $state(false);

  const selected = $derived(notes.find((n) => n.id === selectedId) ?? null);

  onMount(async () => {
    notes = await listNotes();
    if (notes.length) select(notes[0]);
  });

  function select(n: Note) {
    if (dirty && !confirm('discard unsaved changes?')) return;
    selectedId = n.id;
    title = n.title;
    content = n.content;
    dirty = false;
    preview = false;
  }

  async function newNote() {
    const n = await createNote('Untitled');
    notes = [n, ...notes];
    selectedId = n.id;
    title = n.title;
    content = n.content;
    dirty = false;
    preview = false;
  }

  async function save() {
    if (selectedId == null || saving) return;
    const t = title.trim();
    if (!t) {
      toasts.error('a note needs a title');
      return;
    }
    saving = true;
    try {
      const updated = await updateNote(selectedId, { title: t, content });
      notes = notes.map((n) => (n.id === updated.id ? updated : n));
      // keep the list ordered by most-recently-updated
      notes = [...notes].sort((a, b) => b.updated_at - a.updated_at);
      dirty = false;
      toasts.success('note saved');
    } catch (e) {
      toasts.error((e as Error).message);
    } finally {
      saving = false;
    }
  }

  async function del(n: Note, e: Event) {
    e.preventDefault();
    e.stopPropagation();
    if (!confirm(`delete "${n.title}"?`)) return;
    await deleteNote(n.id);
    notes = notes.filter((x) => x.id !== n.id);
    if (selectedId === n.id) {
      selectedId = null;
      title = '';
      content = '';
      dirty = false;
    }
  }

  function markDirty() {
    dirty = true;
  }

  function fmt(ts: number): string {
    return new Date(ts * 1000).toLocaleDateString();
  }
</script>

<svelte:head><title>notes · free-webui</title></svelte:head>

<div class="notes">
  <aside class="list">
    <header>
      <a class="back" href="/">← chat</a>
      <button class="new" onclick={newNote}>+ note</button>
    </header>
    {#each notes as n (n.id)}
      <div class="item" class:active={n.id === selectedId}>
        <button class="pick" onclick={() => select(n)}>
          <span class="t">{n.title || 'Untitled'}</span>
          <span class="meta">{fmt(n.updated_at)}</span>
        </button>
        <button class="x" aria-label="delete note" title="delete" onclick={(e) => del(n, e)}>×</button>
      </div>
    {:else}
      <div class="empty">no notes yet</div>
    {/each}
  </aside>

  <section class="editor">
    {#if selectedId != null}
      <div class="toolbar">
        <input class="title" bind:value={title} oninput={markDirty} placeholder="title" aria-label="note title" />
        <button class="ghost" onclick={() => (preview = !preview)}>{preview ? '✎ edit' : '👁 preview'}</button>
        <button class="primary" onclick={save} disabled={saving || !dirty}>
          {saving ? 'saving…' : dirty ? 'save' : 'saved'}
        </button>
      </div>
      {#if preview}
        <div class="preview"><Markdown source={content} /></div>
      {:else}
        <textarea
          class="body"
          bind:value={content}
          oninput={markDirty}
          placeholder="write in markdown…"
          aria-label="note content"
        ></textarea>
      {/if}
    {:else}
      <div class="placeholder">select a note, or create one</div>
    {/if}
  </section>
</div>

<style>
  .notes {
    display: grid;
    grid-template-columns: 260px 1fr;
    height: 100%;
    min-height: 0;
  }
  .list {
    border-right: 1px solid var(--border-soft);
    display: flex;
    flex-direction: column;
    overflow-y: auto;
    background: var(--bg-sidebar);
  }
  .list header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 0.75rem;
    border-bottom: 1px solid var(--border-soft);
  }
  .back {
    color: var(--text-dim);
    text-decoration: none;
    font-size: 0.85rem;
  }
  .back:hover { color: var(--text); }
  .new {
    background: var(--accent);
    color: #fff;
    border: none;
    border-radius: 6px;
    padding: 0.3rem 0.6rem;
    font: inherit;
    font-size: 0.8rem;
    cursor: pointer;
  }
  .item {
    display: flex;
    align-items: center;
    border-bottom: 1px solid var(--border-soft);
  }
  .item:hover { background: var(--bg-hover); }
  .item.active { background: var(--bg-hover); }
  .item .pick {
    flex: 1;
    min-width: 0;
    display: flex;
    flex-direction: column;
    align-items: flex-start;
    gap: 0.1rem;
    text-align: left;
    background: transparent;
    border: none;
    padding: 0.6rem 0.75rem;
    cursor: pointer;
    color: var(--text);
  }
  .item .t {
    max-width: 100%;
    font-size: 0.9rem;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .item .meta { color: var(--text-muted); font-size: 0.72rem; }
  .item .x {
    background: transparent;
    border: none;
    color: var(--text-dim);
    font-size: 1rem;
    padding: 0 0.6rem;
    cursor: pointer;
  }
  .item .x:hover { color: #d8584a; }
  .empty,
  .placeholder { color: var(--text-muted); padding: 1rem; font-size: 0.85rem; }
  .placeholder {
    display: flex;
    align-items: center;
    justify-content: center;
    height: 100%;
  }
  .editor {
    display: flex;
    flex-direction: column;
    min-width: 0;
    min-height: 0;
  }
  .toolbar {
    display: flex;
    gap: 0.5rem;
    padding: 0.75rem;
    border-bottom: 1px solid var(--border-soft);
  }
  .title {
    flex: 1;
    background: var(--bg-elev);
    color: var(--text);
    border: 1px solid var(--border);
    border-radius: 6px;
    padding: 0.45rem 0.6rem;
    font: inherit;
    font-weight: 600;
  }
  .toolbar button {
    border: 1px solid var(--border);
    border-radius: 6px;
    padding: 0.45rem 0.7rem;
    font: inherit;
    cursor: pointer;
    background: var(--bg-elev);
    color: var(--text);
  }
  .toolbar .primary { background: var(--accent); color: #fff; border-color: var(--accent); }
  .toolbar button:disabled { opacity: 0.55; cursor: default; }
  .body {
    flex: 1;
    resize: none;
    border: none;
    background: var(--bg);
    color: var(--text);
    padding: 1rem;
    font: inherit;
    font-family: var(--mono, ui-monospace, monospace);
    font-size: 0.9rem;
    line-height: 1.5;
  }
  .body:focus { outline: none; }
  .preview {
    flex: 1;
    overflow-y: auto;
    padding: 1rem 1.25rem;
  }
</style>

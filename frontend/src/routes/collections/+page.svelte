<script lang="ts">
  import { onMount } from 'svelte';
  import { goto } from '$app/navigation';
  import { auth } from '$lib/auth.svelte';
  import {
    createCollection,
    deleteCollection,
    deleteCollectionDocument,
    getMyPermissions,
    listCollectionDocuments,
    listCollections,
    uploadCollectionDocument,
    type Collection,
    type Document
  } from '$lib/api';

  let collections = $state<Collection[]>([]);
  let loadError = $state<string | null>(null);
  let newName = $state('');
  let busy = $state(false);
  let knowledgeAllowed = $state(true);
  let fileUploadAllowed = $state(true);

  let openId = $state<number | null>(null);
  let docs = $state<Document[]>([]);
  let uploading = $state(false);
  let fileInput = $state<HTMLInputElement>();

  onMount(async () => {
    if (!auth.loaded) await auth.refresh();
    if (!auth.user) {
      await goto('/login', { replaceState: true });
      return;
    }
    const perms = await getMyPermissions();
    knowledgeAllowed = perms.knowledge !== false;
    fileUploadAllowed = perms.file_upload !== false;
    await refresh();
  });

  async function refresh() {
    try {
      collections = await listCollections();
      loadError = null;
    } catch (e) {
      loadError = (e as Error).message;
    }
  }

  async function add(e: SubmitEvent) {
    e.preventDefault();
    if (!newName.trim() || busy) return;
    busy = true;
    try {
      await createCollection(newName.trim());
      newName = '';
      await refresh();
    } catch (e) {
      loadError = (e as Error).message;
    } finally {
      busy = false;
    }
  }

  async function remove(c: Collection) {
    if (!confirm(`delete collection "${c.name}" and its documents?`) || busy) return;
    busy = true;
    try {
      await deleteCollection(c.id);
      if (openId === c.id) openId = null;
      await refresh();
    } catch (e) {
      loadError = (e as Error).message;
    } finally {
      busy = false;
    }
  }

  async function open(c: Collection) {
    openId = openId === c.id ? null : c.id;
    if (openId != null) docs = await listCollectionDocuments(openId);
  }

  async function onFiles(e: Event) {
    const target = e.target as HTMLInputElement;
    if (!target.files?.length || openId == null) return;
    uploading = true;
    try {
      for (const f of Array.from(target.files)) await uploadCollectionDocument(openId, f);
      docs = await listCollectionDocuments(openId);
      await refresh();
    } catch (e) {
      loadError = (e as Error).message;
    } finally {
      uploading = false;
      target.value = '';
    }
  }

  async function removeDoc(docId: number) {
    if (openId == null) return;
    await deleteCollectionDocument(openId, docId);
    docs = await listCollectionDocuments(openId);
    await refresh();
  }
</script>

<header>
  <a class="back" href="/">← chat</a>
  <h1>knowledge bases</h1>
</header>

<main>
  {#if loadError}<p class="error">{loadError}</p>{/if}

  <section class="card">
    <p class="hint">Collections are reusable document sets. Attach one to any chat
      (in the chat's settings drawer) to ground answers in it — embed once, use
      everywhere.</p>
    {#if !knowledgeAllowed}
      <p class="hint">Creating knowledge bases is disabled for your account.</p>
    {/if}
    <form class="row" onsubmit={add}>
      <input
        placeholder="new collection name"
        bind:value={newName}
        maxlength="120"
        disabled={!knowledgeAllowed}
      />
      <button type="submit" disabled={!newName.trim() || busy || !knowledgeAllowed}>+ create</button>
    </form>

    {#each collections as c (c.id)}
      <div class="item">
        <button class="name-btn" onclick={() => open(c)}>
          {openId === c.id ? '▾' : '▸'} {c.name}
        </button>
        <span class="meta">{c.document_count} doc{c.document_count === 1 ? '' : 's'}</span>
        <button class="small del" onclick={() => remove(c)} disabled={busy}>delete</button>
      </div>
      {#if openId === c.id}
        <div class="docs">
          <button
            class="small"
            onclick={() => fileInput?.click()}
            disabled={uploading || !fileUploadAllowed}
            title={fileUploadAllowed ? '' : 'file upload is disabled for your account'}
          >
            {uploading ? 'uploading…' : '+ upload documents'}
          </button>
          <input bind:this={fileInput} type="file" multiple hidden onchange={onFiles} />
          {#each docs as d (d.id)}
            <div class="doc">
              <span class="fn">{d.filename}</span>
              <span class="meta">{d.chunk_count} chunks</span>
              <button class="doc-x" aria-label="remove" onclick={() => removeDoc(d.id)}>×</button>
            </div>
          {:else}
            <p class="empty">no documents yet</p>
          {/each}
        </div>
      {/if}
    {:else}
      <p class="empty">no collections yet</p>
    {/each}
  </section>
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
  main { max-width: 680px; margin: 0 auto; padding: 1.25rem; }
  .card { border: 1px solid var(--border-soft); border-radius: 8px; padding: 1rem 1.25rem; background: var(--bg-elev); }
  .hint { color: var(--text-dim); font-size: 0.82rem; margin: 0 0 0.75rem; }
  .row { display: flex; gap: 0.5rem; margin-bottom: 0.5rem; }
  .row input {
    flex: 1;
    background: var(--bg);
    color: var(--text);
    border: 1px solid var(--border-soft);
    border-radius: 6px;
    padding: 0.4rem 0.6rem;
    font: inherit;
    font-size: 0.85rem;
  }
  button {
    background: var(--bg);
    color: var(--text);
    border: 1px solid var(--border);
    border-radius: 6px;
    padding: 0.4rem 0.7rem;
    font: inherit;
    font-size: 0.82rem;
    cursor: pointer;
  }
  button:hover:not(:disabled) { background: var(--bg-hover); }
  button:disabled { opacity: 0.5; cursor: default; }
  button.small { padding: 0.25rem 0.55rem; font-size: 0.76rem; }
  button.del:hover:not(:disabled) { color: var(--danger); border-color: var(--danger); }
  .item {
    display: flex;
    align-items: center;
    gap: 0.6rem;
    padding: 0.5rem 0;
    border-top: 1px solid var(--border-soft);
  }
  .name-btn {
    border: 0;
    background: transparent;
    color: var(--text);
    font-weight: 500;
    padding: 0.1rem 0.2rem;
  }
  .name-btn:hover { background: transparent; color: var(--accent); }
  .meta { color: var(--text-muted); font-size: 0.78rem; }
  .item .del { margin-left: auto; }
  .docs { padding: 0.25rem 0 0.6rem 1.25rem; display: flex; flex-direction: column; gap: 0.3rem; }
  .doc { display: flex; align-items: center; gap: 0.5rem; font-size: 0.85rem; }
  .fn { overflow: hidden; white-space: nowrap; text-overflow: ellipsis; }
  .doc .meta { margin-left: auto; }
  .doc-x {
    border: 0; background: transparent; color: var(--text-muted);
    cursor: pointer; font-size: 1rem; line-height: 1; padding: 0 0.3rem;
  }
  .doc-x:hover { color: var(--danger); }
  .empty { color: var(--text-muted); font-size: 0.85rem; padding: 0.3rem 0; }
  .error { color: var(--danger); font-size: 0.85rem; }
</style>

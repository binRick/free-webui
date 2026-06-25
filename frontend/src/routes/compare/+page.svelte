<script lang="ts">
  import { onMount } from 'svelte';
  import Markdown from '$lib/Markdown.svelte';
  import ModelPicker from '$lib/ModelPicker.svelte';
  import { listModels, temporaryChat } from '$lib/api';

  interface Msg {
    role: 'user' | 'assistant';
    content: string;
  }
  interface Column {
    id: number;
    model: string | null;
    messages: Msg[];
    streaming: boolean;
    abort: AbortController | null;
  }

  let allModels = $state<string[]>([]);
  let columns = $state<Column[]>([]);
  let input = $state('');
  let nextId = 1;

  const anyStreaming = $derived(columns.some((c) => c.streaming));

  onMount(async () => {
    allModels = await listModels();
    // Start with two columns (distinct models when possible).
    columns = [makeColumn(allModels[0] ?? null), makeColumn(allModels[1] ?? allModels[0] ?? null)];
  });

  function makeColumn(model: string | null): Column {
    return { id: nextId++, model, messages: [], streaming: false, abort: null };
  }

  function addColumn() {
    if (columns.length >= 4) return;
    columns = [...columns, makeColumn(allModels[0] ?? null)];
  }
  function removeColumn(id: number) {
    const col = columns.find((c) => c.id === id);
    col?.abort?.abort();
    columns = columns.filter((c) => c.id !== id);
  }
  function setModel(id: number, model: string) {
    const col = columns.find((c) => c.id === id);
    if (col) col.model = model;
  }

  async function streamColumn(col: Column) {
    const transcript = col.messages.slice(0, -1).map((m) => ({ role: m.role, content: m.content }));
    col.streaming = true;
    col.abort = new AbortController();
    try {
      await temporaryChat(transcript, col.model, {
        signal: col.abort.signal,
        onDelta: (d) => {
          const last = col.messages[col.messages.length - 1];
          col.messages[col.messages.length - 1] = { ...last, content: last.content + d };
        }
      });
    } catch (e) {
      const last = col.messages[col.messages.length - 1];
      col.messages[col.messages.length - 1] = {
        ...last,
        content: last.content + `\n\n_error: ${(e as Error).message}_`
      };
    } finally {
      col.streaming = false;
      col.abort = null;
    }
  }

  function send() {
    const text = input.trim();
    if (!text || anyStreaming || columns.length === 0) return;
    input = '';
    // Every column gets the same user turn, then streams its own model's reply.
    for (const col of columns) {
      col.messages = [...col.messages, { role: 'user', content: text }, { role: 'assistant', content: '' }];
    }
    for (const col of columns) streamColumn(col); // in parallel
  }

  function stopAll() {
    for (const col of columns) col.abort?.abort();
  }

  function reset() {
    if (anyStreaming) return;
    for (const col of columns) col.messages = [];
  }

  function onKey(e: KeyboardEvent) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      send();
    }
  }
</script>

<svelte:head><title>compare models · free-webui</title></svelte:head>

<div class="compare">
  <header>
    <a class="back" href="/">←</a>
    <span class="title">⚖ compare models</span>
    <span class="hint">one prompt · {columns.length} models · not saved</span>
    <div class="spacer"></div>
    {#if columns.some((c) => c.messages.length)}
      <button class="ghost" onclick={reset} disabled={anyStreaming}>reset</button>
    {/if}
    <button class="ghost" onclick={addColumn} disabled={columns.length >= 4}>+ model</button>
  </header>

  <div class="cols">
    {#each columns as col (col.id)}
      <section class="col">
        <div class="col-head">
          <ModelPicker
            models={allModels}
            value={col.model}
            disabled={col.streaming}
            align="left"
            onSelect={(m) => setModel(col.id, m)}
          />
          {#if columns.length > 1}
            <button class="x" aria-label="remove column" title="remove" onclick={() => removeColumn(col.id)}>×</button>
          {/if}
        </div>
        <div class="col-msgs">
          {#each col.messages as m, i (i)}
            <div class="msg {m.role}">
              {#if m.role === 'assistant'}
                <Markdown source={m.content} reasoning />
              {:else}
                <div class="user-text">{m.content}</div>
              {/if}
            </div>
          {:else}
            <div class="col-empty">—</div>
          {/each}
        </div>
      </section>
    {/each}
  </div>

  <form class="composer" onsubmit={(e) => { e.preventDefault(); send(); }}>
    <textarea
      placeholder="one prompt, sent to every column…"
      bind:value={input}
      onkeydown={onKey}
      rows="2"
      disabled={anyStreaming}
    ></textarea>
    {#if anyStreaming}
      <button type="button" onclick={stopAll}>stop</button>
    {:else}
      <button type="submit" disabled={!input.trim()}>send</button>
    {/if}
  </form>
</div>

<style>
  .compare {
    display: flex;
    flex-direction: column;
    height: 100%;
    min-height: 0;
    width: 100%;
  }
  header {
    display: flex;
    align-items: center;
    gap: 0.6rem;
    padding: 0.75rem 1rem;
    border-bottom: 1px solid var(--border-soft);
  }
  .back {
    color: var(--text-dim);
    text-decoration: none;
    font-size: 1.1rem;
  }
  .back:hover { color: var(--text); }
  .title { font-weight: 600; }
  .hint { color: var(--text-muted); font-size: 0.78rem; }
  .spacer { flex: 1; }
  .ghost {
    background: var(--bg-elev);
    color: var(--text);
    border: 1px solid var(--border);
    border-radius: 6px;
    padding: 0.35rem 0.7rem;
    font: inherit;
    cursor: pointer;
  }
  .ghost:disabled { opacity: 0.5; cursor: default; }
  .cols {
    flex: 1;
    min-height: 0;
    display: flex;
    gap: 1px;
    background: var(--border-soft);
    overflow: hidden;
  }
  .col {
    flex: 1;
    min-width: 0;
    display: flex;
    flex-direction: column;
    background: var(--bg);
    min-height: 0;
  }
  .col-head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 0.4rem;
    padding: 0.5rem 0.6rem;
    border-bottom: 1px solid var(--border-soft);
  }
  .col-head .x {
    background: transparent;
    border: none;
    color: var(--text-dim);
    cursor: pointer;
    font-size: 1rem;
  }
  .col-head .x:hover { color: #d8584a; }
  .col-msgs {
    flex: 1;
    overflow-y: auto;
    padding: 0.75rem;
    display: flex;
    flex-direction: column;
    gap: 0.75rem;
  }
  .msg.user {
    align-self: flex-end;
    max-width: 90%;
    background: color-mix(in srgb, var(--accent) 16%, var(--bg-elev));
    border: 1px solid var(--accent);
    border-radius: 10px;
    padding: 0.35rem 0.6rem;
  }
  .user-text {
    white-space: pre-wrap;
    overflow-wrap: anywhere;
    font-size: 0.9rem;
  }
  .col-empty {
    margin: auto;
    color: var(--text-muted);
  }
  .composer {
    display: flex;
    gap: 0.5rem;
    padding: 0.75rem 1rem 1rem;
    border-top: 1px solid var(--border-soft);
  }
  .composer textarea {
    flex: 1;
    resize: none;
    background: var(--bg-elev);
    color: var(--text);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 0.6rem 0.75rem;
    font: inherit;
  }
  .composer textarea:focus { outline: none; border-color: var(--accent); }
  .composer button {
    background: var(--accent);
    color: #fff;
    border: none;
    border-radius: 8px;
    padding: 0 1.1rem;
    font: inherit;
    cursor: pointer;
  }
  .composer button:disabled { opacity: 0.5; cursor: default; }
</style>

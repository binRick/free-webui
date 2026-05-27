<script lang="ts">
  import { tick } from 'svelte';
  import { page } from '$app/state';
  import {
    editMessage,
    getConversation,
    listModels,
    regenerate,
    sendMessage,
    updateConversation,
    type Role
  } from '$lib/api';
  import { convs } from '$lib/conversations.svelte';
  import Markdown from '$lib/Markdown.svelte';

  interface UIMessage {
    id: number | null;
    role: Role;
    content: string;
  }

  let models = $state<string[]>([]);
  let model = $state<string | null>(null);
  let title = $state('new chat');
  let messages = $state<UIMessage[]>([]);
  let input = $state('');
  let streaming = $state(false);
  let loadingError = $state<string | null>(null);
  let editingIndex = $state<number | null>(null);
  let editText = $state('');
  let settingsOpen = $state(false);
  let systemPrompt = $state('');
  let temperature = $state<string>('');
  let topP = $state<string>('');
  let stopText = $state('');
  let savingSettings = $state(false);
  let abort: AbortController | null = null;
  let scroller: HTMLDivElement;

  let currentId = $derived(page.params.id);

  $effect(() => {
    const id = currentId;
    (async () => {
      if (models.length === 0) models = await listModels();
      if (id) await load(id);
    })();
  });

  async function load(id: string) {
    loadingError = null;
    try {
      const conv = await getConversation(id);
      title = conv.title;
      if (conv.model) model = conv.model;
      else if (models.length && !model) model = models[0];
      messages = conv.messages.map((m) => ({ id: m.id, role: m.role, content: m.content }));
      systemPrompt = conv.system_prompt ?? '';
      temperature = conv.temperature != null ? String(conv.temperature) : '';
      topP = conv.top_p != null ? String(conv.top_p) : '';
      stopText = (conv.stop ?? []).join(', ');
      editingIndex = null;
      await tick();
      scroller?.scrollTo({ top: scroller.scrollHeight });
    } catch (err) {
      loadingError = (err as Error).message;
    }
  }

  function parseNumber(s: string): number | null {
    const t = s.trim();
    if (!t) return null;
    const n = Number(t);
    return Number.isFinite(n) ? n : null;
  }

  function parseStop(s: string): string[] | null {
    const parts = s.split(',').map((x) => x.trim()).filter(Boolean);
    return parts.length ? parts : null;
  }

  async function saveSettings() {
    if (savingSettings) return;
    savingSettings = true;
    try {
      await updateConversation(currentId, {
        system_prompt: systemPrompt.trim() || null,
        temperature: parseNumber(temperature),
        top_p: parseNumber(topP),
        stop: parseStop(stopText)
      });
      settingsOpen = false;
    } finally {
      savingSettings = false;
    }
  }

  function appendDelta(delta: string) {
    const last = messages[messages.length - 1];
    messages[messages.length - 1] = { ...last, content: last.content + delta };
    scroller?.scrollTo({ top: scroller.scrollHeight });
  }

  async function runStream(operation: (opts: { signal: AbortSignal; onDelta: (d: string) => void }) => Promise<void>) {
    streaming = true;
    abort = new AbortController();
    try {
      await operation({ signal: abort.signal, onDelta: appendDelta });
      await load(currentId);
    } catch (err) {
      const last = messages[messages.length - 1];
      if (last) {
        messages[messages.length - 1] = {
          ...last,
          content: last.content + `\n\n_error: ${(err as Error).message}_`
        };
      }
    } finally {
      streaming = false;
      abort = null;
      convs.refresh();
    }
  }

  async function send() {
    const text = input.trim();
    if (!text || streaming) return;
    input = '';
    messages = [
      ...messages,
      { id: null, role: 'user', content: text },
      { id: null, role: 'assistant', content: '' }
    ];
    await tick();
    scroller?.scrollTo({ top: scroller.scrollHeight });
    await runStream((opts) => sendMessage(currentId, text, model, opts));
  }

  async function regen() {
    if (streaming) return;
    const last = messages[messages.length - 1];
    if (!last || last.role !== 'assistant') return;
    messages[messages.length - 1] = { ...last, content: '' };
    await tick();
    await runStream((opts) => regenerate(currentId, model, opts));
  }

  function startEdit(i: number) {
    if (streaming) return;
    editingIndex = i;
    editText = messages[i].content;
  }

  function cancelEdit() {
    editingIndex = null;
    editText = '';
  }

  async function saveEdit() {
    if (editingIndex === null || streaming) return;
    const i = editingIndex;
    const msg = messages[i];
    if (msg.id == null) return;
    const newContent = editText.trim();
    if (!newContent) return;

    editingIndex = null;
    messages = [
      ...messages.slice(0, i),
      { ...msg, content: newContent },
      { id: null, role: 'assistant', content: '' }
    ];
    await tick();
    scroller?.scrollTo({ top: scroller.scrollHeight });
    await runStream((opts) => editMessage(currentId, msg.id!, newContent, model, opts));
  }

  function stop() {
    abort?.abort();
  }

  function onKey(e: KeyboardEvent) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      send();
    }
  }
</script>

<header>
  <div class="title">{title}</div>
  <div class="header-controls">
    <select bind:value={model} disabled={streaming}>
      {#if models.length === 0}
        <option value={null}>no models</option>
      {/if}
      {#each models as m}
        <option value={m}>{m}</option>
      {/each}
    </select>
    <button
      class="settings-toggle"
      class:active={settingsOpen}
      aria-label="chat settings"
      onclick={() => (settingsOpen = !settingsOpen)}
    >⚙</button>
  </div>
</header>

{#if settingsOpen}
  <section class="settings">
    <label>
      <span class="lbl">system prompt</span>
      <textarea
        bind:value={systemPrompt}
        rows="3"
        placeholder="e.g. you are a terse senior engineer"
      ></textarea>
    </label>
    <div class="row">
      <label class="num">
        <span class="lbl">temperature</span>
        <input type="number" min="0" max="2" step="0.1" bind:value={temperature} placeholder="default" />
      </label>
      <label class="num">
        <span class="lbl">top-p</span>
        <input type="number" min="0" max="1" step="0.05" bind:value={topP} placeholder="default" />
      </label>
    </div>
    <label>
      <span class="lbl">stop sequences <span class="hint">comma-separated</span></span>
      <input type="text" bind:value={stopText} placeholder="e.g. ###, END" />
    </label>
    <div class="settings-actions">
      <button class="action" onclick={() => (settingsOpen = false)}>close</button>
      <button class="action primary" onclick={saveSettings} disabled={savingSettings}>
        {savingSettings ? 'saving…' : 'save'}
      </button>
    </div>
  </section>
{/if}

<div class="scroller" bind:this={scroller}>
  {#if loadingError}
    <div class="empty err">couldn't load: {loadingError}</div>
  {:else if messages.length === 0}
    <div class="empty">start a conversation ↓</div>
  {/if}
  {#each messages as msg, i (msg.id ?? `tmp-${i}`)}
    <div class="msg {msg.role}">
      <div class="role-row">
        <span class="role">{msg.role}</span>
        {#if !streaming && editingIndex === null}
          <div class="actions">
            {#if msg.role === 'user' && msg.id != null}
              <button class="action" onclick={() => startEdit(i)}>edit</button>
            {/if}
            {#if msg.role === 'assistant' && i === messages.length - 1 && msg.content}
              <button class="action" onclick={regen}>regenerate</button>
            {/if}
          </div>
        {/if}
      </div>
      <div class="content">
        {#if editingIndex === i}
          <textarea class="edit" bind:value={editText} rows="4"></textarea>
          <div class="edit-actions">
            <button class="action" onclick={cancelEdit}>cancel</button>
            <button class="action primary" onclick={saveEdit} disabled={!editText.trim()}>save &amp; rerun</button>
          </div>
        {:else}
          <Markdown source={msg.content} />
        {/if}
      </div>
    </div>
  {/each}
</div>

<form class="composer" onsubmit={(e) => { e.preventDefault(); send(); }}>
  <textarea
    placeholder="message…"
    bind:value={input}
    onkeydown={onKey}
    rows="2"
    disabled={streaming || editingIndex !== null}
  ></textarea>
  {#if streaming}
    <button type="button" onclick={stop}>stop</button>
  {:else}
    <button type="submit" disabled={!input.trim() || editingIndex !== null}>send</button>
  {/if}
</form>

<style>
  header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 1rem;
    padding: 0.75rem 1rem;
    border-bottom: 1px solid #1e293b;
  }
  .title {
    color: #cbd5e1;
    font-size: 0.95rem;
    overflow: hidden;
    white-space: nowrap;
    text-overflow: ellipsis;
  }
  .header-controls {
    display: flex;
    gap: 0.5rem;
    align-items: center;
  }
  .settings-toggle {
    padding: 0.4rem 0.6rem;
    font-size: 1rem;
    line-height: 1;
  }
  .settings-toggle.active {
    background: #1e293b;
    border-color: #475569;
    color: #fff;
  }
  .settings {
    border-bottom: 1px solid #1e293b;
    background: #07091a;
    padding: 0.85rem 1rem;
    display: flex;
    flex-direction: column;
    gap: 0.65rem;
    max-width: 760px;
    width: calc(100% - 2rem);
    margin: 0 auto;
  }
  .settings label {
    display: flex;
    flex-direction: column;
    gap: 0.3rem;
  }
  .settings .lbl {
    font-size: 0.72rem;
    text-transform: uppercase;
    color: #64748b;
    letter-spacing: 0.05em;
  }
  .settings .hint {
    text-transform: none;
    color: #475569;
    letter-spacing: 0;
    margin-left: 0.4rem;
  }
  .settings input,
  .settings textarea {
    padding: 0.4rem 0.6rem;
    font-size: 0.9rem;
  }
  .settings textarea { resize: vertical; min-height: 4.5rem; }
  .settings .row { display: flex; gap: 0.75rem; }
  .settings .num { flex: 1; }
  .settings-actions {
    display: flex;
    justify-content: flex-end;
    gap: 0.4rem;
    margin-top: 0.25rem;
  }
  select,
  button,
  textarea {
    background: #0f172a;
    color: #e2e8f0;
    border: 1px solid #334155;
    border-radius: 6px;
    padding: 0.5rem 0.75rem;
    font: inherit;
  }
  button { cursor: pointer; }
  button:disabled { opacity: 0.5; cursor: not-allowed; }
  .scroller {
    flex: 1;
    overflow-y: auto;
    padding: 1rem;
    display: flex;
    flex-direction: column;
    gap: 1rem;
  }
  .empty {
    color: #64748b;
    text-align: center;
    margin-top: 4rem;
  }
  .empty.err { color: #ef4444; }
  .msg {
    max-width: 760px;
    width: 100%;
    margin: 0 auto;
  }
  .role-row {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 0.5rem;
    margin-bottom: 0.25rem;
    min-height: 1.2rem;
  }
  .role {
    font-size: 0.75rem;
    text-transform: uppercase;
    color: #64748b;
  }
  .msg.user .role { color: #22d3ee; }
  .msg.assistant .role { color: #a78bfa; }
  .actions {
    display: flex;
    gap: 0.35rem;
    opacity: 0;
    transition: opacity 0.15s;
  }
  .msg:hover .actions { opacity: 1; }
  .action {
    padding: 0.15rem 0.5rem;
    font-size: 0.72rem;
    border-radius: 4px;
    background: transparent;
    border: 1px solid #334155;
    color: #94a3b8;
  }
  .action:hover { color: #fff; background: #1e293b; }
  .action.primary { background: #1e293b; color: #e2e8f0; }
  .action.primary:hover { background: #334155; }
  .content { line-height: 1.5; word-wrap: break-word; }
  .edit {
    width: 100%;
    resize: vertical;
    min-height: 4rem;
    font: inherit;
  }
  .edit-actions {
    display: flex;
    justify-content: flex-end;
    gap: 0.35rem;
    margin-top: 0.5rem;
  }
  .composer {
    display: flex;
    gap: 0.5rem;
    padding: 1rem;
    border-top: 1px solid #1e293b;
    max-width: 760px;
    margin: 0 auto;
    width: calc(100% - 2rem);
  }
  textarea {
    flex: 1;
    resize: vertical;
    min-height: 2.5rem;
    max-height: 12rem;
  }
</style>

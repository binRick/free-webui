<script lang="ts">
  import { tick } from 'svelte';
  import { page } from '$app/state';
  import {
    createPreset,
    createPrompt,
    deleteDocument,
    deletePreset,
    deletePrompt,
    editMessage,
    exportConversationUrl,
    getConversation,
    getWebSearchStatus,
    listDocuments,
    listModels,
    listPresets,
    listPrompts,
    parseContent,
    regenerate,
    sendMessage,
    updateConversation,
    uploadDocument,
    type ContentPart,
    type Document,
    type MessageContent,
    type Preset,
    type Prompt,
    type Role
  } from '$lib/api';
  import { convs } from '$lib/conversations.svelte';
  import Markdown from '$lib/Markdown.svelte';
  import { sidebar } from '$lib/sidebarState.svelte';

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
  let pendingImages = $state<string[]>([]);
  let streaming = $state(false);
  let loadingError = $state<string | null>(null);
  let editingIndex = $state<number | null>(null);
  let editText = $state('');
  let fileInput: HTMLInputElement;
  let settingsOpen = $state(false);
  let systemPrompt = $state('');
  let temperature = $state<string>('');
  let topP = $state<string>('');
  let stopText = $state('');
  let savingSettings = $state(false);
  let docs = $state<Document[]>([]);
  let docUploading = $state(false);
  let docError = $state<string | null>(null);
  let docInput: HTMLInputElement;
  let prompts = $state<Prompt[]>([]);
  let presets = $state<Preset[]>([]);
  let webSearch = $state(false);
  let webSearchAvailable = $state(false);
  let recognising = $state(false);
  let speakingIdx = $state<number | null>(null);
  let recognition: any = null;
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
      docs = await listDocuments(id);
      prompts = await listPrompts();
      presets = await listPresets();
      webSearch = !!conv.web_search;
      webSearchAvailable = (await getWebSearchStatus()).available;
      await tick();
      scroller?.scrollTo({ top: scroller.scrollHeight });
    } catch (err) {
      loadingError = (err as Error).message;
    }
  }

  async function onDocPick(e: Event) {
    const target = e.target as HTMLInputElement;
    if (!target.files || target.files.length === 0) return;
    docUploading = true;
    docError = null;
    try {
      for (const f of Array.from(target.files)) {
        await uploadDocument(currentId, f);
      }
      docs = await listDocuments(currentId);
    } catch (err) {
      docError = (err as Error).message;
    } finally {
      target.value = '';
      docUploading = false;
    }
  }

  async function removeDoc(id: number) {
    if (!confirm('remove this document?')) return;
    await deleteDocument(currentId, id);
    docs = await listDocuments(currentId);
  }

  function formatBytes(n: number): string {
    if (n < 1024) return `${n} B`;
    if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
    return `${(n / 1024 / 1024).toFixed(1)} MB`;
  }

  async function savePromptFromInput() {
    const content = input.trim();
    if (!content) return;
    const title = window.prompt('save as prompt — title?');
    if (!title) return;
    await createPrompt(title, content);
    prompts = await listPrompts();
  }

  function insertPrompt(p: Prompt) {
    input = input ? `${input}\n${p.content}` : p.content;
  }

  async function removePrompt(id: number) {
    if (!confirm('delete this saved prompt?')) return;
    await deletePrompt(id);
    prompts = await listPrompts();
  }

  async function saveCurrentAsPreset() {
    const name = window.prompt('save preset — name?');
    if (!name) return;
    await createPreset({
      name,
      model,
      system_prompt: systemPrompt.trim() || null,
      temperature: parseNumber(temperature),
      top_p: parseNumber(topP),
      stop: parseStop(stopText)
    });
    presets = await listPresets();
  }

  async function applyPreset(p: Preset) {
    if (p.model) model = p.model;
    systemPrompt = p.system_prompt ?? '';
    temperature = p.temperature != null ? String(p.temperature) : '';
    topP = p.top_p != null ? String(p.top_p) : '';
    stopText = (p.stop ?? []).join(', ');
    await updateConversation(currentId, {
      model: p.model ?? model,
      system_prompt: p.system_prompt ?? null,
      temperature: p.temperature,
      top_p: p.top_p,
      stop: p.stop
    });
  }

  async function removePreset(id: number) {
    if (!confirm('delete this preset?')) return;
    await deletePreset(id);
    presets = await listPresets();
  }

  function toggleMic() {
    const SR =
      (globalThis as any).SpeechRecognition ?? (globalThis as any).webkitSpeechRecognition;
    if (!SR) {
      alert('this browser does not support speech recognition (try Chrome)');
      return;
    }
    if (recognising && recognition) {
      recognition.stop();
      return;
    }
    recognition = new SR();
    recognition.continuous = false;
    recognition.interimResults = true;
    recognition.lang = navigator.language || 'en-US';
    recognising = true;
    let finalText = '';
    recognition.onresult = (e: any) => {
      let interim = '';
      for (let i = e.resultIndex; i < e.results.length; i++) {
        const t = e.results[i][0].transcript;
        if (e.results[i].isFinal) finalText += t;
        else interim += t;
      }
      input = (input ? input + ' ' : '') + (finalText + interim).trim();
    };
    recognition.onend = () => {
      recognising = false;
      recognition = null;
    };
    recognition.onerror = () => {
      recognising = false;
      recognition = null;
    };
    recognition.start();
  }

  function speakMessage(idx: number, text: string) {
    if (!('speechSynthesis' in window)) {
      alert('this browser does not support speech synthesis');
      return;
    }
    if (speakingIdx === idx) {
      speechSynthesis.cancel();
      speakingIdx = null;
      return;
    }
    speechSynthesis.cancel();
    const u = new SpeechSynthesisUtterance(text);
    u.onend = () => { if (speakingIdx === idx) speakingIdx = null; };
    u.onerror = () => { if (speakingIdx === idx) speakingIdx = null; };
    speakingIdx = idx;
    speechSynthesis.speak(u);
  }

  function messagePlainText(content: string): string {
    const parsed = parseContent(content);
    if (typeof parsed === 'string') return parsed;
    return parsed
      .filter((p) => p.type === 'text')
      .map((p) => (p as { text: string }).text)
      .join(' ');
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
        stop: parseStop(stopText),
        web_search: webSearch
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

  function buildOutgoing(text: string, images: string[]): MessageContent {
    if (images.length === 0) return text;
    const parts: ContentPart[] = [];
    if (text) parts.push({ type: 'text', text });
    for (const url of images) parts.push({ type: 'image_url', image_url: { url } });
    return parts;
  }

  function serializeForLocal(text: string, images: string[]): string {
    const content = buildOutgoing(text, images);
    return typeof content === 'string' ? content : JSON.stringify(content);
  }

  async function send() {
    const text = input.trim();
    if ((!text && pendingImages.length === 0) || streaming) return;
    const outgoing = buildOutgoing(text, pendingImages);
    const localContent = serializeForLocal(text, pendingImages);
    input = '';
    pendingImages = [];
    messages = [
      ...messages,
      { id: null, role: 'user', content: localContent },
      { id: null, role: 'assistant', content: '' }
    ];
    await tick();
    scroller?.scrollTo({ top: scroller.scrollHeight });
    await runStream((opts) => sendMessage(currentId, outgoing, model, opts));
  }

  async function filesToDataUrls(files: FileList | File[]): Promise<string[]> {
    const out: string[] = [];
    for (const f of Array.from(files)) {
      if (!f.type.startsWith('image/')) continue;
      out.push(await new Promise<string>((resolve, reject) => {
        const r = new FileReader();
        r.onload = () => resolve(String(r.result));
        r.onerror = () => reject(r.error ?? new Error('read failed'));
        r.readAsDataURL(f);
      }));
    }
    return out;
  }

  async function onFilePick(e: Event) {
    const target = e.target as HTMLInputElement;
    if (!target.files) return;
    pendingImages = [...pendingImages, ...(await filesToDataUrls(target.files))];
    target.value = '';
  }

  async function onPaste(e: ClipboardEvent) {
    const items = e.clipboardData?.items;
    if (!items) return;
    const imgs: File[] = [];
    for (const item of items) {
      if (item.kind === 'file' && item.type.startsWith('image/')) {
        const f = item.getAsFile();
        if (f) imgs.push(f);
      }
    }
    if (imgs.length) {
      e.preventDefault();
      pendingImages = [...pendingImages, ...(await filesToDataUrls(imgs))];
    }
  }

  async function onDrop(e: DragEvent) {
    if (!e.dataTransfer?.files?.length) return;
    e.preventDefault();
    pendingImages = [...pendingImages, ...(await filesToDataUrls(e.dataTransfer.files))];
  }

  function removeImage(i: number) {
    pendingImages = pendingImages.filter((_, idx) => idx !== i);
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
  <button class="hamburger" aria-label="open sidebar" onclick={() => sidebar.toggle()}>☰</button>
  <div class="title">
    {title}
    {#if docs.length > 0}
      <span class="rag-badge" title="{docs.length} document{docs.length === 1 ? '' : 's'} attached — RAG active">
        📎 {docs.length}
      </span>
    {/if}
    {#if webSearch}
      <span class="rag-badge web" title="web search active for this chat">🌐 web</span>
    {/if}
  </div>
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
    <label class="toggle">
      <input type="checkbox" bind:checked={webSearch} disabled={!webSearchAvailable} />
      <span class="lbl" style="text-transform: none; letter-spacing: 0;">
        web search
        {#if !webSearchAvailable}
          <span class="hint">— set <code>FREE_WEBUI_SEARXNG_URL</code> on the backend to enable</span>
        {/if}
      </span>
    </label>
    <div class="settings-actions">
      <button class="action" onclick={() => (settingsOpen = false)}>close</button>
      <button class="action primary" onclick={saveSettings} disabled={savingSettings}>
        {savingSettings ? 'saving…' : 'save'}
      </button>
    </div>

    <div class="docs">
      <div class="docs-head">
        <span class="lbl">presets ("modelfiles")</span>
        <button class="action" type="button" onclick={saveCurrentAsPreset}>+ save current</button>
      </div>
      {#if presets.length === 0}
        <div class="doc-empty">no presets — save the current model + system prompt + params as a named bundle</div>
      {:else}
        <ul class="doc-list">
          {#each presets as p (p.id)}
            <li>
              <button class="prompt-pick" type="button" onclick={() => applyPreset(p)} title="{p.model ?? 'any model'} · {p.system_prompt ?? 'no system prompt'}">
                {p.name}
              </button>
              <span class="doc-meta">{p.model ?? '—'}</span>
              <button class="doc-x" aria-label="delete" onclick={() => removePreset(p.id)}>×</button>
            </li>
          {/each}
        </ul>
      {/if}
    </div>

    <div class="docs">
      <div class="docs-head">
        <span class="lbl">prompts</span>
        <button class="action" type="button" onclick={savePromptFromInput} disabled={!input.trim()}>
          + save current
        </button>
      </div>
      {#if prompts.length === 0}
        <div class="doc-empty">no saved prompts — type something then click "save current"</div>
      {:else}
        <ul class="doc-list">
          {#each prompts as p (p.id)}
            <li>
              <button class="prompt-pick" type="button" onclick={() => insertPrompt(p)} title={p.content}>
                {p.title}
              </button>
              <button class="doc-x" aria-label="delete" onclick={() => removePrompt(p.id)}>×</button>
            </li>
          {/each}
        </ul>
      {/if}
    </div>

    <div class="docs">
      <div class="docs-head">
        <span class="lbl">export this chat</span>
        <div style="display: flex; gap: 0.35rem;">
          <a class="action" href={exportConversationUrl(currentId, 'json')} download>↓ json</a>
          <a class="action" href={exportConversationUrl(currentId, 'md')} download>↓ markdown</a>
        </div>
      </div>
    </div>

    <div class="docs">
      <div class="docs-head">
        <span class="lbl">documents (rag)</span>
        <input
          bind:this={docInput}
          type="file"
          accept=".txt,.md,.pdf,.py,.ts,.tsx,.js,.jsx,.svelte,.go,.rs,.java,.json,.yaml,.yml,.toml,.csv,.html,.css,.sql"
          multiple
          hidden
          onchange={onDocPick}
        />
        <button
          class="action"
          type="button"
          onclick={() => docInput.click()}
          disabled={docUploading}
        >{docUploading ? 'uploading…' : '+ upload'}</button>
      </div>
      {#if docError}<div class="doc-err">{docError}</div>{/if}
      {#if docs.length === 0}
        <div class="doc-empty">no documents — upload .txt, .md, .pdf or a code file to ground replies in its contents</div>
      {:else}
        <ul class="doc-list">
          {#each docs as d (d.id)}
            <li>
              <span class="doc-name" title={d.filename}>{d.filename}</span>
              <span class="doc-meta">{d.chunk_count} chunks · {formatBytes(d.bytes)}</span>
              <button class="doc-x" aria-label="remove" onclick={() => removeDoc(d.id)}>×</button>
            </li>
          {/each}
        </ul>
      {/if}
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
            {#if msg.role === 'assistant' && msg.content}
              <button class="action" onclick={() => speakMessage(i, messagePlainText(msg.content))}>
                {speakingIdx === i ? '⏹ stop' : '🔊 speak'}
              </button>
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
          {@const parsed = parseContent(msg.content)}
          {#if typeof parsed === 'string'}
            <Markdown source={parsed} />
          {:else}
            {#each parsed as part}
              {#if part.type === 'text'}
                <Markdown source={part.text} />
              {:else if part.type === 'image_url'}
                <img class="attached" src={part.image_url.url} alt="attachment" />
              {/if}
            {/each}
          {/if}
        {/if}
      </div>
    </div>
  {/each}
</div>

<form
  class="composer"
  ondragover={(e) => e.preventDefault()}
  ondrop={onDrop}
  onsubmit={(e) => { e.preventDefault(); send(); }}
>
  {#if pendingImages.length}
    <div class="pending">
      {#each pendingImages as src, i (src)}
        <div class="thumb">
          <img src={src} alt="pending {i + 1}" />
          <button type="button" class="thumb-x" aria-label="remove" onclick={() => removeImage(i)}>×</button>
        </div>
      {/each}
    </div>
  {/if}
  <div class="row">
    <input
      bind:this={fileInput}
      type="file"
      accept="image/*"
      multiple
      hidden
      onchange={onFilePick}
    />
    <button
      type="button"
      class="attach"
      aria-label="attach image"
      onclick={() => fileInput.click()}
      disabled={streaming || editingIndex !== null}
    >📎</button>
    <button
      type="button"
      class="attach mic"
      class:active={recognising}
      aria-label="voice input"
      onclick={toggleMic}
      disabled={streaming || editingIndex !== null}
    >🎤</button>
    <textarea
      placeholder="message…  (paste / drop images, or click 📎)"
      bind:value={input}
      onkeydown={onKey}
      onpaste={onPaste}
      rows="2"
      disabled={streaming || editingIndex !== null}
    ></textarea>
    {#if streaming}
      <button type="button" onclick={stop}>stop</button>
    {:else}
      <button
        type="submit"
        disabled={(!input.trim() && pendingImages.length === 0) || editingIndex !== null}
      >send</button>
    {/if}
  </div>
</form>

<style>
  header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 1rem;
    padding: 0.75rem 1rem;
    border-bottom: 1px solid var(--border-soft);
  }
  .hamburger {
    display: none;
    background: transparent;
    border: 0;
    color: var(--text);
    font-size: 1.4rem;
    line-height: 1;
    padding: 0.25rem 0.5rem;
    cursor: pointer;
  }
  .title {
    flex: 1;
    color: var(--text);
    font-size: 0.95rem;
    overflow: hidden;
    white-space: nowrap;
    text-overflow: ellipsis;
    display: flex;
    align-items: center;
    gap: 0.5rem;
    min-width: 0;
  }
  .rag-badge {
    flex-shrink: 0;
    background: color-mix(in srgb, var(--accent-2) 18%, transparent);
    color: var(--accent-2);
    border: 1px solid color-mix(in srgb, var(--accent-2) 40%, transparent);
    padding: 0.1rem 0.5rem;
    border-radius: 999px;
    font-size: 0.7rem;
  }
  .rag-badge.web {
    background: color-mix(in srgb, var(--accent) 18%, transparent);
    color: var(--accent);
    border-color: color-mix(in srgb, var(--accent) 40%, transparent);
  }
  .toggle {
    display: flex !important;
    flex-direction: row !important;
    align-items: center;
    gap: 0.5rem !important;
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
    background: var(--bg-hover);
    border-color: var(--text-faint);
    color: var(--text);
  }
  .settings {
    border-bottom: 1px solid var(--border-soft);
    background: var(--bg-sidebar);
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
    color: var(--text-muted);
    letter-spacing: 0.05em;
  }
  .settings .hint {
    text-transform: none;
    color: var(--text-faint);
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
  .docs {
    border-top: 1px solid var(--border-soft);
    padding-top: 0.65rem;
    margin-top: 0.25rem;
    display: flex;
    flex-direction: column;
    gap: 0.5rem;
  }
  .docs-head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 0.5rem;
  }
  .doc-err {
    color: var(--danger);
    background: color-mix(in srgb, var(--danger) 12%, transparent);
    padding: 0.35rem 0.55rem;
    border-radius: 4px;
    font-size: 0.8rem;
  }
  .doc-empty {
    color: var(--text-muted);
    font-size: 0.8rem;
    padding: 0.25rem 0;
  }
  .doc-list {
    list-style: none;
    padding: 0;
    margin: 0;
    display: flex;
    flex-direction: column;
    gap: 0.3rem;
  }
  .doc-list li {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    padding: 0.3rem 0.5rem;
    background: var(--bg-elev);
    border: 1px solid var(--border-soft);
    border-radius: 6px;
    font-size: 0.82rem;
  }
  .doc-name {
    flex: 1;
    overflow: hidden;
    white-space: nowrap;
    text-overflow: ellipsis;
    color: var(--text);
  }
  .doc-meta {
    color: var(--text-muted);
    font-size: 0.72rem;
    white-space: nowrap;
  }
  .doc-x {
    background: transparent;
    border: 0;
    color: var(--text-muted);
    cursor: pointer;
    font-size: 1rem;
    line-height: 1;
    padding: 0 0.35rem;
    border-radius: 4px;
  }
  .doc-x:hover { color: var(--danger); background: color-mix(in srgb, var(--danger) 10%, transparent); }
  .prompt-pick {
    flex: 1;
    text-align: left;
    background: transparent;
    border: 0;
    color: var(--text);
    cursor: pointer;
    padding: 0;
    font: inherit;
    overflow: hidden;
    white-space: nowrap;
    text-overflow: ellipsis;
  }
  .prompt-pick:hover { color: var(--accent); }
  a.action {
    text-decoration: none;
    color: var(--text-dim);
  }
  a.action:hover { color: var(--text); background: var(--bg-hover); }
  select,
  button,
  textarea,
  input {
    background: var(--bg-elev);
    color: var(--text);
    border: 1px solid var(--border);
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
    color: var(--text-muted);
    text-align: center;
    margin-top: 4rem;
  }
  .empty.err { color: var(--danger); }
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
    color: var(--text-muted);
  }
  .msg.user .role { color: var(--accent); }
  .msg.assistant .role { color: var(--accent-2); }
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
    border: 1px solid var(--border);
    color: var(--text-dim);
  }
  .action:hover { color: var(--text); background: var(--bg-hover); }
  .action.primary { background: var(--bg-hover); color: var(--text); }
  .action.primary:hover { background: var(--border); }
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
    flex-direction: column;
    gap: 0.5rem;
    padding: 1rem;
    border-top: 1px solid var(--border-soft);
    max-width: 760px;
    margin: 0 auto;
    width: calc(100% - 2rem);
  }
  .composer .row { display: flex; gap: 0.5rem; align-items: stretch; }
  .pending {
    display: flex;
    flex-wrap: wrap;
    gap: 0.5rem;
  }
  .thumb {
    position: relative;
    width: 72px;
    height: 72px;
    border-radius: 6px;
    overflow: hidden;
    border: 1px solid var(--border);
    background: var(--bg-elev);
  }
  .thumb img { width: 100%; height: 100%; object-fit: cover; display: block; }
  .thumb-x {
    position: absolute;
    top: 2px;
    right: 2px;
    width: 20px;
    height: 20px;
    padding: 0;
    border: 0;
    border-radius: 999px;
    background: rgba(0, 0, 0, 0.7);
    color: #fff;
    cursor: pointer;
    font-size: 0.85rem;
    line-height: 18px;
  }
  .attach {
    padding: 0.5rem 0.75rem;
    font-size: 1.1rem;
    line-height: 1;
  }
  .mic.active {
    background: color-mix(in srgb, var(--danger) 30%, var(--bg-elev));
    border-color: var(--danger);
    color: #fff;
    animation: mic-pulse 1.1s ease-in-out infinite;
  }
  @keyframes mic-pulse {
    0%, 100% { box-shadow: 0 0 0 0 color-mix(in srgb, var(--danger) 60%, transparent); }
    50%      { box-shadow: 0 0 0 6px color-mix(in srgb, var(--danger) 0%, transparent); }
  }
  .attached {
    display: block;
    max-width: 100%;
    max-height: 320px;
    border-radius: 6px;
    margin: 0.5rem 0;
  }
  textarea {
    flex: 1;
    resize: vertical;
    min-height: 2.5rem;
    max-height: 12rem;
  }

  @media (max-width: 768px) {
    .hamburger { display: block; }
    .composer { padding: 0.75rem; width: calc(100% - 1.5rem); }
    .scroller { padding: 0.75rem; }
  }
</style>

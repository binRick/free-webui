<script lang="ts">
  import { tick } from 'svelte';
  import { page } from '$app/state';
  import {
    activateVariant,
    autotitle,
    createMemory,
    createPreset,
    createPrompt,
    deleteDocument,
    deleteMemory,
    deletePreset,
    deletePrompt,
    continueMessage,
    deleteMessage,
    editMessage,
    exportConversationUrl,
    getCodeStatus,
    getConversation,
    getImageStatus,
    getWebSearchStatus,
    createShare,
    deleteShare,
    getConversationCollections,
    getConversationTags,
    getFollowups,
    getShareToken,
    listFolders,
    setConversationTags,
    type Folder,
    listCollections,
    listDocuments,
    listMemories,
    listModels,
    listVariants,
    setConversationCollections,
    type Collection,
    listPresets,
    updatePreset,
    listPrompts,
    parseContent,
    regenerateMessage,
    sendMessage,
    setFeedback,
    updateConversation,
    uploadDocument,
    type ContentPart,
    type Document,
    type Memory,
    type MessageContent,
    type MessageVariant,
    type Preset,
    type Prompt,
    type Role,
    type Source,
    type ToolCallEvent
  } from '$lib/api';
  import { convs } from '$lib/conversations.svelte';
  import Markdown from '$lib/Markdown.svelte';
  import ModelPicker from '$lib/ModelPicker.svelte';
  import { sidebar } from '$lib/sidebarState.svelte';

  interface UIMessage {
    id: number | null;
    role: Role;
    content: string;
    tool_calls?: ToolCallEvent[];
    images?: string[];
    rating?: number | null;
    sources?: Source[];
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
  let maxTokens = $state<string>('');
  let presencePenalty = $state<string>('');
  let frequencyPenalty = $state<string>('');
  let seed = $state<string>('');
  let tagsText = $state('');
  let folders = $state<Folder[]>([]);
  let folderId = $state<number | null>(null);
  let savingSettings = $state(false);
  let docs = $state<Document[]>([]);
  let docUploading = $state(false);
  let collections = $state<Collection[]>([]);
  let attachedCollections = $state<Set<number>>(new Set());
  let shareToken = $state<string | null>(null);
  let shareCopied = $state(false);
  let followups = $state<string[]>([]);
  let docError = $state<string | null>(null);
  let docInput: HTMLInputElement;
  let prompts = $state<Prompt[]>([]);
  let presets = $state<Preset[]>([]);
  let memories = $state<Memory[]>([]);
  let webSearch = $state(false);
  let webSearchAvailable = $state(false);
  let toolsEnabled = $state(false);
  let imageGenAvailable = $state(false);
  let codeInterpreterAvailable = $state(false);

  let builtinTools = $derived(
    ['now', 'calculate', ...(imageGenAvailable ? ['imagine'] : []), ...(codeInterpreterAvailable ? ['run_python'] : [])]
  );
  let recognising = $state(false);
  let speakingIdx = $state<number | null>(null);
  let recognition: any = null;
  let abort: AbortController | null = null;
  let scroller: HTMLDivElement;

  // This route is /chat/[id], so the param is always present here.
  let currentId = $derived(page.params.id!);

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
      messages = conv.messages.map((m) => ({
        id: m.id,
        role: m.role,
        content: m.content,
        rating: m.rating ?? null,
        sources: m.sources ?? undefined
      }));
      await refreshVariants();
      systemPrompt = conv.system_prompt ?? '';
      temperature = conv.temperature != null ? String(conv.temperature) : '';
      topP = conv.top_p != null ? String(conv.top_p) : '';
      stopText = (conv.stop ?? []).join(', ');
      maxTokens = conv.max_tokens != null ? String(conv.max_tokens) : '';
      presencePenalty = conv.presence_penalty != null ? String(conv.presence_penalty) : '';
      frequencyPenalty = conv.frequency_penalty != null ? String(conv.frequency_penalty) : '';
      seed = conv.seed != null ? String(conv.seed) : '';
      editingIndex = null;
      docs = await listDocuments(id);
      collections = await listCollections();
      attachedCollections = new Set(await getConversationCollections(id));
      tagsText = (await getConversationTags(id)).join(', ');
      folders = await listFolders();
      folderId = conv.folder_id ?? null;
      shareToken = await getShareToken(id);
      prompts = await listPrompts();
      presets = await listPresets();
      memories = await listMemories();
      webSearch = !!conv.web_search;
      webSearchAvailable = (await getWebSearchStatus()).available;
      toolsEnabled = !!conv.tools_enabled;
      imageGenAvailable = (await getImageStatus()).available;
      codeInterpreterAvailable = (await getCodeStatus()).available;
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

  async function toggleCollection(id: number) {
    const next = new Set(attachedCollections);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    attachedCollections = new Set(await setConversationCollections(currentId, [...next]));
  }

  function shareUrl(token: string): string {
    return `${window.location.origin}/shared/${token}`;
  }
  async function makeShare() {
    try {
      shareToken = await createShare(currentId);
    } catch {
      /* sharing disabled / failed */
    }
  }
  async function revokeShare() {
    await deleteShare(currentId);
    shareToken = null;
  }
  async function copyShare() {
    if (!shareToken) return;
    try {
      await navigator.clipboard.writeText(shareUrl(shareToken));
      shareCopied = true;
      setTimeout(() => (shareCopied = false), 1200);
    } catch {
      /* clipboard unavailable */
    }
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

  // ---- prompt variables ----
  // Saved prompts may embed {{...}} placeholders. System variables ({{date}},
  // {{time}}, {{datetime}}) resolve automatically; any other {{name}} is a
  // custom input the user fills in via a small form before the prompt is used.
  const SYSTEM_VARS = ['date', 'time', 'datetime'];
  // A single {{ token }}: capture the inner spec, no nested braces. The name is
  // length-bounded and has NO surrounding \s* group — overlapping \s* and
  // [^{}]+? over a whitespace run caused O(n^2) backtracking (a tab-freezing
  // ReDoS) on an unclosed "{{". Callers .trim() the captured group instead.
  const VAR_RE = /\{\{([^{}]{1,200}?)\}\}/g;

  function resolveSystemVars(text: string): string {
    const now = new Date();
    return text.replace(VAR_RE, (whole, inner) => {
      switch (String(inner).trim().toLowerCase()) {
        case 'date':
          return now.toLocaleDateString();
        case 'time':
          return now.toLocaleTimeString();
        case 'datetime':
          return now.toLocaleString();
        default:
          return whole;
      }
    });
  }

  function extractCustomVars(text: string): string[] {
    const seen = new Set<string>();
    const out: string[] = [];
    for (const m of text.matchAll(VAR_RE)) {
      const name = m[1].trim();
      if (!name || SYSTEM_VARS.includes(name.toLowerCase()) || seen.has(name)) continue;
      seen.add(name);
      out.push(name);
    }
    return out;
  }

  function fillCustomVars(text: string, values: Record<string, string>): string {
    return text.replace(VAR_RE, (whole, inner) => {
      const name = String(inner).trim();
      return name in values ? values[name] : whole;
    });
  }

  // Modal state for collecting custom-variable values before insertion.
  let varFill = $state<{
    template: string;
    vars: string[];
    values: Record<string, string>;
    apply: (filled: string) => void;
  } | null>(null);

  // Resolve system vars, then either insert directly or open the fill-in form.
  function beginInsert(content: string, apply: (filled: string) => void) {
    const resolved = resolveSystemVars(content);
    const vars = extractCustomVars(resolved);
    if (vars.length === 0) {
      apply(resolved);
      return;
    }
    varFill = {
      template: resolved,
      vars,
      values: Object.fromEntries(vars.map((v) => [v, ''])),
      apply
    };
  }

  function submitVarFill() {
    if (!varFill) return;
    const filled = fillCustomVars(varFill.template, varFill.values);
    const apply = varFill.apply;
    varFill = null;
    apply(filled);
  }

  function cancelVarFill() {
    varFill = null;
  }

  function focusOnMount(node: HTMLElement) {
    node.focus();
  }

  function insertPrompt(p: Prompt) {
    beginInsert(p.content, (filled) => {
      input = input ? `${input}\n${filled}` : filled;
    });
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
      stop: parseStop(stopText),
      tools_enabled: toolsEnabled,
      web_search: webSearch,
      collection_ids: [...attachedCollections]
    });
    presets = await listPresets();
  }

  // Update an existing assistant in place with the chat's current configuration.
  async function updatePresetFromCurrent(p: Preset) {
    if (!confirm(`overwrite "${p.name}" with the current configuration?`)) return;
    await updatePreset(p.id, {
      name: p.name,
      model,
      system_prompt: systemPrompt.trim() || null,
      temperature: parseNumber(temperature),
      top_p: parseNumber(topP),
      stop: parseStop(stopText),
      tools_enabled: toolsEnabled,
      web_search: webSearch,
      collection_ids: [...attachedCollections]
    });
    presets = await listPresets();
  }

  // Applying a preset = "become this assistant": set its persona/behavior and,
  // if it carries knowledge, attach exactly those collections. A preset with no
  // knowledge leaves the chat's manually-attached collections untouched.
  async function applyPreset(p: Preset) {
    if (p.model) model = p.model;
    systemPrompt = p.system_prompt ?? '';
    temperature = p.temperature != null ? String(p.temperature) : '';
    topP = p.top_p != null ? String(p.top_p) : '';
    stopText = (p.stop ?? []).join(', ');
    toolsEnabled = p.tools_enabled;
    webSearch = p.web_search && webSearchAvailable;
    await updateConversation(currentId, {
      model: p.model ?? model,
      system_prompt: p.system_prompt ?? null,
      temperature: p.temperature,
      top_p: p.top_p,
      stop: p.stop,
      tools_enabled: toolsEnabled,
      web_search: webSearch
    });
    if (p.collection_ids.length) {
      attachedCollections = new Set(await setConversationCollections(currentId, p.collection_ids));
    }
  }

  async function removePreset(id: number) {
    if (!confirm('delete this preset?')) return;
    await deletePreset(id);
    presets = await listPresets();
  }

  async function addMemory() {
    const text = window.prompt('add a memory — what should the model always remember?');
    if (!text || !text.trim()) return;
    await createMemory(text.trim());
    memories = await listMemories();
  }

  async function removeMemory(id: number) {
    if (!confirm('forget this memory?')) return;
    await deleteMemory(id);
    memories = await listMemories();
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

  let copiedIdx = $state<number | null>(null);
  async function copyMessage(idx: number, content: string) {
    try {
      await navigator.clipboard.writeText(messagePlainText(content));
      copiedIdx = idx;
      setTimeout(() => { if (copiedIdx === idx) copiedIdx = null; }, 1200);
    } catch {
      // clipboard unavailable (insecure context / denied) — no-op
    }
  }

  async function rateMessage(idx: number, rating: number) {
    const msg = messages[idx];
    if (msg.id == null) return;
    const next = msg.rating === rating ? 0 : rating; // click the active thumb to clear
    try {
      const res = await setFeedback(currentId, msg.id, next);
      messages[idx] = { ...msg, rating: res.rating };
    } catch {
      // ignore feedback failure
    }
  }

  // Regenerate variants of the trailing assistant message (◀ n/m ▶ navigation).
  let variants = $state<MessageVariant[]>([]);
  async function refreshVariants() {
    const last = messages[messages.length - 1];
    if (last && last.role === 'assistant' && last.id != null) {
      variants = await listVariants(currentId, last.id);
    } else {
      variants = [];
    }
  }
  const variantNav = $derived.by(() => {
    if (variants.length < 2) return null;
    const last = messages[messages.length - 1];
    if (!last || last.role !== 'assistant' || last.id == null) return null;
    const idx = variants.findIndex((v) => v.id === last.id);
    if (idx < 0) return null;
    return {
      idx,
      total: variants.length,
      prev: idx > 0 ? variants[idx - 1].id : null,
      next: idx < variants.length - 1 ? variants[idx + 1].id : null
    };
  });
  async function switchVariant(id: number | null) {
    if (id == null || streaming) return;
    await activateVariant(currentId, id);
    await load(currentId);
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
        max_tokens: parseNumber(maxTokens),
        presence_penalty: parseNumber(presencePenalty),
        frequency_penalty: parseNumber(frequencyPenalty),
        seed: parseNumber(seed),
        web_search: webSearch,
        tools_enabled: toolsEnabled,
        folder_id: folderId
      });
      const tags = tagsText
        .split(',')
        .map((t) => t.trim())
        .filter(Boolean);
      tagsText = (await setConversationTags(currentId, tags)).join(', ');
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

  function appendToolCall(tc: ToolCallEvent) {
    const last = messages[messages.length - 1];
    const tool_calls = [...(last.tool_calls ?? []), tc];
    messages[messages.length - 1] = { ...last, tool_calls };
    scroller?.scrollTo({ top: scroller.scrollHeight });
  }

  function appendImage(url: string) {
    const last = messages[messages.length - 1];
    const images = [...(last.images ?? []), url];
    messages[messages.length - 1] = { ...last, images };
    scroller?.scrollTo({ top: scroller.scrollHeight });
  }

  function setSources(sources: Source[]) {
    const last = messages[messages.length - 1];
    messages[messages.length - 1] = { ...last, sources };
  }

  async function runStream(
    operation: (opts: {
      signal: AbortSignal;
      onDelta: (d: string) => void;
      onToolCall: (tc: ToolCallEvent) => void;
      onImage: (url: string) => void;
      onSources: (s: Source[]) => void;
    }) => Promise<void>
  ) {
    streaming = true;
    followups = [];
    abort = new AbortController();
    try {
      await operation({
        signal: abort.signal,
        onDelta: appendDelta,
        onToolCall: appendToolCall,
        onImage: appendImage,
        onSources: setSources
      });
      await load(currentId);
      followups = await getFollowups(currentId).catch(() => []);
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
    const wasFirst = messages.length === 0; // no prior turns -> this is the opener
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
    if (wasFirst) {
      const t = await autotitle(currentId).catch(() => null);
      if (t) {
        title = t;
        convs.refresh();
      }
    }
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

  // Regenerate any assistant turn (not just the trailing one). Regenerating
  // mid-thread discards the turns after it (the backend branches there).
  async function regenAt(i: number) {
    if (streaming) return;
    const msg = messages[i];
    if (msg.role !== 'assistant' || msg.id == null) return;
    if (i < messages.length - 1 && !confirm('regenerate this reply? later messages will be discarded')) return;
    // clear carry-over so the prior variant's sources/tools/images don't
    // briefly mis-attribute to the regenerating reply
    messages = [...messages.slice(0, i), { id: null, role: 'assistant', content: '' }];
    await tick();
    scroller?.scrollTo({ top: scroller.scrollHeight });
    await runStream((opts) => regenerateMessage(currentId, msg.id!, model, opts));
  }

  // Continue (extend) the trailing assistant reply that stopped early. The
  // streamed text is appended onto the same message — appendDelta already
  // targets the trailing message, which is the one being continued.
  async function continueAt(i: number) {
    if (streaming) return;
    const msg = messages[i];
    if (msg.role !== 'assistant' || msg.id == null || i !== messages.length - 1) return;
    followups = [];
    await runStream((opts) => continueMessage(currentId, msg.id!, model, opts));
  }

  // Delete a message and everything after it (truncate the thread here).
  async function deleteAt(i: number) {
    if (streaming) return;
    const msg = messages[i];
    if (msg.id == null) return;
    const trailing = i === messages.length - 1;
    if (!confirm(trailing ? 'delete this message?' : 'delete this message and everything after it?')) return;
    await deleteMessage(currentId, msg.id);
    messages = messages.slice(0, i);
    editingIndex = null;
    convs.refresh();
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

  // ---- in-composer commands: / prompts · @ models · # collections ----
  type CmdKind = 'prompt' | 'model' | 'collection';
  let composer = $state<HTMLTextAreaElement | null>(null);
  let cmd = $state<{ kind: CmdKind; query: string; start: number; end: number } | null>(null);
  let cmdIndex = $state(0);

  const CMD_HINT: Record<CmdKind, string> = {
    prompt: 'prompts — insert',
    model: 'models — switch',
    collection: 'knowledge — attach'
  };

  // The active command is the trailing /@# token at the caret (whitespace-
  // delimited), e.g. "summarise /sum" -> prompt command, query "sum".
  function syncCommand() {
    const el = composer;
    if (!el || streaming || editingIndex !== null) {
      cmd = null;
      return;
    }
    const caret = el.selectionStart ?? input.length;
    const m = /(?:^|\s)([/@#])(\S*)$/.exec(input.slice(0, caret));
    if (!m) {
      cmd = null;
      return;
    }
    const kind: CmdKind = m[1] === '/' ? 'prompt' : m[1] === '@' ? 'model' : 'collection';
    // Preserve the highlighted row across caret-only events (arrow nav fires
    // keyup -> syncCommand); only reset when the actual token changes.
    if (!cmd || cmd.kind !== kind || cmd.query !== m[2] || cmd.start !== caret - m[2].length - 1)
      cmdIndex = 0;
    cmd = { kind, query: m[2], start: caret - m[2].length - 1, end: caret };
  }

  const cmdItems = $derived.by<(Prompt | Collection | string)[]>(() => {
    if (!cmd) return [];
    const q = cmd.query.toLowerCase();
    if (cmd.kind === 'prompt')
      return prompts
        .filter((p) => p.title.toLowerCase().includes(q) || p.content.toLowerCase().includes(q))
        .slice(0, 8);
    if (cmd.kind === 'model') return models.filter((m) => m.toLowerCase().includes(q)).slice(0, 8);
    return collections.filter((c) => c.name.toLowerCase().includes(q)).slice(0, 8);
  });

  function cmdLabel(item: Prompt | Collection | string): string {
    if (typeof item === 'string') return item;
    return 'title' in item ? item.title : item.name;
  }

  async function applyCommand(item: Prompt | Collection | string) {
    if (!cmd || !composer) return;
    const before = input.slice(0, cmd.start);
    const after = input.slice(cmd.end);
    if (cmd.kind === 'prompt' && typeof item !== 'string' && 'content' in item) {
      // Remove the command token from the composer NOW (not only via the
      // deferred splice) so cancelling the var-fill form can't leave an orphaned
      // "/query" behind. The composer is disabled while the form is open, so the
      // captured before/after can't drift before the (possibly deferred) insert.
      cmd = null;
      input = before + after;
      beginInsert(item.content, async (filled) => {
        input = before + filled + after;
        const caretPos = (before + filled).length;
        await tick();
        composer?.focus();
        composer?.setSelectionRange(caretPos, caretPos);
      });
      return;
    }
    let caretPos = before.length;
    if (cmd.kind === 'model' && typeof item === 'string') {
      model = item;
      input = before + after;
    } else if (cmd.kind === 'collection' && typeof item !== 'string' && 'document_count' in item) {
      if (!attachedCollections.has(item.id)) await toggleCollection(item.id);
      input = before + after;
    }
    cmd = null;
    await tick();
    composer.focus();
    composer.setSelectionRange(caretPos, caretPos);
  }

  function onKey(e: KeyboardEvent) {
    if (cmd && cmdItems.length) {
      if (e.key === 'ArrowDown') {
        e.preventDefault();
        cmdIndex = Math.min(cmdItems.length - 1, cmdIndex + 1);
        return;
      }
      if (e.key === 'ArrowUp') {
        e.preventDefault();
        cmdIndex = Math.max(0, cmdIndex - 1);
        return;
      }
      if (e.key === 'Enter' || e.key === 'Tab') {
        e.preventDefault();
        applyCommand(cmdItems[cmdIndex]);
        return;
      }
      if (e.key === 'Escape') {
        e.preventDefault();
        cmd = null;
        return;
      }
    }
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
    {#if docs.length + attachedCollections.size > 0}
      <span class="rag-badge" title="{docs.length} doc(s) + {attachedCollections.size} collection(s) — RAG active">
        📎 {docs.length + attachedCollections.size}
      </span>
    {/if}
    {#if webSearch}
      <span class="rag-badge web" title="web search active for this chat">🌐 web</span>
    {/if}
    {#if toolsEnabled}
      <span class="rag-badge tools" title="tools enabled ({builtinTools.join(', ')})">🔧 tools</span>
    {/if}
  </div>
  <div class="header-controls">
    <ModelPicker {models} value={model} disabled={streaming} onSelect={(m) => (model = m)} />
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
    <div class="row">
      <label class="num">
        <span class="lbl">max tokens</span>
        <input type="number" min="1" step="1" bind:value={maxTokens} placeholder="default" />
      </label>
      <label class="num">
        <span class="lbl">seed</span>
        <input type="number" step="1" bind:value={seed} placeholder="random" />
      </label>
    </div>
    <div class="row">
      <label class="num">
        <span class="lbl">presence penalty</span>
        <input type="number" min="-2" max="2" step="0.1" bind:value={presencePenalty} placeholder="default" />
      </label>
      <label class="num">
        <span class="lbl">frequency penalty</span>
        <input type="number" min="-2" max="2" step="0.1" bind:value={frequencyPenalty} placeholder="default" />
      </label>
    </div>
    <label>
      <span class="lbl">stop sequences <span class="hint">comma-separated</span></span>
      <input type="text" bind:value={stopText} placeholder="e.g. ###, END" />
    </label>
    <label>
      <span class="lbl">tags <span class="hint">comma-separated</span></span>
      <input type="text" bind:value={tagsText} placeholder="e.g. work, research" />
    </label>
    {#if folders.length}
      <label>
        <span class="lbl">folder</span>
        <select bind:value={folderId}>
          <option value={null}>— none —</option>
          {#each folders as f (f.id)}
            <option value={f.id}>{f.name}</option>
          {/each}
        </select>
      </label>
    {/if}
    <label class="toggle">
      <input type="checkbox" bind:checked={webSearch} disabled={!webSearchAvailable} />
      <span class="lbl" style="text-transform: none; letter-spacing: 0;">
        web search
        {#if !webSearchAvailable}
          <span class="hint">— set <code>FREE_WEBUI_SEARXNG_URL</code> on the backend to enable</span>
        {/if}
      </span>
    </label>
    <label class="toggle">
      <input type="checkbox" bind:checked={toolsEnabled} />
      <span class="lbl" style="text-transform: none; letter-spacing: 0;">
        tools <span class="hint">— built-in: {#each builtinTools as t, ti (t)}{ti > 0 ? ', ' : ''}<code>{t}</code>{/each}</span>
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
        <span class="lbl">assistants <span class="hint">model · persona · tools · knowledge</span></span>
        <button class="action" type="button" onclick={saveCurrentAsPreset}>+ save current</button>
      </div>
      {#if presets.length === 0}
        <div class="doc-empty">no assistants — configure the chat (model, system prompt, tools, web, knowledge) then "save current"</div>
      {:else}
        <ul class="doc-list">
          {#each presets as p (p.id)}
            <li>
              <button
                class="prompt-pick"
                type="button"
                onclick={() => applyPreset(p)}
                title={p.description ?? `${p.model ?? 'any model'} · ${p.system_prompt ?? 'no system prompt'}`}
              >
                {p.name}
                {#if p.tools_enabled}<span class="badge" title="tools on">🔧</span>{/if}
                {#if p.web_search}<span class="badge" title="web search on">🌐</span>{/if}
                {#if p.collection_ids.length}<span class="badge" title="{p.collection_ids.length} knowledge collection(s)">📚 {p.collection_ids.length}</span>{/if}
              </button>
              <span class="doc-meta">{p.model ?? '—'}</span>
              <button class="doc-x" aria-label="overwrite with current config" title="overwrite with current config" onclick={() => updatePresetFromCurrent(p)}>↑</button>
              <button class="doc-x" aria-label="delete" onclick={() => removePreset(p.id)}>×</button>
            </li>
          {/each}
        </ul>
      {/if}
    </div>

    <div class="docs">
      <div class="docs-head">
        <span class="lbl">prompts <span class="hint">{`{{var}}`} prompts you for input</span></span>
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
        <span class="lbl">memories <span class="hint">applied to every chat</span></span>
        <button class="action" type="button" onclick={addMemory}>+ add</button>
      </div>
      {#if memories.length === 0}
        <div class="doc-empty">no memories — add facts you want the model to remember everywhere</div>
      {:else}
        <ul class="doc-list">
          {#each memories as m (m.id)}
            <li>
              <span class="doc-name" title={m.content}>{m.content}</span>
              <button class="doc-x" aria-label="forget" onclick={() => removeMemory(m.id)}>×</button>
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
        <span class="lbl">public share link</span>
        {#if shareToken}
          <div style="display: flex; gap: 0.35rem;">
            <button class="action" type="button" onclick={copyShare}>{shareCopied ? '✓ copied' : 'copy link'}</button>
            <button class="action" type="button" onclick={revokeShare}>revoke</button>
          </div>
        {:else}
          <button class="action" type="button" onclick={makeShare}>create link</button>
        {/if}
      </div>
      {#if shareToken}
        <div class="doc-empty" style="word-break: break-all;">{shareUrl(shareToken)}</div>
      {:else}
        <div class="doc-empty">anyone with the link can view this conversation read-only</div>
      {/if}
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

    <div class="docs">
      <div class="docs-head">
        <span class="lbl">knowledge bases</span>
        <a class="action" href="/collections">manage →</a>
      </div>
      {#if collections.length === 0}
        <div class="doc-empty">no collections — create reusable document sets in knowledge bases</div>
      {:else}
        <ul class="doc-list">
          {#each collections as c (c.id)}
            <li>
              <label class="kb-row">
                <input
                  type="checkbox"
                  checked={attachedCollections.has(c.id)}
                  onchange={() => toggleCollection(c.id)}
                />
                <span class="doc-name" title={c.name}>{c.name}</span>
              </label>
              <span class="doc-meta">{c.document_count} docs</span>
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
            {#if messagePlainText(msg.content)}
              <button class="action" title="copy message" onclick={() => copyMessage(i, msg.content)}>
                {copiedIdx === i ? '✓ copied' : 'copy'}
              </button>
            {/if}
            {#if msg.role === 'assistant' && i === messages.length - 1 && variantNav}
              <span class="variant-nav">
                <button
                  class="action vnav"
                  aria-label="previous variant"
                  disabled={variantNav.prev == null}
                  onclick={() => switchVariant(variantNav.prev)}
                >◀</button>
                <span class="vcount">{variantNav.idx + 1}/{variantNav.total}</span>
                <button
                  class="action vnav"
                  aria-label="next variant"
                  disabled={variantNav.next == null}
                  onclick={() => switchVariant(variantNav.next)}
                >▶</button>
              </span>
            {/if}
            {#if msg.role === 'assistant' && msg.id != null && msg.content}
              <button class="action" title="regenerate this reply" onclick={() => regenAt(i)}>regenerate</button>
            {/if}
            {#if msg.role === 'assistant' && msg.id != null && msg.content && i === messages.length - 1}
              <button class="action" title="continue this reply" onclick={() => continueAt(i)}>↪ continue</button>
            {/if}
            {#if msg.role === 'assistant' && msg.id != null && msg.content}
              <button
                class="action thumb"
                class:on={msg.rating === 1}
                aria-label="good response"
                title="good response"
                onclick={() => rateMessage(i, 1)}
              >👍</button>
              <button
                class="action thumb"
                class:on={msg.rating === -1}
                aria-label="bad response"
                title="bad response"
                onclick={() => rateMessage(i, -1)}
              >👎</button>
            {/if}
            {#if msg.role === 'assistant' && messagePlainText(msg.content)}
              <button class="action" onclick={() => speakMessage(i, messagePlainText(msg.content))}>
                {speakingIdx === i ? '⏹ stop' : '🔊 speak'}
              </button>
            {/if}
            {#if msg.id != null}
              <button class="action del" title="delete from here" aria-label="delete message" onclick={() => deleteAt(i)}>🗑</button>
            {/if}
          </div>
        {/if}
      </div>
      {#if msg.tool_calls && msg.tool_calls.length}
        <div class="tool-calls">
          {#each msg.tool_calls as tc, ti (ti)}
            <div class="tool-chip" title={JSON.stringify(tc.arguments)}>
              🔧 <code>{tc.name}({Object.entries(tc.arguments).map(([k, v]) => `${k}=${JSON.stringify(v)}`).join(', ')})</code>
              → <code class="tool-result">{tc.result}</code>
            </div>
          {/each}
        </div>
      {/if}
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
          {#if msg.images && msg.images.length}
            {#each msg.images as src, ii (ii)}
              <img class="attached generated" src={src} alt="generated" />
            {/each}
          {/if}
        {/if}
        {#if msg.sources && msg.sources.length}
          <div class="sources">
            <span class="src-label">sources</span>
            {#each msg.sources as s, si (si)}
              {#if s.kind === 'web' && s.detail}
                <a class="src" href={s.detail} target="_blank" rel="noreferrer noopener" title={s.detail}>🌐 {s.label}</a>
              {:else}
                <span class="src" title={s.label}>📄 {s.label}</span>
              {/if}
            {/each}
          </div>
        {/if}
      </div>
    </div>
  {/each}
</div>

{#if followups.length && !streaming}
  <div class="followups">
    {#each followups as f, fi (fi)}
      <button type="button" class="followup" onclick={() => { input = f; }}>{f}</button>
    {/each}
  </div>
{/if}

<form
  class="composer"
  ondragover={(e) => e.preventDefault()}
  ondrop={onDrop}
  onsubmit={(e) => { e.preventDefault(); send(); }}
>
  {#if cmd && cmdItems.length}
    <div class="cmd-menu" role="listbox">
      <div class="cmd-hint">{CMD_HINT[cmd.kind]}</div>
      {#each cmdItems as item, i (cmdLabel(item))}
        <button
          type="button"
          role="option"
          aria-selected={i === cmdIndex}
          class="cmd-item"
          class:active={i === cmdIndex}
          onmousedown={(e) => { e.preventDefault(); applyCommand(item); }}
          onmouseenter={() => (cmdIndex = i)}
        >
          <span class="cmd-label">{cmdLabel(item)}</span>
          {#if cmd.kind === 'collection' && typeof item !== 'string' && 'document_count' in item}
            <span class="cmd-meta">{attachedCollections.has(item.id) ? 'attached' : `${item.document_count} docs`}</span>
          {/if}
        </button>
      {/each}
    </div>
  {/if}
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
      bind:this={composer}
      placeholder="message…  (/ prompts · @ models · # knowledge)"
      bind:value={input}
      onkeydown={onKey}
      oninput={syncCommand}
      onkeyup={syncCommand}
      onclick={syncCommand}
      onblur={() => setTimeout(() => (cmd = null), 120)}
      onpaste={onPaste}
      rows="2"
      disabled={streaming || editingIndex !== null || varFill !== null}
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

{#if varFill}
  <div
    class="var-overlay"
    role="presentation"
    onclick={(e) => { if (e.target === e.currentTarget) cancelVarFill(); }}
  >
    <div
      class="var-modal"
      role="dialog"
      aria-modal="true"
      aria-label="fill in prompt variables"
      tabindex="-1"
      onkeydown={(e) => {
        if (e.key === 'Escape') cancelVarFill();
        else if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) submitVarFill();
      }}
    >
      <h3>fill in the prompt</h3>
      <p class="var-hint">⌘/Ctrl+Enter to insert</p>
      {#each varFill.vars as v, i (v)}
        <label class="var-row">
          <span class="var-name">{v}</span>
          {#if i === 0}
            <textarea rows="2" bind:value={varFill.values[v]} placeholder={v} use:focusOnMount></textarea>
          {:else}
            <textarea rows="2" bind:value={varFill.values[v]} placeholder={v}></textarea>
          {/if}
        </label>
      {/each}
      <div class="var-actions">
        <button type="button" onclick={cancelVarFill}>cancel</button>
        <button type="button" class="primary" onclick={submitVarFill}>insert</button>
      </div>
    </div>
  </div>
{/if}

<style>
  .var-overlay {
    position: fixed;
    inset: 0;
    z-index: 50;
    display: flex;
    align-items: center;
    justify-content: center;
    background: rgba(0, 0, 0, 0.45);
    padding: 1rem;
  }
  .var-modal {
    width: min(480px, 100%);
    max-height: 85vh;
    overflow-y: auto;
    background: var(--bg-elev);
    border: 1px solid var(--border-soft);
    border-radius: 10px;
    box-shadow: 0 12px 40px rgba(0, 0, 0, 0.35);
    padding: 1.25rem;
  }
  .var-modal h3 {
    margin: 0 0 0.25rem;
    font-size: 1rem;
  }
  .var-hint {
    margin: 0 0 1rem;
    color: var(--text-muted);
    font-size: 0.75rem;
  }
  .var-row {
    display: flex;
    flex-direction: column;
    gap: 0.3rem;
    margin-bottom: 0.85rem;
  }
  .var-name {
    font-size: 0.8rem;
    color: var(--text-dim);
    font-family: var(--mono, ui-monospace, monospace);
  }
  .var-row textarea {
    resize: vertical;
    background: var(--bg);
    color: var(--text);
    border: 1px solid var(--border);
    border-radius: 6px;
    padding: 0.5rem 0.6rem;
    font: inherit;
    font-size: 0.9rem;
  }
  .var-row textarea:focus {
    outline: none;
    border-color: var(--accent);
  }
  .var-actions {
    display: flex;
    justify-content: flex-end;
    gap: 0.5rem;
    margin-top: 0.5rem;
  }
  .var-actions button {
    border: 1px solid var(--border);
    border-radius: 6px;
    padding: 0.45rem 0.9rem;
    font: inherit;
    cursor: pointer;
    background: var(--bg-elev);
    color: var(--text);
  }
  .var-actions .primary {
    background: var(--accent);
    color: #fff;
    border-color: var(--accent);
  }
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
  .rag-badge.tools {
    background: color-mix(in srgb, #f59e0b 22%, transparent);
    color: #f59e0b;
    border-color: color-mix(in srgb, #f59e0b 45%, transparent);
  }
  .tool-calls {
    display: flex;
    flex-direction: column;
    gap: 0.35rem;
    margin: 0.25rem 0 0.5rem;
  }
  .tool-chip {
    font-size: 0.78rem;
    background: color-mix(in srgb, #f59e0b 10%, transparent);
    border: 1px solid color-mix(in srgb, #f59e0b 35%, transparent);
    border-radius: 6px;
    padding: 0.35rem 0.55rem;
    color: var(--text-dim);
  }
  .tool-chip code {
    font-family: ui-monospace, monospace;
    background: var(--bg-hover);
    padding: 0.05em 0.3em;
    border-radius: 3px;
    color: var(--text);
  }
  .tool-chip .tool-result { color: var(--accent-2); }
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
  .kb-row { display: flex; align-items: center; gap: 0.4rem; flex: 1; min-width: 0; cursor: pointer; }
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
  .prompt-pick .badge {
    font-size: 0.7rem;
    color: var(--text-dim);
    margin-left: 0.3rem;
    white-space: nowrap;
  }
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
  .action.thumb { padding: 0.15rem 0.35rem; }
  .action.thumb.on { color: var(--text); border-color: var(--accent); background: color-mix(in srgb, var(--accent) 14%, transparent); }
  .variant-nav { display: inline-flex; align-items: center; gap: 0.2rem; }
  .action.vnav { padding: 0.15rem 0.35rem; }
  .action.vnav:disabled { opacity: 0.4; cursor: default; }
  .vcount { font-size: 0.7rem; color: var(--text-muted); min-width: 1.8rem; text-align: center; }
  .content { line-height: 1.5; word-wrap: break-word; }
  .sources {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 0.35rem;
    margin-top: 0.6rem;
    padding-top: 0.5rem;
    border-top: 1px dashed var(--border-soft);
  }
  .src-label { font-size: 0.68rem; text-transform: uppercase; letter-spacing: 0.04em; color: var(--text-muted); }
  .src {
    font-size: 0.74rem;
    color: var(--text-dim);
    background: var(--bg-elev);
    border: 1px solid var(--border-soft);
    border-radius: 999px;
    padding: 0.1rem 0.5rem;
    max-width: 16rem;
    overflow: hidden;
    white-space: nowrap;
    text-overflow: ellipsis;
    text-decoration: none;
  }
  a.src:hover { color: var(--text); border-color: var(--accent); }
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
  .followups {
    display: flex;
    flex-wrap: wrap;
    gap: 0.4rem;
    max-width: 760px;
    margin: 0 auto;
    width: 100%;
    padding: 0 1rem 0.5rem;
    box-sizing: border-box;
  }
  .followup {
    background: var(--bg-elev);
    color: var(--text-dim);
    border: 1px solid var(--border-soft);
    border-radius: 999px;
    padding: 0.3rem 0.7rem;
    font: inherit;
    font-size: 0.8rem;
    cursor: pointer;
    text-align: left;
  }
  .followup:hover { color: var(--text); border-color: var(--accent); background: var(--bg-hover); }
  .composer {
    position: relative;
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
  .cmd-menu {
    position: absolute;
    bottom: calc(100% - 0.25rem);
    left: 1rem;
    right: 1rem;
    z-index: 30;
    background: var(--bg-elev);
    border: 1px solid var(--border-soft);
    border-radius: 8px;
    box-shadow: 0 -6px 24px rgba(0, 0, 0, 0.25);
    padding: 0.3rem;
    display: flex;
    flex-direction: column;
    max-height: 280px;
    overflow-y: auto;
  }
  .cmd-hint {
    color: var(--text-dim);
    font-size: 0.7rem;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    padding: 0.25rem 0.5rem;
  }
  .cmd-item {
    display: flex;
    justify-content: space-between;
    align-items: center;
    gap: 0.5rem;
    text-align: left;
    background: transparent;
    color: var(--text);
    border: none;
    border-radius: 6px;
    padding: 0.4rem 0.5rem;
    font: inherit;
    font-size: 0.85rem;
    cursor: pointer;
  }
  .cmd-item.active { background: var(--bg-sidebar); }
  .cmd-label {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .cmd-meta {
    flex-shrink: 0;
    color: var(--text-dim);
    font-size: 0.72rem;
  }
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
  .attached.generated {
    max-height: 512px;
    border: 1px solid color-mix(in srgb, var(--accent-2) 40%, transparent);
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

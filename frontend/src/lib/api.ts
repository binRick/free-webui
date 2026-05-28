export type Role = 'system' | 'user' | 'assistant';

export interface Message {
  role: Role;
  content: string;
}

export interface ConversationSummary {
  id: string;
  title: string;
  model: string | null;
  updated_at: number;
}

export interface StoredMessage {
  id: number;
  role: Role;
  content: string;
  created_at: number;
}

export interface Conversation {
  id: string;
  title: string;
  model: string | null;
  system_prompt: string | null;
  temperature: number | null;
  top_p: number | null;
  stop: string[] | null;
  web_search: boolean;
  created_at: number;
  updated_at: number;
  messages: StoredMessage[];
}

export interface UpdateConversation {
  title?: string | null;
  model?: string | null;
  system_prompt?: string | null;
  temperature?: number | null;
  top_p?: number | null;
  stop?: string[] | null;
  web_search?: boolean | null;
}

export interface WebSearchStatus {
  available: boolean;
  url: string | null;
}

export async function getWebSearchStatus(): Promise<WebSearchStatus> {
  const res = await fetch('/api/web_search/status');
  if (!res.ok) return { available: false, url: null };
  return res.json();
}

export async function listConversations(): Promise<ConversationSummary[]> {
  const res = await fetch('/api/conversations');
  if (!res.ok) return [];
  return res.json();
}

export async function createConversation(model: string | null = null): Promise<ConversationSummary> {
  const res = await fetch('/api/conversations', {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ model })
  });
  if (!res.ok) throw new Error(`create failed: ${res.status}`);
  return res.json();
}

export async function getConversation(id: string): Promise<Conversation> {
  const res = await fetch(`/api/conversations/${id}`);
  if (!res.ok) throw new Error(`load failed: ${res.status}`);
  return res.json();
}

export async function deleteConversation(id: string): Promise<void> {
  await fetch(`/api/conversations/${id}`, { method: 'DELETE' });
}

export interface Document {
  id: number;
  filename: string;
  mime: string | null;
  bytes: number;
  chunk_count: number;
  embedding_model: string | null;
  created_at: number;
}

export async function listDocuments(conversationId: string): Promise<Document[]> {
  const res = await fetch(`/api/conversations/${conversationId}/documents`);
  if (!res.ok) return [];
  return res.json();
}

export async function uploadDocument(
  conversationId: string,
  file: File
): Promise<Document> {
  const fd = new FormData();
  fd.append('file', file);
  const res = await fetch(`/api/conversations/${conversationId}/documents`, {
    method: 'POST',
    body: fd
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail ?? `upload failed: ${res.status}`);
  }
  return res.json();
}

export async function deleteDocument(
  conversationId: string,
  documentId: number
): Promise<void> {
  await fetch(
    `/api/conversations/${conversationId}/documents/${documentId}`,
    { method: 'DELETE' }
  );
}

export interface InstalledModel {
  name: string;
  size: number | null;
  modified_at: string | null;
  digest: string | null;
}

export async function listInstalledModels(): Promise<InstalledModel[]> {
  const res = await fetch('/api/admin/models');
  if (!res.ok) throw new Error(`load failed: ${res.status}`);
  return res.json();
}

export async function deleteInstalledModel(name: string): Promise<void> {
  const res = await fetch(`/api/admin/models?name=${encodeURIComponent(name)}`, {
    method: 'DELETE'
  });
  if (!res.ok) throw new Error(`delete failed: ${res.status}`);
}

export interface PullOpts {
  signal?: AbortSignal;
  onEvent: (event: Record<string, unknown>) => void;
}

export async function pullModel(name: string, opts: PullOpts): Promise<void> {
  const res = await fetch('/api/admin/models/pull', {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ name }),
    signal: opts.signal
  });
  if (!res.ok || !res.body) throw new Error(`pull failed: ${res.status}`);
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buf = '';
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    let nl: number;
    while ((nl = buf.indexOf('\n')) !== -1) {
      const line = buf.slice(0, nl).trim();
      buf = buf.slice(nl + 1);
      if (!line) continue;
      try {
        opts.onEvent(JSON.parse(line));
      } catch {
        // ignore non-JSON lines
      }
    }
  }
}

export interface Preset {
  id: number;
  name: string;
  model: string | null;
  system_prompt: string | null;
  temperature: number | null;
  top_p: number | null;
  stop: string[] | null;
  created_at: number;
  updated_at: number;
}

export interface PresetIn {
  name: string;
  model?: string | null;
  system_prompt?: string | null;
  temperature?: number | null;
  top_p?: number | null;
  stop?: string[] | null;
}

export async function listPresets(): Promise<Preset[]> {
  const res = await fetch('/api/presets');
  if (!res.ok) return [];
  return res.json();
}

export async function createPreset(body: PresetIn): Promise<Preset> {
  const res = await fetch('/api/presets', {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(body)
  });
  if (!res.ok) throw new Error(`create preset failed: ${res.status}`);
  return res.json();
}

export async function deletePreset(id: number): Promise<void> {
  await fetch(`/api/presets/${id}`, { method: 'DELETE' });
}

export interface Prompt {
  id: number;
  title: string;
  content: string;
  created_at: number;
  updated_at: number;
}

export async function listPrompts(): Promise<Prompt[]> {
  const res = await fetch('/api/prompts');
  if (!res.ok) return [];
  return res.json();
}

export async function createPrompt(title: string, content: string): Promise<Prompt> {
  const res = await fetch('/api/prompts', {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ title, content })
  });
  if (!res.ok) throw new Error(`create prompt failed: ${res.status}`);
  return res.json();
}

export async function deletePrompt(id: number): Promise<void> {
  await fetch(`/api/prompts/${id}`, { method: 'DELETE' });
}

export function exportConversationUrl(
  conversationId: string,
  format: 'json' | 'md'
): string {
  return `/api/conversations/${conversationId}/export?format=${format}`;
}

export async function updateConversation(
  id: string,
  patch: UpdateConversation
): Promise<Conversation> {
  const res = await fetch(`/api/conversations/${id}`, {
    method: 'PATCH',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(patch)
  });
  if (!res.ok) throw new Error(`update failed: ${res.status}`);
  return res.json();
}

export async function listModels(): Promise<string[]> {
  const res = await fetch('/api/models');
  if (!res.ok) return [];
  const json = await res.json();
  return (json.data ?? []).map((m: { id: string }) => m.id);
}

export type ContentPart =
  | { type: 'text'; text: string }
  | { type: 'image_url'; image_url: { url: string } };

export type MessageContent = string | ContentPart[];

export function parseContent(raw: string): MessageContent {
  if (raw.startsWith('[') && raw.endsWith(']')) {
    try {
      const parsed = JSON.parse(raw);
      if (Array.isArray(parsed)) return parsed as ContentPart[];
    } catch {
      // fall through
    }
  }
  return raw;
}

export interface StreamOpts {
  signal?: AbortSignal;
  onDelta: (delta: string) => void;
}

async function consumeStream(res: Response, opts: StreamOpts): Promise<void> {
  if (!res.ok || !res.body) throw new Error(`stream failed: ${res.status}`);
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buf = '';
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    let sep: number;
    while ((sep = buf.indexOf('\n\n')) !== -1) {
      const event = buf.slice(0, sep);
      buf = buf.slice(sep + 2);
      for (const line of event.split('\n')) {
        if (!line.startsWith('data:')) continue;
        const data = line.slice(5).trim();
        if (data === '[DONE]') return;
        try {
          const json = JSON.parse(data);
          const delta = json.choices?.[0]?.delta?.content;
          if (typeof delta === 'string' && delta.length) opts.onDelta(delta);
        } catch {
          // keep-alives etc.
        }
      }
    }
  }
}

export async function sendMessage(
  conversationId: string,
  content: MessageContent,
  model: string | null,
  opts: StreamOpts
): Promise<void> {
  const res = await fetch(`/api/conversations/${conversationId}/messages`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ content, model }),
    signal: opts.signal
  });
  await consumeStream(res, opts);
}

export async function regenerate(
  conversationId: string,
  model: string | null,
  opts: StreamOpts
): Promise<void> {
  const res = await fetch(`/api/conversations/${conversationId}/regenerate`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ model }),
    signal: opts.signal
  });
  await consumeStream(res, opts);
}

export async function editMessage(
  conversationId: string,
  messageId: number,
  content: MessageContent,
  model: string | null,
  opts: StreamOpts
): Promise<void> {
  const res = await fetch(
    `/api/conversations/${conversationId}/messages/${messageId}`,
    {
      method: 'PATCH',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ content, model }),
      signal: opts.signal
    }
  );
  await consumeStream(res, opts);
}

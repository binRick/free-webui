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
  rating?: number | null; // current user's feedback: 1, -1, or null
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
  tools_enabled: boolean;
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
  tools_enabled?: boolean | null;
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

export interface ImageStatus {
  available: boolean;
  backend: string | null;
}

export async function getImageStatus(): Promise<ImageStatus> {
  const res = await fetch('/api/images/status');
  if (!res.ok) return { available: false, backend: null };
  return res.json();
}

export interface CodeStatus {
  available: boolean;
  backend: string | null;
}

export async function getCodeStatus(): Promise<CodeStatus> {
  const res = await fetch('/api/code/status');
  if (!res.ok) return { available: false, backend: null };
  return res.json();
}

export async function listConversations(q?: string): Promise<ConversationSummary[]> {
  const term = q?.trim();
  const url = term ? `/api/conversations?q=${encodeURIComponent(term)}` : '/api/conversations';
  const res = await fetch(url);
  if (!res.ok) return [];
  return res.json();
}

export async function renameConversation(id: string, title: string): Promise<void> {
  await updateConversation(id, { title });
}

export async function autotitle(id: string): Promise<string | null> {
  const res = await fetch(`/api/conversations/${id}/autotitle`, { method: 'POST' });
  if (!res.ok) return null;
  return (await res.json()).title ?? null;
}

export interface MessageVariant {
  id: number;
  active: boolean;
  created_at: number;
}

export async function listVariants(
  conversationId: string,
  messageId: number
): Promise<MessageVariant[]> {
  const res = await fetch(`/api/conversations/${conversationId}/messages/${messageId}/variants`);
  if (!res.ok) return [];
  return (await res.json()).variants ?? [];
}

export async function activateVariant(conversationId: string, messageId: number): Promise<void> {
  await fetch(`/api/conversations/${conversationId}/messages/${messageId}/activate`, {
    method: 'POST'
  });
}

export async function setFeedback(
  conversationId: string,
  messageId: number,
  rating: number
): Promise<{ rating: number | null }> {
  const res = await fetch(
    `/api/conversations/${conversationId}/messages/${messageId}/feedback`,
    {
      method: 'PUT',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ rating })
    }
  );
  if (!res.ok) throw new Error(`feedback failed: ${res.status}`);
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

export interface McpServer {
  id: number;
  name: string;
  url: string;
  headers: Record<string, string> | null;
  enabled: boolean;
  created_at: number;
  updated_at: number;
}

export async function listMcpServers(): Promise<McpServer[]> {
  const res = await fetch('/api/mcp_servers');
  if (!res.ok) return [];
  return res.json();
}

export async function createMcpServer(body: {
  name: string;
  url: string;
  headers?: Record<string, string> | null;
}): Promise<McpServer> {
  const res = await fetch('/api/mcp_servers', {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(body)
  });
  if (!res.ok) {
    const b = await res.json().catch(() => ({}));
    throw new Error(b.detail ?? `create failed: ${res.status}`);
  }
  return res.json();
}

export async function patchMcpServer(
  id: number,
  patch: Partial<{ name: string; url: string; headers: Record<string, string> | null; enabled: boolean }>
): Promise<McpServer> {
  const res = await fetch(`/api/mcp_servers/${id}`, {
    method: 'PATCH',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(patch)
  });
  if (!res.ok) {
    const b = await res.json().catch(() => ({}));
    throw new Error(b.detail ?? `patch failed: ${res.status}`);
  }
  return res.json();
}

export async function deleteMcpServer(id: number): Promise<void> {
  await fetch(`/api/mcp_servers/${id}`, { method: 'DELETE' });
}

export async function probeMcpServer(
  id: number
): Promise<{ tools: { name: string; description?: string }[] }> {
  const res = await fetch(`/api/mcp_servers/${id}/probe`, { method: 'POST' });
  if (!res.ok) {
    const b = await res.json().catch(() => ({}));
    throw new Error(b.detail ?? `probe failed: ${res.status}`);
  }
  return res.json();
}

export interface AdminUser {
  id: number;
  username: string;
  role: string;
  created_at: number;
}

export async function adminListUsers(): Promise<AdminUser[]> {
  const res = await fetch('/api/admin/users');
  if (!res.ok) throw new Error(`load failed: ${res.status}`);
  return res.json();
}

export async function adminCreateUser(
  username: string,
  password: string,
  role: 'admin' | 'user'
): Promise<AdminUser> {
  const res = await fetch('/api/admin/users', {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ username, password, role })
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail ?? `create failed: ${res.status}`);
  }
  return res.json();
}

export async function adminPatchUser(
  id: number,
  patch: { role?: 'admin' | 'user'; password?: string }
): Promise<AdminUser> {
  const res = await fetch(`/api/admin/users/${id}`, {
    method: 'PATCH',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(patch)
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail ?? `patch failed: ${res.status}`);
  }
  return res.json();
}

export async function adminDeleteUser(id: number): Promise<void> {
  const res = await fetch(`/api/admin/users/${id}`, { method: 'DELETE' });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail ?? `delete failed: ${res.status}`);
  }
}

export interface Group {
  id: number;
  name: string;
  member_count: number;
  created_at: number;
}

export async function listGroups(): Promise<Group[]> {
  const res = await fetch('/api/admin/groups');
  if (!res.ok) return [];
  return res.json();
}

export async function createGroup(name: string): Promise<Group> {
  const res = await fetch('/api/admin/groups', {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ name })
  });
  if (!res.ok) {
    const b = await res.json().catch(() => ({}));
    throw new Error(b.detail ?? `create failed: ${res.status}`);
  }
  return res.json();
}

export async function deleteGroup(id: number): Promise<void> {
  const res = await fetch(`/api/admin/groups/${id}`, { method: 'DELETE' });
  if (!res.ok) throw new Error(`delete failed: ${res.status}`);
}

export async function getGroupMembers(id: number): Promise<number[]> {
  const res = await fetch(`/api/admin/groups/${id}/members`);
  if (!res.ok) return [];
  return (await res.json()).user_ids ?? [];
}

export async function setGroupMembers(id: number, userIds: number[]): Promise<number[]> {
  const res = await fetch(`/api/admin/groups/${id}/members`, {
    method: 'PUT',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ user_ids: userIds })
  });
  if (!res.ok) throw new Error(`update failed: ${res.status}`);
  return (await res.json()).user_ids ?? [];
}

export interface ModelAccessEntry {
  group_ids: number[];
  user_ids: number[];
}

export async function listModelAccess(): Promise<Record<string, ModelAccessEntry>> {
  const res = await fetch('/api/admin/model_access');
  if (!res.ok) return {};
  return res.json();
}

export async function setModelAccess(
  modelId: string,
  groupIds: number[],
  userIds: number[]
): Promise<void> {
  const res = await fetch('/api/admin/model_access', {
    method: 'PUT',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ model_id: modelId, group_ids: groupIds, user_ids: userIds })
  });
  if (!res.ok) throw new Error(`update failed: ${res.status}`);
}

export interface PluginRecord {
  name: string;
  priority: number;
  has_inlet: boolean;
  has_outlet: boolean;
  error: string | null;
}

export async function getPlugins(): Promise<PluginRecord[]> {
  const res = await fetch('/api/plugins');
  if (!res.ok) throw new Error(`load failed: ${res.status}`);
  return res.json();
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

export interface Memory {
  id: number;
  content: string;
  created_at: number;
  updated_at: number;
}

export async function listMemories(): Promise<Memory[]> {
  const res = await fetch('/api/memories');
  if (!res.ok) return [];
  return res.json();
}

export async function createMemory(content: string): Promise<Memory> {
  const res = await fetch('/api/memories', {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ content })
  });
  if (!res.ok) throw new Error(`create memory failed: ${res.status}`);
  return res.json();
}

export async function deleteMemory(id: number): Promise<void> {
  await fetch(`/api/memories/${id}`, { method: 'DELETE' });
}

export interface ApiKey {
  id: number;
  name: string;
  key_prefix: string;
  last_used_at: number | null;
  created_at: number;
  key?: string;
}

export async function listApiKeys(): Promise<ApiKey[]> {
  const res = await fetch('/api/api_keys');
  if (!res.ok) return [];
  return res.json();
}

export async function mintApiKey(name: string): Promise<ApiKey> {
  const res = await fetch('/api/api_keys', {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ name })
  });
  if (!res.ok) throw new Error(`mint key failed: ${res.status}`);
  return res.json();
}

export async function revokeApiKey(id: number): Promise<void> {
  await fetch(`/api/api_keys/${id}`, { method: 'DELETE' });
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

export interface ToolCallEvent {
  name: string;
  arguments: Record<string, unknown>;
  result: string;
}

export interface StreamOpts {
  signal?: AbortSignal;
  onDelta: (delta: string) => void;
  onToolCall?: (tc: ToolCallEvent) => void;
  onImage?: (url: string) => void;
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
      const frame = buf.slice(0, sep);
      buf = buf.slice(sep + 2);
      let eventType = '';
      let data = '';
      for (const line of frame.split('\n')) {
        if (line.startsWith('event:')) eventType = line.slice(6).trim();
        else if (line.startsWith('data:')) data = line.slice(5).trim();
      }
      if (!data) continue;
      if (data === '[DONE]') return;
      try {
        const json = JSON.parse(data);
        if (eventType === 'tool_call') {
          opts.onToolCall?.(json as ToolCallEvent);
        } else if (eventType === 'image') {
          if (typeof json.url === 'string') opts.onImage?.(json.url);
        } else {
          const delta = json.choices?.[0]?.delta?.content;
          if (typeof delta === 'string' && delta.length) opts.onDelta(delta);
        }
      } catch {
        // keep-alives etc.
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

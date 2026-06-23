import { toasts } from './toastStore.svelte';

let redirectingTo401 = false;

// Single choke point for every API request. A 401 mid-session means the cookie
// expired or was revoked (password reset / logout-everywhere); surface it once
// and bounce to the login page instead of letting the UI silently break.
async function apiFetch(input: RequestInfo | URL, init?: RequestInit): Promise<Response> {
  const res = await fetch(input, init);
  if (res.status === 401 && typeof window !== 'undefined') {
    const p = window.location.pathname;
    if (!redirectingTo401 && !p.startsWith('/login') && !p.startsWith('/setup')) {
      redirectingTo401 = true;
      toasts.error('your session expired — please sign in again');
      const next = encodeURIComponent(p + window.location.search);
      window.location.href = `/login?next=${next}`;
    }
  }
  return res;
}

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
  pinned: boolean;
  archived: boolean;
  folder_id: number | null;
  tags: string[];
}

export interface Source {
  kind: string; // 'document' | 'web'
  label: string;
  detail?: string;
  snippet?: string;
}

export interface StoredMessage {
  id: number;
  role: Role;
  content: string;
  created_at: number;
  rating?: number | null; // current user's feedback: 1, -1, or null
  sources?: Source[] | null;
  tool_calls?: ToolCallEvent[] | null; // 🔧 calls run for this reply, re-rendered on reload
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
  full_context: boolean;
  max_tokens: number | null;
  presence_penalty: number | null;
  frequency_penalty: number | null;
  seed: number | null;
  folder_id: number | null;
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
  full_context?: boolean | null;
  max_tokens?: number | null;
  presence_penalty?: number | null;
  frequency_penalty?: number | null;
  seed?: number | null;
  pinned?: boolean | null;
  archived?: boolean | null;
  folder_id?: number | null;
}

export interface Folder {
  id: number;
  name: string;
  created_at: number;
  updated_at: number;
}

export async function listFolders(): Promise<Folder[]> {
  const res = await apiFetch('/api/folders');
  if (!res.ok) return [];
  return res.json();
}

export async function createFolder(name: string): Promise<Folder> {
  const res = await apiFetch('/api/folders', {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ name })
  });
  if (!res.ok) throw new Error(`create folder failed: ${res.status}`);
  return res.json();
}

export async function renameFolder(id: number, name: string): Promise<Folder> {
  const res = await apiFetch(`/api/folders/${id}`, {
    method: 'PATCH',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ name })
  });
  if (!res.ok) throw new Error(`rename folder failed: ${res.status}`);
  return res.json();
}

export async function deleteFolder(id: number): Promise<void> {
  await apiFetch(`/api/folders/${id}`, { method: 'DELETE' });
}

export interface Note {
  id: number;
  title: string;
  content: string;
  created_at: number;
  updated_at: number;
}

export async function listNotes(): Promise<Note[]> {
  const res = await apiFetch('/api/notes');
  if (!res.ok) return [];
  return res.json();
}

export async function createNote(title: string, content = ''): Promise<Note> {
  const res = await apiFetch('/api/notes', {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ title, content })
  });
  if (!res.ok) throw new Error(`create note failed: ${res.status}`);
  return res.json();
}

export async function updateNote(
  id: number,
  patch: { title?: string; content?: string }
): Promise<Note> {
  const res = await apiFetch(`/api/notes/${id}`, {
    method: 'PATCH',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(patch)
  });
  if (!res.ok) throw new Error(`update note failed: ${res.status}`);
  return res.json();
}

export async function deleteNote(id: number): Promise<void> {
  await apiFetch(`/api/notes/${id}`, { method: 'DELETE' });
}

export interface WebSearchStatus {
  available: boolean;
  url: string | null;
}

export async function getWebSearchStatus(): Promise<WebSearchStatus> {
  const res = await apiFetch('/api/web_search/status');
  if (!res.ok) return { available: false, url: null };
  return res.json();
}

export interface ImageStatus {
  available: boolean;
  backend: string | null;
}

export async function getImageStatus(): Promise<ImageStatus> {
  const res = await apiFetch('/api/images/status');
  if (!res.ok) return { available: false, backend: null };
  return res.json();
}

export interface CodeStatus {
  available: boolean;
  backend: string | null;
}

export async function getCodeStatus(): Promise<CodeStatus> {
  const res = await apiFetch('/api/code/status');
  if (!res.ok) return { available: false, backend: null };
  return res.json();
}

export interface AudioStatus {
  stt: boolean;
  tts: boolean;
  voice: string | null;
}

export async function getAudioStatus(): Promise<AudioStatus> {
  const res = await apiFetch('/api/audio/status');
  if (!res.ok) return { stt: false, tts: false, voice: null };
  return res.json();
}

// Send a recorded clip to the server's Whisper-style backend; returns the text.
export async function transcribeAudio(blob: Blob): Promise<string> {
  const fd = new FormData();
  const ext = blob.type.includes('mp4')
    ? 'mp4'
    : blob.type.includes('ogg')
      ? 'ogg'
      : blob.type.includes('wav')
        ? 'wav'
        : 'webm';
  fd.append('file', blob, `audio.${ext}`);
  // No explicit content-type — the browser sets the multipart boundary.
  const res = await apiFetch('/api/audio/transcriptions', { method: 'POST', body: fd });
  if (!res.ok) throw new Error(`transcription failed: ${res.status}`);
  return (await res.json()).text ?? '';
}

// Synthesize speech for text; returns an audio Blob to play.
export async function synthesizeSpeech(input: string, voice?: string): Promise<Blob> {
  const res = await apiFetch('/api/audio/speech', {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ input, voice })
  });
  if (!res.ok) throw new Error(`speech failed: ${res.status}`);
  return res.blob();
}

// ---- real-time channels ----

export interface Channel {
  id: string;
  name: string;
  description: string | null;
  created_by: number | null;
  created_at: number;
}

export interface ChannelMessage {
  id: number;
  channel_id: string;
  user_id: number | null;
  username: string;
  content: string;
  created_at: number;
}

export async function listChannels(): Promise<Channel[]> {
  const res = await apiFetch('/api/channels');
  if (!res.ok) return [];
  return res.json();
}

export async function createChannel(name: string, description?: string): Promise<Channel> {
  const res = await apiFetch('/api/channels', {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ name, description: description ?? null })
  });
  if (!res.ok) throw new Error(`create channel failed: ${res.status}`);
  return res.json();
}

export async function getChannel(id: string): Promise<Channel | null> {
  const res = await apiFetch(`/api/channels/${id}`);
  if (!res.ok) return null;
  return res.json();
}

export async function deleteChannel(id: string): Promise<void> {
  await apiFetch(`/api/channels/${id}`, { method: 'DELETE' });
}

export async function listChannelMessages(id: string, before?: number): Promise<ChannelMessage[]> {
  const qs = before != null ? `?before=${before}` : '';
  const res = await apiFetch(`/api/channels/${id}/messages${qs}`);
  if (!res.ok) return [];
  return res.json();
}

export async function postChannelMessage(id: string, content: string): Promise<ChannelMessage> {
  const res = await apiFetch(`/api/channels/${id}/messages`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ content })
  });
  if (!res.ok) throw new Error(`post failed: ${res.status}`);
  return res.json();
}

// Same-origin WebSocket URL for a channel's live feed (the session cookie rides
// along on the handshake).
export function channelSocketUrl(id: string): string {
  const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
  return `${proto}//${location.host}/api/channels/${id}/ws`;
}

export async function listConversations(
  q?: string,
  archived = false,
  tag?: string,
  folderId?: number | null
): Promise<ConversationSummary[]> {
  const params = new URLSearchParams();
  if (q?.trim()) params.set('q', q.trim());
  if (archived) params.set('archived', 'true');
  if (tag) params.set('tag', tag);
  if (folderId != null) params.set('folder_id', String(folderId));
  const qs = params.toString();
  const res = await apiFetch(`/api/conversations${qs ? '?' + qs : ''}`);
  if (!res.ok) return [];
  return res.json();
}

export async function getConversationTags(id: string): Promise<string[]> {
  const res = await apiFetch(`/api/conversations/${id}/tags`);
  if (!res.ok) return [];
  return (await res.json()).tags ?? [];
}

export async function setConversationTags(id: string, tags: string[]): Promise<string[]> {
  const res = await apiFetch(`/api/conversations/${id}/tags`, {
    method: 'PUT',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ tags })
  });
  if (!res.ok) throw new Error(`update failed: ${res.status}`);
  return (await res.json()).tags ?? [];
}

export async function renameConversation(id: string, title: string): Promise<void> {
  await updateConversation(id, { title });
}

export async function setPinned(id: string, pinned: boolean): Promise<void> {
  await updateConversation(id, { pinned });
}

export async function setArchived(id: string, archived: boolean): Promise<void> {
  await updateConversation(id, { archived });
}

export async function getShareToken(conversationId: string): Promise<string | null> {
  const res = await apiFetch(`/api/conversations/${conversationId}/share`);
  if (!res.ok) return null;
  return (await res.json()).token ?? null;
}

export async function createShare(conversationId: string): Promise<string> {
  const res = await apiFetch(`/api/conversations/${conversationId}/share`, { method: 'POST' });
  if (!res.ok) {
    const b = await res.json().catch(() => ({}));
    throw new Error(b.detail ?? `share failed: ${res.status}`);
  }
  return (await res.json()).token;
}

export async function deleteShare(conversationId: string): Promise<void> {
  await apiFetch(`/api/conversations/${conversationId}/share`, { method: 'DELETE' });
}

export interface SharedConversation {
  title: string;
  messages: { role: string; content: MessageContent; sources?: Source[] }[];
}

export async function getSharedConversation(token: string): Promise<SharedConversation | null> {
  const res = await apiFetch(`/api/shared/${token}`);
  if (!res.ok) return null;
  return res.json();
}

export async function autotitle(id: string): Promise<string | null> {
  const res = await apiFetch(`/api/conversations/${id}/autotitle`, { method: 'POST' });
  if (!res.ok) return null;
  return (await res.json()).title ?? null;
}

export async function getFollowups(id: string): Promise<string[]> {
  const res = await apiFetch(`/api/conversations/${id}/followups`, { method: 'POST' });
  if (!res.ok) return [];
  return (await res.json()).suggestions ?? [];
}

// Ask the server to auto-suggest topic tags (no-op unless FREE_WEBUI_AUTO_TAG is
// on); returns the conversation's full tag set.
export async function autotagConversation(id: string): Promise<string[]> {
  const res = await apiFetch(`/api/conversations/${id}/autotag`, { method: 'POST' });
  if (!res.ok) return [];
  return (await res.json()).tags ?? [];
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
  const res = await apiFetch(`/api/conversations/${conversationId}/messages/${messageId}/variants`);
  if (!res.ok) return [];
  return (await res.json()).variants ?? [];
}

export async function activateVariant(conversationId: string, messageId: number): Promise<void> {
  await apiFetch(`/api/conversations/${conversationId}/messages/${messageId}/activate`, {
    method: 'POST'
  });
}

export async function setFeedback(
  conversationId: string,
  messageId: number,
  rating: number
): Promise<{ rating: number | null }> {
  const res = await apiFetch(
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
  const res = await apiFetch('/api/conversations', {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ model })
  });
  if (!res.ok) throw new Error(`create failed: ${res.status}`);
  return res.json();
}

export async function importConversation(data: unknown): Promise<ConversationSummary> {
  const res = await apiFetch('/api/conversations/import', {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(data)
  });
  if (!res.ok) {
    const b = await res.json().catch(() => ({}));
    throw new Error(b.detail ?? `import failed: ${res.status}`);
  }
  return res.json();
}

export async function getConversation(id: string): Promise<Conversation> {
  const res = await apiFetch(`/api/conversations/${id}`);
  if (!res.ok) throw new Error(`load failed: ${res.status}`);
  return res.json();
}

export async function deleteConversation(id: string): Promise<void> {
  await apiFetch(`/api/conversations/${id}`, { method: 'DELETE' });
}

export async function cloneConversation(id: string): Promise<ConversationSummary> {
  const res = await apiFetch(`/api/conversations/${id}/clone`, { method: 'POST' });
  if (!res.ok) throw new Error(`clone failed: ${res.status}`);
  return res.json();
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
  const res = await apiFetch(`/api/conversations/${conversationId}/documents`);
  if (!res.ok) return [];
  return res.json();
}

export async function uploadDocument(
  conversationId: string,
  file: File
): Promise<Document> {
  const fd = new FormData();
  fd.append('file', file);
  const res = await apiFetch(`/api/conversations/${conversationId}/documents`, {
    method: 'POST',
    body: fd
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail ?? `upload failed: ${res.status}`);
  }
  return res.json();
}

export async function addDocumentUrl(
  conversationId: string,
  url: string
): Promise<Document> {
  const res = await apiFetch(`/api/conversations/${conversationId}/documents/url`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ url })
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail ?? `fetch failed: ${res.status}`);
  }
  return res.json();
}

export async function deleteDocument(
  conversationId: string,
  documentId: number
): Promise<void> {
  await apiFetch(
    `/api/conversations/${conversationId}/documents/${documentId}`,
    { method: 'DELETE' }
  );
}

export interface Collection {
  id: number;
  name: string;
  document_count: number;
  created_at: number;
  updated_at: number;
}

export async function listCollections(): Promise<Collection[]> {
  const res = await apiFetch('/api/collections');
  if (!res.ok) return [];
  return res.json();
}

export async function createCollection(name: string): Promise<Collection> {
  const res = await apiFetch('/api/collections', {
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

export async function deleteCollection(id: number): Promise<void> {
  const res = await apiFetch(`/api/collections/${id}`, { method: 'DELETE' });
  if (!res.ok) throw new Error(`delete failed: ${res.status}`);
}

export async function listCollectionDocuments(id: number): Promise<Document[]> {
  const res = await apiFetch(`/api/collections/${id}/documents`);
  if (!res.ok) return [];
  return res.json();
}

export async function uploadCollectionDocument(id: number, file: File): Promise<Document> {
  const fd = new FormData();
  fd.append('file', file);
  const res = await apiFetch(`/api/collections/${id}/documents`, { method: 'POST', body: fd });
  if (!res.ok) {
    const b = await res.json().catch(() => ({}));
    throw new Error(b.detail ?? `upload failed: ${res.status}`);
  }
  return res.json();
}

export async function addCollectionDocumentUrl(id: number, url: string): Promise<Document> {
  const res = await apiFetch(`/api/collections/${id}/documents/url`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ url })
  });
  if (!res.ok) {
    const b = await res.json().catch(() => ({}));
    throw new Error(b.detail ?? `fetch failed: ${res.status}`);
  }
  return res.json();
}

export async function deleteCollectionDocument(collectionId: number, documentId: number): Promise<void> {
  await apiFetch(`/api/collections/${collectionId}/documents/${documentId}`, { method: 'DELETE' });
}

export async function getConversationCollections(conversationId: string): Promise<number[]> {
  const res = await apiFetch(`/api/conversations/${conversationId}/collections`);
  if (!res.ok) return [];
  return (await res.json()).collection_ids ?? [];
}

export async function setConversationCollections(
  conversationId: string,
  collectionIds: number[]
): Promise<number[]> {
  const res = await apiFetch(`/api/conversations/${conversationId}/collections`, {
    method: 'PUT',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ collection_ids: collectionIds })
  });
  if (!res.ok) throw new Error(`update failed: ${res.status}`);
  return (await res.json()).collection_ids ?? [];
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
  const res = await apiFetch('/api/mcp_servers');
  if (!res.ok) return [];
  return res.json();
}

export async function createMcpServer(body: {
  name: string;
  url: string;
  headers?: Record<string, string> | null;
}): Promise<McpServer> {
  const res = await apiFetch('/api/mcp_servers', {
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
  const res = await apiFetch(`/api/mcp_servers/${id}`, {
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
  await apiFetch(`/api/mcp_servers/${id}`, { method: 'DELETE' });
}

export async function probeMcpServer(
  id: number
): Promise<{ tools: { name: string; description?: string }[] }> {
  const res = await apiFetch(`/api/mcp_servers/${id}/probe`, { method: 'POST' });
  if (!res.ok) {
    const b = await res.json().catch(() => ({}));
    throw new Error(b.detail ?? `probe failed: ${res.status}`);
  }
  return res.json();
}

// OpenAPI tool servers share the McpServer shape (id/name/url/headers/enabled).
export type OpenApiServer = McpServer;

export async function listOpenApiServers(): Promise<OpenApiServer[]> {
  const res = await apiFetch('/api/openapi_servers');
  if (!res.ok) return [];
  return res.json();
}

export async function createOpenApiServer(body: {
  name: string;
  url: string;
  headers?: Record<string, string> | null;
}): Promise<OpenApiServer> {
  const res = await apiFetch('/api/openapi_servers', {
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

export async function patchOpenApiServer(
  id: number,
  patch: Partial<{ name: string; url: string; headers: Record<string, string> | null; enabled: boolean }>
): Promise<OpenApiServer> {
  const res = await apiFetch(`/api/openapi_servers/${id}`, {
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

export async function deleteOpenApiServer(id: number): Promise<void> {
  await apiFetch(`/api/openapi_servers/${id}`, { method: 'DELETE' });
}

export interface AdminUser {
  id: number;
  username: string;
  role: string;
  created_at: number;
  disabled: boolean;
}

export async function adminListUsers(): Promise<AdminUser[]> {
  const res = await apiFetch('/api/admin/users');
  if (!res.ok) throw new Error(`load failed: ${res.status}`);
  return res.json();
}

export async function adminCreateUser(
  username: string,
  password: string,
  role: 'admin' | 'user'
): Promise<AdminUser> {
  const res = await apiFetch('/api/admin/users', {
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
  patch: { role?: 'admin' | 'user'; password?: string; disabled?: boolean }
): Promise<AdminUser> {
  const res = await apiFetch(`/api/admin/users/${id}`, {
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
  const res = await apiFetch(`/api/admin/users/${id}`, { method: 'DELETE' });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail ?? `delete failed: ${res.status}`);
  }
}

export interface Connection {
  id: number;
  name: string;
  base_url: string;
  has_api_key: boolean;
  headers: Record<string, string> | null;
  enabled: boolean;
  created_at: number;
  updated_at: number;
}

export interface ConnectionInput {
  name: string;
  base_url: string;
  api_key?: string | null;
  headers?: Record<string, string> | null;
  enabled?: boolean;
}

export async function listConnections(): Promise<Connection[]> {
  const res = await apiFetch('/api/admin/connections');
  if (!res.ok) return [];
  return res.json();
}

export async function createConnection(body: ConnectionInput): Promise<Connection> {
  const res = await apiFetch('/api/admin/connections', {
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

export async function patchConnection(
  id: number,
  patch: Partial<ConnectionInput>
): Promise<Connection> {
  const res = await apiFetch(`/api/admin/connections/${id}`, {
    method: 'PATCH',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(patch)
  });
  if (!res.ok) throw new Error(`update failed: ${res.status}`);
  return res.json();
}

export async function deleteConnection(id: number): Promise<void> {
  const res = await apiFetch(`/api/admin/connections/${id}`, { method: 'DELETE' });
  if (!res.ok) throw new Error(`delete failed: ${res.status}`);
}

export async function testConnection(
  body: ConnectionInput
): Promise<{ ok: boolean; error: string | null; models: string[] }> {
  const res = await apiFetch('/api/admin/connections/test', {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(body)
  });
  if (!res.ok) return { ok: false, error: `request failed: ${res.status}`, models: [] };
  return res.json();
}

export interface Group {
  id: number;
  name: string;
  member_count: number;
  created_at: number;
}

export async function listGroups(): Promise<Group[]> {
  const res = await apiFetch('/api/admin/groups');
  if (!res.ok) return [];
  return res.json();
}

export async function createGroup(name: string): Promise<Group> {
  const res = await apiFetch('/api/admin/groups', {
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
  const res = await apiFetch(`/api/admin/groups/${id}`, { method: 'DELETE' });
  if (!res.ok) throw new Error(`delete failed: ${res.status}`);
}

export async function getGroupMembers(id: number): Promise<number[]> {
  const res = await apiFetch(`/api/admin/groups/${id}/members`);
  if (!res.ok) return [];
  return (await res.json()).user_ids ?? [];
}

export async function setGroupMembers(id: number, userIds: number[]): Promise<number[]> {
  const res = await apiFetch(`/api/admin/groups/${id}/members`, {
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
  const res = await apiFetch('/api/admin/model_access');
  if (!res.ok) return {};
  return res.json();
}

export async function setModelAccess(
  modelId: string,
  groupIds: number[],
  userIds: number[]
): Promise<void> {
  const res = await apiFetch('/api/admin/model_access', {
    method: 'PUT',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ model_id: modelId, group_ids: groupIds, user_ids: userIds })
  });
  if (!res.ok) throw new Error(`update failed: ${res.status}`);
}

// ---- per-feature permission matrix ----

export type Permissions = Record<string, boolean>;

export interface PermissionMatrix {
  permissions: { key: string; label: string; builtin_default: boolean }[];
  defaults: Permissions;
  groups: { id: number; name: string; keys: string[] }[];
}

export async function getMyPermissions(): Promise<Permissions> {
  // On a transient failure this returns {} and callers treat missing keys as
  // allowed (perms.x !== false) — i.e. the UI fails OPEN, showing controls. This
  // is intentional: the backend enforces every permission regardless, so a brief
  // network hiccup must not hide working features behind a false "denied".
  const res = await apiFetch('/api/permissions/me');
  if (!res.ok) return {};
  return res.json();
}

export async function getPermissionMatrix(): Promise<PermissionMatrix> {
  const res = await apiFetch('/api/admin/permissions');
  if (!res.ok) throw new Error(`load failed: ${res.status}`);
  return res.json();
}

export async function setPermissionDefaults(defaults: Permissions): Promise<Permissions> {
  const res = await apiFetch('/api/admin/permissions/defaults', {
    method: 'PUT',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ defaults })
  });
  if (!res.ok) throw new Error(`save failed: ${res.status}`);
  return (await res.json()) as Permissions;
}

export async function setGroupPermissions(gid: number, keys: string[]): Promise<void> {
  const res = await apiFetch(`/api/admin/permissions/groups/${gid}`, {
    method: 'PUT',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ keys })
  });
  if (!res.ok) throw new Error(`save failed: ${res.status}`);
}

// ---- instance appearance (branding + custom CSS) ----

export interface AppConfig {
  instance_name: string;
  custom_css: string;
}

export async function getConfig(): Promise<AppConfig> {
  const res = await apiFetch('/api/config');
  if (!res.ok) return { instance_name: 'free-webui', custom_css: '' };
  return res.json();
}

export async function getAppearance(): Promise<AppConfig> {
  const res = await apiFetch('/api/admin/appearance');
  if (!res.ok) throw new Error(`load failed: ${res.status}`);
  return res.json();
}

export async function setAppearance(instanceName: string, customCss: string): Promise<AppConfig> {
  const res = await apiFetch('/api/admin/appearance', {
    method: 'PUT',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ instance_name: instanceName, custom_css: customCss })
  });
  if (!res.ok) {
    const b = await res.json().catch(() => ({}));
    throw new Error(b.detail ?? `save failed: ${res.status}`);
  }
  return res.json();
}

export interface AuditEntry {
  id: number;
  username: string | null;
  action: string;
  detail: string | null;
  created_at: number;
}

export async function listAudit(limit = 100): Promise<AuditEntry[]> {
  const res = await apiFetch(`/api/admin/audit?limit=${limit}`);
  if (!res.ok) return [];
  return res.json();
}

export interface FeedbackRow {
  id: number;
  rating: number;
  comment: string | null;
  username: string | null;
  conversation_id: string | null;
  conversation_title: string | null;
  snippet: string;
  created_at: number;
}

export async function listFeedback(rating?: number): Promise<FeedbackRow[]> {
  const q = rating === 1 || rating === -1 ? `?rating=${rating}` : '';
  const res = await apiFetch(`/api/admin/feedback${q}`);
  if (!res.ok) return [];
  return res.json();
}

export interface ModelTokens {
  model: string;
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  cost: number | null;
}

export interface Analytics {
  totals: Record<string, number>;
  feedback: { up: number; down: number };
  active_users_7d: number;
  new_users_7d: number;
  messages_per_day: { date: string; count: number }[];
  messages_per_model: { model: string; count: number }[];
  tokens: { prompt: number; completion: number; total: number };
  tokens_per_model: ModelTokens[];
  cost_total: number | null;
}

export async function getAnalytics(days = 30): Promise<Analytics> {
  const res = await apiFetch(`/api/admin/analytics?days=${days}`);
  if (!res.ok) throw new Error(`analytics failed: ${res.status}`);
  return res.json();
}

export type BannerType = 'info' | 'warning' | 'error' | 'success';
export interface Banner {
  id: number;
  content: string;
  type: BannerType;
  dismissible: boolean;
  created_at: number;
}

export async function listBanners(): Promise<Banner[]> {
  const res = await apiFetch('/api/banners');
  if (!res.ok) return [];
  return res.json();
}

export async function createBanner(
  content: string,
  type: BannerType,
  dismissible: boolean
): Promise<Banner> {
  const res = await apiFetch('/api/admin/banners', {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ content, type, dismissible })
  });
  if (!res.ok) throw new Error(`create banner failed: ${res.status}`);
  return res.json();
}

export async function deleteBanner(id: number): Promise<void> {
  await apiFetch(`/api/admin/banners/${id}`, { method: 'DELETE' });
}

export interface PluginRecord {
  name: string;
  priority: number;
  has_inlet: boolean;
  has_outlet: boolean;
  error: string | null;
}

export async function getPlugins(): Promise<PluginRecord[]> {
  const res = await apiFetch('/api/plugins');
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
  const res = await apiFetch('/api/admin/models');
  if (!res.ok) throw new Error(`load failed: ${res.status}`);
  return res.json();
}

export async function deleteInstalledModel(name: string): Promise<void> {
  const res = await apiFetch(`/api/admin/models?name=${encodeURIComponent(name)}`, {
    method: 'DELETE'
  });
  if (!res.ok) throw new Error(`delete failed: ${res.status}`);
}

export interface PullOpts {
  signal?: AbortSignal;
  onEvent: (event: Record<string, unknown>) => void;
}

export async function pullModel(name: string, opts: PullOpts): Promise<void> {
  const res = await apiFetch('/api/admin/models/pull', {
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
  const res = await apiFetch('/api/memories');
  if (!res.ok) return [];
  return res.json();
}

export async function createMemory(content: string): Promise<Memory> {
  const res = await apiFetch('/api/memories', {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ content })
  });
  if (!res.ok) throw new Error(`create memory failed: ${res.status}`);
  return res.json();
}

export async function deleteMemory(id: number): Promise<void> {
  await apiFetch(`/api/memories/${id}`, { method: 'DELETE' });
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
  const res = await apiFetch('/api/api_keys');
  if (!res.ok) return [];
  return res.json();
}

export async function mintApiKey(name: string): Promise<ApiKey> {
  const res = await apiFetch('/api/api_keys', {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ name })
  });
  if (!res.ok) throw new Error(`mint key failed: ${res.status}`);
  return res.json();
}

export async function revokeApiKey(id: number): Promise<void> {
  await apiFetch(`/api/api_keys/${id}`, { method: 'DELETE' });
}

// ---- self-service account ----

export const ACCOUNT_EXPORT_URL = '/api/account/export';

// Permanently delete the caller's own account (password re-auth). Returns true
// on success, or an error message string (e.g. wrong password / only admin).
export async function deleteAccount(password: string): Promise<true | string> {
  const res = await apiFetch('/api/account', {
    method: 'DELETE',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ password })
  });
  if (res.ok) return true;
  try {
    return (await res.json()).detail ?? `failed (${res.status})`;
  } catch {
    return `failed (${res.status})`;
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
  description: string | null;
  tools_enabled: boolean;
  web_search: boolean;
  collection_ids: number[];
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
  description?: string | null;
  tools_enabled?: boolean;
  web_search?: boolean;
  collection_ids?: number[];
}

export async function listPresets(): Promise<Preset[]> {
  const res = await apiFetch('/api/presets');
  if (!res.ok) return [];
  return res.json();
}

export async function createPreset(body: PresetIn): Promise<Preset> {
  const res = await apiFetch('/api/presets', {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(body)
  });
  if (!res.ok) throw new Error(`create preset failed: ${res.status}`);
  return res.json();
}

export async function updatePreset(id: number, body: PresetIn): Promise<Preset> {
  const res = await apiFetch(`/api/presets/${id}`, {
    method: 'PUT',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(body)
  });
  if (!res.ok) throw new Error(`update preset failed: ${res.status}`);
  return res.json();
}

export async function deletePreset(id: number): Promise<void> {
  await apiFetch(`/api/presets/${id}`, { method: 'DELETE' });
}

export interface Prompt {
  id: number;
  title: string;
  content: string;
  created_at: number;
  updated_at: number;
}

export async function listPrompts(): Promise<Prompt[]> {
  const res = await apiFetch('/api/prompts');
  if (!res.ok) return [];
  return res.json();
}

export async function createPrompt(title: string, content: string): Promise<Prompt> {
  const res = await apiFetch('/api/prompts', {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ title, content })
  });
  if (!res.ok) throw new Error(`create prompt failed: ${res.status}`);
  return res.json();
}

export async function deletePrompt(id: number): Promise<void> {
  await apiFetch(`/api/prompts/${id}`, { method: 'DELETE' });
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
  const res = await apiFetch(`/api/conversations/${id}`, {
    method: 'PATCH',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(patch)
  });
  if (!res.ok) throw new Error(`update failed: ${res.status}`);
  return res.json();
}

export async function listModels(): Promise<string[]> {
  const res = await apiFetch('/api/models');
  if (!res.ok) return [];
  const json = await res.json();
  return (json.data ?? []).map((m: { id: string }) => m.id);
}

// Stateless, never-persisted completion: the caller passes the whole transcript.
// Content may be plain text or multimodal parts (text + inline data: images,
// used by the voice/video call mode for vision input).
export interface TemporaryParams {
  system_prompt?: string;
  temperature?: number;
  max_tokens?: number;
}

export async function temporaryChat(
  messages: { role: string; content: string | ContentPart[] }[],
  model: string | null,
  opts: StreamOpts,
  params: TemporaryParams = {}
): Promise<void> {
  const res = await apiFetch('/api/chat/temporary', {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    // undefined params are dropped by JSON.stringify, so unset fields use defaults
    body: JSON.stringify({ messages, model, ...params }),
    signal: opts.signal
  });
  await consumeStream(res, opts);
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
  onSources?: (sources: Source[]) => void;
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
        } else if (eventType === 'sources') {
          if (Array.isArray(json)) opts.onSources?.(json as Source[]);
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
  const res = await apiFetch(`/api/conversations/${conversationId}/messages`, {
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
  const res = await apiFetch(`/api/conversations/${conversationId}/regenerate`, {
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
  const res = await apiFetch(
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

export async function regenerateMessage(
  conversationId: string,
  messageId: number,
  model: string | null,
  opts: StreamOpts
): Promise<void> {
  const res = await apiFetch(
    `/api/conversations/${conversationId}/messages/${messageId}/regenerate`,
    {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ model }),
      signal: opts.signal
    }
  );
  await consumeStream(res, opts);
}

export async function continueMessage(
  conversationId: string,
  messageId: number,
  model: string | null,
  opts: StreamOpts
): Promise<void> {
  const res = await apiFetch(
    `/api/conversations/${conversationId}/messages/${messageId}/continue`,
    {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ model }),
      signal: opts.signal
    }
  );
  await consumeStream(res, opts);
}

export async function deleteMessage(conversationId: string, messageId: number): Promise<void> {
  await apiFetch(`/api/conversations/${conversationId}/messages/${messageId}`, { method: 'DELETE' });
}

// In-place edit of an assistant reply (no truncation, no model rerun).
export async function editAssistantMessage(
  conversationId: string,
  messageId: number,
  content: string
): Promise<void> {
  await apiFetch(`/api/conversations/${conversationId}/messages/${messageId}/content`, {
    method: 'PUT',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ content })
  });
}

// ---- Evaluations: leaderboard + arena ----

export interface LeaderboardRow {
  model: string;
  up: number;
  down: number;
  feedback_count: number;
  rating: number; // Wilson lower bound, 0..1
  elo: number;
  arena_games: number;
  wins: number;
  losses: number;
  ties: number;
}

export type ArenaWinner = 'a' | 'b' | 'tie' | 'both_bad';

export async function getLeaderboard(): Promise<LeaderboardRow[]> {
  const res = await apiFetch('/api/evaluations/leaderboard');
  if (!res.ok) return [];
  return res.json();
}

export async function arenaVote(
  modelA: string,
  modelB: string,
  winner: ArenaWinner,
  prompt?: string
): Promise<void> {
  await apiFetch('/api/evaluations/arena/vote', {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ model_a: modelA, model_b: modelB, winner, prompt })
  });
}

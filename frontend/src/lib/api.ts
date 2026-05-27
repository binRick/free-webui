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
  content: string,
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
  content: string,
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

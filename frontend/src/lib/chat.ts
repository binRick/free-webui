export type Role = 'system' | 'user' | 'assistant';

export interface Message {
  role: Role;
  content: string;
}

export interface StreamChatArgs {
  model: string | null;
  messages: Message[];
  signal?: AbortSignal;
  onDelta: (delta: string) => void;
}

/**
 * POST /api/chat and parse the SSE stream. Calls `onDelta` for each
 * incremental token of the assistant's reply.
 */
export async function streamChat({ model, messages, signal, onDelta }: StreamChatArgs): Promise<void> {
  const res = await fetch('/api/chat', {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ model, messages }),
    signal
  });
  if (!res.ok || !res.body) {
    throw new Error(`chat failed: ${res.status}`);
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buf = '';

  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });

    // SSE events are separated by a blank line.
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
          if (typeof delta === 'string' && delta.length) onDelta(delta);
        } catch {
          // Ignore malformed chunk; upstream sometimes sends keep-alives.
        }
      }
    }
  }
}

export async function listModels(): Promise<string[]> {
  const res = await fetch('/api/models');
  if (!res.ok) return [];
  const json = await res.json();
  return (json.data ?? []).map((m: { id: string }) => m.id);
}

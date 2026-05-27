<script lang="ts">
  import { onMount, tick } from 'svelte';
  import { listModels, streamChat, type Message } from '$lib/chat';

  let models = $state<string[]>([]);
  let model = $state<string | null>(null);
  let messages = $state<Message[]>([]);
  let input = $state('');
  let streaming = $state(false);
  let abort: AbortController | null = null;
  let scroller: HTMLDivElement;

  onMount(async () => {
    models = await listModels();
    if (models.length && !model) model = models[0];
  });

  async function send() {
    const text = input.trim();
    if (!text || streaming) return;
    input = '';
    messages = [...messages, { role: 'user', content: text }, { role: 'assistant', content: '' }];
    streaming = true;
    abort = new AbortController();
    await tick();
    scroller?.scrollTo({ top: scroller.scrollHeight });

    try {
      await streamChat({
        model,
        messages: messages.slice(0, -1),
        signal: abort.signal,
        onDelta: (delta) => {
          const last = messages[messages.length - 1];
          messages[messages.length - 1] = { ...last, content: last.content + delta };
          scroller?.scrollTo({ top: scroller.scrollHeight });
        }
      });
    } catch (err) {
      const last = messages[messages.length - 1];
      messages[messages.length - 1] = {
        ...last,
        content: last.content + `\n\n_error: ${(err as Error).message}_`
      };
    } finally {
      streaming = false;
      abort = null;
    }
  }

  function stop() {
    abort?.abort();
  }

  function newChat() {
    if (streaming) stop();
    messages = [];
  }

  function onKey(e: KeyboardEvent) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      send();
    }
  }
</script>

<header>
  <div class="brand">free-webui</div>
  <div class="controls">
    <select bind:value={model} disabled={streaming}>
      {#if models.length === 0}
        <option value={null}>no models</option>
      {/if}
      {#each models as m}
        <option value={m}>{m}</option>
      {/each}
    </select>
    <button onclick={newChat} disabled={streaming && false}>new chat</button>
  </div>
</header>

<div class="scroller" bind:this={scroller}>
  {#if messages.length === 0}
    <div class="empty">start a conversation ↓</div>
  {/if}
  {#each messages as msg}
    <div class="msg {msg.role}">
      <div class="role">{msg.role}</div>
      <div class="content">{msg.content}</div>
    </div>
  {/each}
</div>

<form class="composer" onsubmit={(e) => { e.preventDefault(); send(); }}>
  <textarea
    placeholder="message…"
    bind:value={input}
    onkeydown={onKey}
    rows="2"
    disabled={streaming}
  ></textarea>
  {#if streaming}
    <button type="button" onclick={stop}>stop</button>
  {:else}
    <button type="submit" disabled={!input.trim()}>send</button>
  {/if}
</form>

<style>
  header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 0.75rem 1rem;
    border-bottom: 1px solid #1e293b;
  }
  .brand {
    font-family: ui-monospace, SFMono-Regular, monospace;
    color: #22d3ee;
    font-weight: 600;
  }
  .controls {
    display: flex;
    gap: 0.5rem;
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
  button {
    cursor: pointer;
  }
  button:disabled {
    opacity: 0.5;
    cursor: not-allowed;
  }
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
  .msg {
    max-width: 760px;
    width: 100%;
    margin: 0 auto;
  }
  .msg .role {
    font-size: 0.75rem;
    text-transform: uppercase;
    color: #64748b;
    margin-bottom: 0.25rem;
  }
  .msg.user .role {
    color: #22d3ee;
  }
  .msg.assistant .role {
    color: #a78bfa;
  }
  .content {
    white-space: pre-wrap;
    line-height: 1.5;
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

<script lang="ts">
  import { onMount, tick } from 'svelte';
  import Markdown from '$lib/Markdown.svelte';
  import ModelPicker from '$lib/ModelPicker.svelte';
  import { toasts } from '$lib/toastStore.svelte';
  import { listModels, temporaryChat } from '$lib/api';

  interface Msg {
    role: 'user' | 'assistant';
    content: string;
  }

  let models = $state<string[]>([]);
  let model = $state<string | null>(null);
  let messages = $state<Msg[]>([]);
  let input = $state('');
  let streaming = $state(false);
  let scroller = $state<HTMLDivElement | null>(null);
  let abort: AbortController | null = null;

  onMount(async () => {
    models = await listModels();
    if (models.length) model = models[0];
  });

  async function scrollToBottom() {
    await tick();
    scroller?.scrollTo({ top: scroller.scrollHeight });
  }

  async function send() {
    const text = input.trim();
    if (!text || streaming) return;
    input = '';
    messages = [...messages, { role: 'user', content: text }, { role: 'assistant', content: '' }];
    await scrollToBottom();

    // Replay the whole transcript except the empty placeholder we just added.
    const transcript = messages.slice(0, -1).map((m) => ({ role: m.role, content: m.content }));
    streaming = true;
    abort = new AbortController();
    try {
      await temporaryChat(transcript, model, {
        signal: abort.signal,
        onDelta: (d) => {
          const last = messages[messages.length - 1];
          messages[messages.length - 1] = { ...last, content: last.content + d };
          scrollToBottom();
        }
      });
    } catch (e) {
      const last = messages[messages.length - 1];
      messages[messages.length - 1] = {
        ...last,
        content: last.content + `\n\n_error: ${(e as Error).message}_`
      };
    } finally {
      streaming = false;
      abort = null;
    }
  }

  function stop() {
    abort?.abort();
  }

  function clearAll() {
    if (streaming) return;
    messages = [];
  }

  function onKey(e: KeyboardEvent) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      send();
    }
  }
</script>

<svelte:head><title>temporary chat · free-webui</title></svelte:head>

<div class="temp">
  <header>
    <a class="back" href="/">←</a>
    <span class="badge" title="this chat is never saved">👻 temporary</span>
    <div class="spacer"></div>
    <ModelPicker {models} value={model} disabled={streaming} onSelect={(m) => (model = m)} />
    {#if messages.length}
      <button class="clear" onclick={clearAll} disabled={streaming} title="clear">clear</button>
    {/if}
  </header>

  <div class="banner">Temporary chat — nothing here is saved. Leaving or refreshing clears it.</div>

  <div class="messages" bind:this={scroller}>
    {#each messages as m, i (i)}
      <div class="msg {m.role}">
        <span class="role">{m.role}</span>
        <div class="content">
          {#if m.role === 'assistant'}
            <Markdown source={m.content} />
          {:else}
            <div class="user-text">{m.content}</div>
          {/if}
        </div>
      </div>
    {:else}
      <div class="empty">start a throwaway conversation 👻</div>
    {/each}
  </div>

  <form class="composer" onsubmit={(e) => { e.preventDefault(); send(); }}>
    <textarea
      placeholder="message… (not saved)"
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
</div>

<style>
  .temp {
    display: flex;
    flex-direction: column;
    height: 100%;
    min-height: 0;
    max-width: 820px;
    margin: 0 auto;
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
  .badge {
    font-size: 0.8rem;
    color: var(--text-dim);
    border: 1px solid var(--border-soft);
    border-radius: 999px;
    padding: 0.15rem 0.6rem;
  }
  .spacer { flex: 1; }
  .clear {
    background: var(--bg-elev);
    color: var(--text);
    border: 1px solid var(--border);
    border-radius: 6px;
    padding: 0.35rem 0.7rem;
    font: inherit;
    cursor: pointer;
  }
  .clear:disabled { opacity: 0.5; cursor: default; }
  .banner {
    font-size: 0.78rem;
    color: var(--text-muted);
    background: color-mix(in srgb, var(--accent) 8%, transparent);
    padding: 0.4rem 1rem;
    border-bottom: 1px solid var(--border-soft);
  }
  .messages {
    flex: 1;
    overflow-y: auto;
    padding: 1rem;
    display: flex;
    flex-direction: column;
    gap: 1rem;
  }
  .msg {
    display: flex;
    flex-direction: column;
    gap: 0.25rem;
  }
  .role {
    font-size: 0.72rem;
    color: var(--text-muted);
    text-transform: uppercase;
    letter-spacing: 0.04em;
  }
  .user-text {
    white-space: pre-wrap;
    overflow-wrap: anywhere;
  }
  .empty {
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

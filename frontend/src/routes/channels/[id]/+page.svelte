<script lang="ts">
  import { tick } from 'svelte';
  import { goto } from '$app/navigation';
  import { page } from '$app/state';
  import { auth } from '$lib/auth.svelte';
  import { toasts } from '$lib/toastStore.svelte';
  import {
    getChannel,
    listChannelMessages,
    postChannelMessage,
    deleteChannel,
    channelSocketUrl,
    type Channel,
    type ChannelMessage
  } from '$lib/api';

  let channel = $state<Channel | null>(null);
  let messages = $state<ChannelMessage[]>([]);
  let text = $state('');
  let online = $state(0);
  let connected = $state(false);
  let typingNames = $state<string[]>([]);
  let scroller = $state<HTMLDivElement | null>(null);

  // Non-reactive connection state (rebuilt per channel by the $effect below).
  let ws: WebSocket | null = null;
  let seen = new Set<number>();
  const typingTimers = new Map<string, ReturnType<typeof setTimeout>>();
  let lastTypingSent = 0;

  const canDelete = $derived(
    !!channel &&
      !!auth.user &&
      (auth.user.role === 'admin' || channel.created_by === auth.user.id)
  );

  async function scrollToBottom() {
    await tick();
    scroller?.scrollTo({ top: scroller.scrollHeight });
  }

  function addMessage(m: ChannelMessage) {
    if (seen.has(m.id)) return;
    seen.add(m.id);
    messages = [...messages, m];
    scrollToBottom();
  }

  function showTyping(username: string) {
    if (username === auth.user?.username) return;
    const t = typingTimers.get(username);
    if (t) clearTimeout(t);
    if (!typingNames.includes(username)) typingNames = [...typingNames, username];
    typingTimers.set(
      username,
      setTimeout(() => {
        typingNames = typingNames.filter((u) => u !== username);
        typingTimers.delete(username);
      }, 3000)
    );
  }

  // Connect (and reconnect) the live socket for whichever channel is in the URL.
  // The $effect cleanup tears the socket down when the id changes or on unmount.
  $effect(() => {
    const channelId = page.params.id ?? '';
    if (!channelId) return;
    let cancelled = false;
    let socket: WebSocket | null = null;
    let reconnectTimer: ReturnType<typeof setTimeout> | undefined;

    async function reloadHistory() {
      const hist = await listChannelMessages(channelId);
      if (cancelled) return;
      seen = new Set(hist.map((m) => m.id));
      messages = hist;
      scrollToBottom();
    }

    function connect() {
      if (cancelled) return;
      socket = new WebSocket(channelSocketUrl(channelId));
      ws = socket;
      socket.onopen = () => {
        if (!cancelled) connected = true;
      };
      socket.onmessage = (e) => {
        let f: any;
        try {
          f = JSON.parse(e.data);
        } catch {
          return;
        }
        if (f.type === 'message') addMessage(f as ChannelMessage);
        else if (f.type === 'presence') online = f.online ?? online;
        else if (f.type === 'typing') showTyping(f.username);
      };
      socket.onclose = () => {
        connected = false;
        if (ws === socket) ws = null;
        if (!cancelled) reconnectTimer = setTimeout(reconnect, 2000);
      };
      socket.onerror = () => socket?.close();
    }

    async function reconnect() {
      if (cancelled) return;
      await reloadHistory(); // catch up anything missed while disconnected
      connect();
    }

    (async () => {
      channel = await getChannel(channelId);
      if (cancelled) return;
      if (!channel) {
        toasts.error('channel not found');
        return;
      }
      await reloadHistory();
      connect();
    })();

    return () => {
      cancelled = true;
      if (reconnectTimer) clearTimeout(reconnectTimer);
      for (const t of typingTimers.values()) clearTimeout(t);
      typingTimers.clear();
      socket?.close();
      ws = null;
      connected = false;
    };
  });

  function send() {
    const content = text.trim();
    const id = page.params.id;
    if (!content || !id) return;
    text = '';
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ type: 'message', content }));
    } else {
      // Socket down — fall back to REST; dedupe renders it once.
      postChannelMessage(id, content)
        .then(addMessage)
        .catch(() => toasts.error('send failed'));
    }
  }

  function onComposerInput() {
    const now = Date.now();
    if (ws && ws.readyState === WebSocket.OPEN && now - lastTypingSent > 2000) {
      lastTypingSent = now;
      ws.send(JSON.stringify({ type: 'typing' }));
    }
  }

  function onKey(e: KeyboardEvent) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      send();
    }
  }

  async function removeChannel() {
    if (!channel || !confirm(`delete #${channel.name}? all messages are lost.`)) return;
    await deleteChannel(channel.id);
    goto('/channels');
  }

  function fmtTime(ts: number): string {
    return new Date(ts * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  }
</script>

<svelte:head><title>{channel ? `#${channel.name}` : 'channel'} · free-webui</title></svelte:head>

<div class="room">
  <header>
    <a class="back" href="/channels">←</a>
    <div class="title">
      <span class="hash">#</span>{channel?.name ?? '…'}
      {#if channel?.description}<span class="desc">{channel.description}</span>{/if}
    </div>
    <span class="status" class:on={connected} title={connected ? 'live' : 'reconnecting…'}>
      {connected ? `● ${online} online` : '○ reconnecting…'}
    </span>
    {#if canDelete}
      <button class="del" title="delete channel" onclick={removeChannel}>🗑</button>
    {/if}
  </header>

  <div class="messages" bind:this={scroller}>
    {#each messages as m (m.id)}
      <div class="msg" class:mine={m.user_id === auth.user?.id}>
        <div class="meta">
          <span class="author">{m.username}</span>
          <span class="time">{fmtTime(m.created_at)}</span>
        </div>
        <div class="body">{m.content}</div>
      </div>
    {:else}
      <div class="empty">no messages yet — say hello 👋</div>
    {/each}
  </div>

  <div class="typing">
    {#if typingNames.length}
      {typingNames.join(', ')}
      {typingNames.length === 1 ? 'is' : 'are'} typing…
    {/if}
  </div>

  <form class="composer" onsubmit={(e) => { e.preventDefault(); send(); }}>
    <textarea
      placeholder="message #{channel?.name ?? ''}"
      bind:value={text}
      oninput={onComposerInput}
      onkeydown={onKey}
      rows="1"
    ></textarea>
    <button type="submit" disabled={!text.trim()}>send</button>
  </form>
</div>

<style>
  .room {
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
  .title {
    flex: 1;
    font-weight: 600;
    display: flex;
    align-items: baseline;
    gap: 0.5rem;
    overflow: hidden;
  }
  .hash { color: var(--text-muted); }
  .desc {
    color: var(--text-dim);
    font-weight: 400;
    font-size: 0.8rem;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .status {
    font-size: 0.75rem;
    color: var(--text-muted);
  }
  .status.on { color: #3fa66a; }
  .del {
    background: transparent;
    border: none;
    cursor: pointer;
    font-size: 0.95rem;
  }
  .messages {
    flex: 1;
    overflow-y: auto;
    padding: 1rem;
    display: flex;
    flex-direction: column;
    gap: 0.6rem;
  }
  .msg {
    max-width: 75%;
    align-self: flex-start;
  }
  .msg.mine {
    align-self: flex-end;
    text-align: right;
  }
  .meta {
    display: flex;
    gap: 0.4rem;
    align-items: baseline;
    font-size: 0.72rem;
    color: var(--text-muted);
    margin-bottom: 0.1rem;
  }
  .msg.mine .meta { justify-content: flex-end; }
  .author { font-weight: 600; color: var(--text-dim); }
  .body {
    display: inline-block;
    background: var(--bg-elev);
    border: 1px solid var(--border-soft);
    border-radius: 10px;
    padding: 0.45rem 0.7rem;
    white-space: pre-wrap;
    overflow-wrap: anywhere;
    text-align: left;
  }
  .msg.mine .body {
    background: color-mix(in srgb, var(--accent) 18%, var(--bg-elev));
    border-color: var(--accent);
  }
  .empty {
    margin: auto;
    color: var(--text-muted);
  }
  .typing {
    height: 1.1rem;
    padding: 0 1rem;
    font-size: 0.75rem;
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

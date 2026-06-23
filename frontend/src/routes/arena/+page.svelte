<script lang="ts">
  import { onMount } from 'svelte';
  import Markdown from '$lib/Markdown.svelte';
  import { listModels, temporaryChat, arenaVote, type ArenaWinner } from '$lib/api';

  let allModels = $state<string[]>([]);
  let prompt = $state('');

  // One blind round: two anonymised models answer the same prompt.
  let modelA = $state<string | null>(null);
  let modelB = $state<string | null>(null);
  let textA = $state('');
  let textB = $state('');
  let streaming = $state(false);
  let revealed = $state(false); // model identities shown after a vote
  let voted = $state(false);
  let lastPrompt = $state('');

  const ready = $derived(allModels.length >= 2);
  const canVote = $derived(!!modelA && !streaming && !voted);

  onMount(async () => {
    allModels = await listModels();
  });

  // Two distinct models, randomly ordered, so left/right leaks no identity.
  function pickPair(): [string, string] {
    const pool = [...allModels];
    const i = Math.floor(Math.random() * pool.length);
    const a = pool.splice(i, 1)[0];
    const j = Math.floor(Math.random() * pool.length);
    const b = pool.splice(j, 1)[0];
    return [a, b];
  }

  async function battle() {
    const text = prompt.trim();
    if (!text || !ready || streaming) return;
    const [a, b] = pickPair();
    modelA = a;
    modelB = b;
    textA = '';
    textB = '';
    revealed = false;
    voted = false;
    lastPrompt = text;
    streaming = true;
    const turn = [{ role: 'user', content: text }];
    await Promise.all([
      temporaryChat(turn, a, { onDelta: (d) => (textA += d) }).catch(
        (e) => (textA += `\n\n_error: ${(e as Error).message}_`)
      ),
      temporaryChat(turn, b, { onDelta: (d) => (textB += d) }).catch(
        (e) => (textB += `\n\n_error: ${(e as Error).message}_`)
      )
    ]);
    streaming = false;
  }

  async function vote(winner: ArenaWinner) {
    if (!canVote || !modelA || !modelB) return;
    voted = true;
    revealed = true;
    try {
      await arenaVote(modelA, modelB, winner, lastPrompt);
    } catch {
      // best-effort: the reveal already happened
    }
  }

  function next() {
    prompt = '';
    modelA = null;
    modelB = null;
    textA = '';
    textB = '';
    revealed = false;
    voted = false;
  }

  function onKey(e: KeyboardEvent) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      battle();
    }
  }
</script>

<svelte:head><title>arena · free-webui</title></svelte:head>

<div class="arena">
  <header>
    <a class="back" href="/">←</a>
    <span class="title">⚔ model arena</span>
    <span class="hint">blind A/B vote · nothing saved as a chat</span>
    <div class="spacer"></div>
    <a class="ghost" href="/evaluations">🏆 leaderboard</a>
  </header>

  {#if !ready}
    <p class="empty">The arena needs at least two available models to compare.</p>
  {:else}
    <div class="panes">
      {#each [{ side: 'A', model: modelA, text: textA }, { side: 'B', model: modelB, text: textB }] as p (p.side)}
        <section class="pane">
          <div class="pane-head">
            <span class="label">Model {p.side}</span>
            {#if revealed && p.model}<span class="reveal">{p.model}</span>{/if}
          </div>
          <div class="pane-body">
            {#if p.text}
              <Markdown source={p.text} reasoning />
            {:else}
              <div class="pane-empty">{streaming ? '…' : 'enter a prompt below to start a battle'}</div>
            {/if}
          </div>
        </section>
      {/each}
    </div>

    {#if modelA && !streaming}
      <div class="vote">
        {#if voted}
          <span class="voted">vote recorded — identities revealed above</span>
          <button class="primary" onclick={next}>next battle →</button>
        {:else}
          <button onclick={() => vote('a')}>👈 A is better</button>
          <button onclick={() => vote('tie')}>🤝 tie</button>
          <button onclick={() => vote('both_bad')}>👎 both bad</button>
          <button onclick={() => vote('b')}>B is better 👉</button>
        {/if}
      </div>
    {/if}

    <form class="composer" onsubmit={(e) => { e.preventDefault(); battle(); }}>
      <textarea
        placeholder="prompt both models with the same question…"
        bind:value={prompt}
        onkeydown={onKey}
        rows="2"
        disabled={streaming}
      ></textarea>
      <button type="submit" disabled={streaming || !prompt.trim()}>
        {streaming ? '…' : modelA ? 're-roll' : 'battle'}
      </button>
    </form>
  {/if}
</div>

<style>
  .arena {
    display: flex;
    flex-direction: column;
    height: 100%;
    min-height: 0;
    width: 100%;
  }
  header {
    display: flex;
    align-items: center;
    gap: 0.6rem;
    padding: 0.75rem 1rem;
    border-bottom: 1px solid var(--border-soft);
  }
  .back { color: var(--text-dim); text-decoration: none; font-size: 1.1rem; }
  .back:hover { color: var(--text); }
  .title { font-weight: 600; }
  .hint { color: var(--text-muted); font-size: 0.78rem; }
  .spacer { flex: 1; }
  .ghost {
    background: var(--bg-elev);
    color: var(--text);
    border: 1px solid var(--border);
    border-radius: 6px;
    padding: 0.35rem 0.7rem;
    font: inherit;
    text-decoration: none;
  }
  .ghost:hover { border-color: var(--accent); }
  .empty { margin: 2rem auto; color: var(--text-muted); }
  .panes {
    flex: 1;
    min-height: 0;
    display: flex;
    gap: 1px;
    background: var(--border-soft);
    overflow: hidden;
  }
  .pane {
    flex: 1;
    min-width: 0;
    display: flex;
    flex-direction: column;
    background: var(--bg);
    min-height: 0;
  }
  .pane-head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 0.4rem;
    padding: 0.5rem 0.7rem;
    border-bottom: 1px solid var(--border-soft);
  }
  .label { font-weight: 600; letter-spacing: 0.03em; }
  .reveal {
    font-size: 0.8rem;
    color: var(--accent);
    overflow-wrap: anywhere;
  }
  .pane-body { flex: 1; overflow-y: auto; padding: 0.85rem; }
  .pane-empty { color: var(--text-muted); margin: auto; text-align: center; }
  .vote {
    display: flex;
    gap: 0.5rem;
    justify-content: center;
    align-items: center;
    flex-wrap: wrap;
    padding: 0.6rem 1rem;
    border-top: 1px solid var(--border-soft);
  }
  .vote button {
    background: var(--bg-elev);
    color: var(--text);
    border: 1px solid var(--border);
    border-radius: 6px;
    padding: 0.4rem 0.8rem;
    font: inherit;
    cursor: pointer;
  }
  .vote button:hover { border-color: var(--accent); }
  .vote .primary { background: var(--accent); color: #fff; border-color: var(--accent); }
  .voted { color: var(--text-muted); font-size: 0.85rem; }
  .composer {
    display: flex;
    gap: 0.5rem;
    padding: 0.75rem 1rem 1rem;
    border-top: 1px solid var(--border-soft);
  }
  textarea {
    flex: 1;
    resize: none;
    background: var(--bg-elev);
    color: var(--text);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 0.5rem 0.7rem;
    font: inherit;
  }
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

<script lang="ts">
  import { onMount } from 'svelte';
  import { getLeaderboard, type LeaderboardRow } from '$lib/api';

  let rows = $state<LeaderboardRow[]>([]);
  let loading = $state(true);

  onMount(async () => {
    rows = await getLeaderboard();
    loading = false;
  });

  // A model that has arena games is ranked by ELO; pure-feedback models show "—".
  function eloCell(r: LeaderboardRow): string {
    return r.arena_games ? String(r.elo) : '—';
  }
  function pct(r: LeaderboardRow): string {
    return r.feedback_count ? `${Math.round(r.rating * 100)}%` : '—';
  }
</script>

<svelte:head><title>leaderboard · free-webui</title></svelte:head>

<div class="page">
  <header>
    <a class="back" href="/">←</a>
    <span class="title">🏆 model leaderboard</span>
    <span class="hint">ranked by arena ELO, then feedback</span>
    <div class="spacer"></div>
    <a class="ghost" href="/arena">⚔ open arena</a>
  </header>

  {#if loading}
    <p class="empty">loading…</p>
  {:else if rows.length === 0}
    <p class="empty">
      No evaluation data yet. Rate replies with 👍/👎, or run blind battles in the
      <a href="/arena">arena</a> to build the leaderboard.
    </p>
  {:else}
    <div class="table-wrap">
      <table>
        <thead>
          <tr>
            <th class="rank">#</th>
            <th>model</th>
            <th class="num" title="arena ELO rating">ELO</th>
            <th class="num" title="arena wins / losses / ties">W / L / T</th>
            <th class="num" title="positive feedback rate (Wilson lower bound)">rating</th>
            <th class="num" title="👍 / 👎 on this model's replies">👍 / 👎</th>
          </tr>
        </thead>
        <tbody>
          {#each rows as r, i (r.model)}
            <tr>
              <td class="rank">{i + 1}</td>
              <td class="model">{r.model}</td>
              <td class="num elo">{eloCell(r)}</td>
              <td class="num dim">{r.wins} / {r.losses} / {r.ties}</td>
              <td class="num">{pct(r)}</td>
              <td class="num dim">{r.up} / {r.down}</td>
            </tr>
          {/each}
        </tbody>
      </table>
    </div>
  {/if}
</div>

<style>
  .page {
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
  .empty { margin: 2rem auto; color: var(--text-muted); max-width: 32rem; text-align: center; }
  .empty a { color: var(--accent); }
  .table-wrap { flex: 1; overflow-y: auto; padding: 1rem; }
  table { width: 100%; max-width: 52rem; margin: 0 auto; border-collapse: collapse; font-size: 0.9rem; }
  th, td { text-align: left; padding: 0.5rem 0.7rem; border-bottom: 1px solid var(--border-soft); }
  th { color: var(--text-muted); font-weight: 600; font-size: 0.78rem; }
  .num { text-align: right; font-variant-numeric: tabular-nums; }
  .rank { width: 2rem; color: var(--text-muted); text-align: right; }
  .model { font-weight: 600; overflow-wrap: anywhere; }
  .elo { font-weight: 600; }
  .dim { color: var(--text-muted); }
  tbody tr:hover { background: var(--bg-elev); }
</style>

<script lang="ts">
  import { onMount } from 'svelte';
  import { goto } from '$app/navigation';
  import { auth } from '$lib/auth.svelte';
  import { getAnalytics, type Analytics } from '$lib/api';

  let data = $state<Analytics | null>(null);
  let days = $state(30);
  let loadError = $state<string | null>(null);

  onMount(async () => {
    if (!auth.loaded) await auth.refresh();
    if (!auth.user) {
      await goto('/login', { replaceState: true });
      return;
    }
    if (auth.user.role !== 'admin') {
      await goto('/', { replaceState: true });
      return;
    }
    await load();
  });

  async function load() {
    try {
      data = await getAnalytics(days);
      loadError = null;
    } catch (e) {
      loadError = (e as Error).message;
    }
  }

  async function setDays(d: number) {
    days = d;
    await load();
  }

  const maxDay = $derived(Math.max(1, ...(data?.messages_per_day.map((d) => d.count) ?? [0])));
  const maxModel = $derived(Math.max(1, ...(data?.messages_per_model.map((m) => m.count) ?? [0])));
  const feedbackTotal = $derived((data?.feedback.up ?? 0) + (data?.feedback.down ?? 0));

  function shortDate(iso: string): string {
    // "2026-06-15" -> "6/15"
    const [, m, d] = iso.split('-');
    return `${Number(m)}/${Number(d)}`;
  }
</script>

<svelte:head><title>analytics · free-webui</title></svelte:head>

<div class="analytics">
  <header>
    <a class="back" href="/">← chat</a>
    <h1>analytics</h1>
    <div class="range">
      {#each [7, 30, 90] as d (d)}
        <button class:on={days === d} onclick={() => setDays(d)}>{d}d</button>
      {/each}
    </div>
  </header>

  {#if loadError}
    <div class="error">{loadError}</div>
  {:else if !data}
    <div class="loading">loading…</div>
  {:else}
    <section class="cards">
      <div class="card"><span class="n">{data.totals.users}</span><span class="l">users</span></div>
      <div class="card"><span class="n">{data.totals.conversations}</span><span class="l">conversations</span></div>
      <div class="card"><span class="n">{data.totals.messages}</span><span class="l">messages</span></div>
      <div class="card"><span class="n">{data.totals.channels}</span><span class="l">channels</span></div>
      <div class="card"><span class="n">{data.active_users_7d}</span><span class="l">active users · 7d</span></div>
      <div class="card"><span class="n">{data.new_users_7d}</span><span class="l">new users · 7d</span></div>
    </section>

    <section class="block">
      <h2>messages per day</h2>
      {#if data.messages_per_day.every((d) => d.count === 0)}
        <div class="muted">no messages in this window</div>
      {:else}
        <div class="chart" style="--n: {data.messages_per_day.length}">
          {#each data.messages_per_day as d (d.date)}
            <div class="bar-wrap" title="{d.date}: {d.count}">
              <div class="bar" style="height: {(d.count / maxDay) * 100}%"></div>
            </div>
          {/each}
        </div>
        <div class="axis">
          <span>{shortDate(data.messages_per_day[0].date)}</span>
          <span>{shortDate(data.messages_per_day[data.messages_per_day.length - 1].date)}</span>
        </div>
      {/if}
    </section>

    <div class="two">
      <section class="block">
        <h2>top models</h2>
        {#if data.messages_per_model.length === 0}
          <div class="muted">no assistant messages yet</div>
        {:else}
          <ul class="hbars">
            {#each data.messages_per_model as m (m.model)}
              <li>
                <span class="hb-label" title={m.model}>{m.model}</span>
                <span class="hb-track"><span class="hb-fill" style="width: {(m.count / maxModel) * 100}%"></span></span>
                <span class="hb-n">{m.count}</span>
              </li>
            {/each}
          </ul>
        {/if}
      </section>

      <section class="block">
        <h2>feedback</h2>
        {#if feedbackTotal === 0}
          <div class="muted">no ratings yet</div>
        {:else}
          <div class="fb">
            <div class="fb-row"><span>👍 {data.feedback.up}</span><span>👎 {data.feedback.down}</span></div>
            <div class="fb-bar">
              <span class="fb-up" style="width: {(data.feedback.up / feedbackTotal) * 100}%"></span>
            </div>
            <div class="muted">{Math.round((data.feedback.up / feedbackTotal) * 100)}% positive · {feedbackTotal} total</div>
          </div>
        {/if}
      </section>
    </div>
  {/if}
</div>

<style>
  .analytics {
    max-width: 900px;
    margin: 0 auto;
    width: 100%;
    padding: 1rem;
    overflow-y: auto;
  }
  header {
    display: flex;
    align-items: center;
    gap: 1rem;
    margin-bottom: 1rem;
  }
  header h1 { flex: 1; font-size: 1.2rem; margin: 0; }
  .back { color: var(--text-dim); text-decoration: none; font-size: 0.85rem; }
  .back:hover { color: var(--text); }
  .range { display: flex; gap: 0.25rem; }
  .range button {
    background: var(--bg-elev);
    color: var(--text-dim);
    border: 1px solid var(--border-soft);
    border-radius: 6px;
    padding: 0.25rem 0.55rem;
    font: inherit;
    font-size: 0.8rem;
    cursor: pointer;
  }
  .range button.on { color: var(--accent); border-color: var(--accent); }
  .error { color: #d8584a; padding: 1rem; }
  .loading, .muted { color: var(--text-muted); padding: 0.5rem 0; font-size: 0.85rem; }
  .cards {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(130px, 1fr));
    gap: 0.6rem;
    margin-bottom: 1.5rem;
  }
  .card {
    display: flex;
    flex-direction: column;
    gap: 0.2rem;
    padding: 0.8rem;
    background: var(--bg-elev);
    border: 1px solid var(--border-soft);
    border-radius: 8px;
  }
  .card .n { font-size: 1.5rem; font-weight: 700; }
  .card .l { color: var(--text-dim); font-size: 0.75rem; }
  .block {
    margin-bottom: 1.5rem;
    background: var(--bg-elev);
    border: 1px solid var(--border-soft);
    border-radius: 8px;
    padding: 1rem;
  }
  .block h2 { font-size: 0.9rem; margin: 0 0 0.75rem; }
  .chart {
    display: grid;
    grid-template-columns: repeat(var(--n), 1fr);
    align-items: end;
    gap: 2px;
    height: 140px;
  }
  .bar-wrap { height: 100%; display: flex; align-items: flex-end; }
  .bar {
    width: 100%;
    min-height: 1px;
    background: var(--accent);
    border-radius: 2px 2px 0 0;
    opacity: 0.85;
  }
  .bar-wrap:hover .bar { opacity: 1; }
  .axis {
    display: flex;
    justify-content: space-between;
    color: var(--text-muted);
    font-size: 0.7rem;
    margin-top: 0.35rem;
  }
  .two {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
    gap: 1rem;
  }
  .hbars { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 0.5rem; }
  .hbars li { display: grid; grid-template-columns: 110px 1fr auto; align-items: center; gap: 0.5rem; }
  .hb-label { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 0.8rem; }
  .hb-track { background: var(--bg-sidebar); border-radius: 4px; height: 0.7rem; overflow: hidden; }
  .hb-fill { display: block; height: 100%; background: var(--accent); border-radius: 4px; }
  .hb-n { color: var(--text-dim); font-size: 0.8rem; font-variant-numeric: tabular-nums; }
  .fb-row { display: flex; justify-content: space-between; margin-bottom: 0.4rem; font-size: 0.9rem; }
  .fb-bar { background: #d8584a; border-radius: 4px; height: 0.7rem; overflow: hidden; }
  .fb-up { display: block; height: 100%; background: #3fa66a; }
  .fb .muted { margin-top: 0.4rem; }
</style>

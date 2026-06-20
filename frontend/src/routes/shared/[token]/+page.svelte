<script lang="ts">
  import { onMount } from 'svelte';
  import { page } from '$app/state';
  import Markdown from '$lib/Markdown.svelte';
  import { appConfig } from '$lib/appConfig.svelte';
  import { getSharedConversation, type SharedConversation } from '$lib/api';

  let conv = $state<SharedConversation | null>(null);
  let notFound = $state(false);

  onMount(async () => {
    const c = await getSharedConversation(page.params.token!);
    if (c) conv = c;
    else notFound = true;
  });
</script>

<svelte:head>
  <title>{conv?.title ?? 'shared conversation'} · free-webui</title>
  <meta name="robots" content="noindex" />
</svelte:head>

<div class="page">
  <header>
    <span class="brand">{appConfig.instanceName}</span>
    <span class="tag">shared · read-only</span>
  </header>

  {#if notFound}
    <div class="empty">This shared conversation was not found or is no longer available.</div>
  {:else if !conv}
    <div class="empty">loading…</div>
  {:else}
    <h1>{conv.title}</h1>
    <div class="thread">
      {#each conv.messages as m, i (i)}
        <div class="msg {m.role}">
          <span class="role">{m.role}</span>
          <div class="content">
            {#if typeof m.content === 'string'}
              <Markdown source={m.content} sources={m.sources ?? []} />
            {:else}
              {#each m.content as part}
                {#if part.type === 'text'}
                  <Markdown source={part.text} sources={m.sources ?? []} />
                {:else if part.type === 'image_url'}
                  <img class="attached" src={part.image_url.url} alt="attachment" />
                {/if}
              {/each}
            {/if}
          </div>
        </div>
      {/each}
    </div>
  {/if}
</div>

<style>
  .page { max-width: 760px; margin: 0 auto; padding: 1.5rem 1.25rem 4rem; }
  header {
    display: flex;
    align-items: center;
    gap: 0.75rem;
    padding-bottom: 1rem;
    border-bottom: 1px solid var(--border-soft);
    margin-bottom: 1.25rem;
  }
  .brand { color: var(--accent); font-family: ui-monospace, monospace; font-weight: 600; }
  .tag { color: var(--text-muted); font-size: 0.78rem; }
  h1 { font-size: 1.2rem; margin: 0 0 1.25rem; }
  .thread { display: flex; flex-direction: column; gap: 1.25rem; }
  .msg { display: flex; flex-direction: column; gap: 0.3rem; }
  .role {
    font-size: 0.7rem;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    color: var(--text-muted);
  }
  .msg.user .role { color: var(--accent-2); }
  .content { line-height: 1.5; word-wrap: break-word; }
  .attached { max-width: 100%; border-radius: 8px; margin-top: 0.5rem; }
  .empty { color: var(--text-muted); text-align: center; padding: 3rem 1rem; }
</style>

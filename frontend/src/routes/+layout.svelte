<script lang="ts">
  import Sidebar from '$lib/Sidebar.svelte';
  let { children } = $props();
</script>

<div class="shell">
  <Sidebar />
  <section class="main">
    {@render children()}
  </section>
</div>

<style>
  :global(html, body) {
    margin: 0;
    padding: 0;
    height: 100%;
    background: #0b1020;
    color: #e2e8f0;
    font-family: ui-sans-serif, system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif;
  }
  .shell {
    display: flex;
    height: 100vh;
    width: 100%;
  }
  .main {
    flex: 1;
    display: flex;
    flex-direction: column;
    min-width: 0;
  }
</style>

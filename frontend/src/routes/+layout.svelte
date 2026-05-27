<script lang="ts">
  let { children } = $props();
</script>

<main>
  {@render children()}
</main>

<style>
  :global(html, body) {
    margin: 0;
    padding: 0;
    height: 100%;
    background: #0b1020;
    color: #e2e8f0;
    font-family: ui-sans-serif, system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif;
  }
  main {
    height: 100vh;
    display: flex;
    flex-direction: column;
  }
</style>

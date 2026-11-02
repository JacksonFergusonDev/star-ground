<script lang="ts">
  // The head of an app page: where it sits, its title, what it's for, and its
  // main actions. Moving to a page focuses its title, so a screen reader
  // announces where the reader arrived.
  import type { Snippet } from 'svelte';

  let {
    title,
    lede,
    crumbs,
    actions,
    children,
  }: {
    title: string;
    lede?: string;
    /** Links to the pages above this one, as list items. */
    crumbs?: Snippet;
    actions?: Snippet;
    /** Anything under the title, such as a project's tabs. */
    children?: Snippet;
  } = $props();
</script>

<header class="app-head">
  {#if crumbs}
    <nav aria-label="Breadcrumb">
      <ol class="app-crumbs">{@render crumbs()}</ol>
    </nav>
  {/if}
  <div class="app-head-row">
    <div>
      <h1 class="app-title" tabindex="-1">{title}</h1>
      {#if lede}<p class="app-lede">{lede}</p>{/if}
    </div>
    {#if actions}<div class="app-actions">{@render actions()}</div>{/if}
  </div>
  {@render children?.()}
</header>

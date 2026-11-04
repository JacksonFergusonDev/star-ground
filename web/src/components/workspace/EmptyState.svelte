<script lang="ts">
  // A page with nothing on it yet: the hero's board, still and dimmed, what
  // will appear here, and the one thing to do next.
  import type { Snippet } from 'svelte';
  import BoardStill from './BoardStill.svelte';

  let {
    title,
    level = 2,
    action,
    children,
  }: {
    title: string;
    /** The heading's level, under the page's own headings. */
    level?: 2 | 3;
    action?: Snippet;
    children: Snippet;
  } = $props();
</script>

<section class="empty">
  <div class="board"><BoardStill /></div>
  <div class="copy">
    <svelte:element this={`h${level}`}>{title}</svelte:element>
    <p>{@render children()}</p>
    {#if action}<div class="action">{@render action()}</div>{/if}
  </div>
</section>

<style>
  .empty {
    display: grid;
    overflow: hidden;
    border: 1px solid var(--line);
    border-radius: 5px;
    background: var(--panel);
  }
  .board {
    height: 150px;
    border-bottom: 1px solid var(--line);
  }
  .copy {
    display: grid;
    gap: 10px;
    justify-items: start;
    padding: 22px 26px 26px;
  }
  .copy :global(h2),
  .copy :global(h3) {
    max-width: none;
    margin: 0;
    font-size: var(--fs-h4);
    font-weight: 500;
    letter-spacing: 0;
  }
  p {
    max-width: 56ch;
    margin: 0;
    color: var(--muted);
    font-size: var(--fs-ui);
    line-height: 1.7;
  }
  .action {
    margin-top: 6px;
  }
</style>

<script lang="ts">
  // Copies a link that adds these projects to another browser's workspace.
  import { shareable, shareLink } from '../../lib/share';
  import type { Card } from '../../lib/workspace.svelte';
  import { count } from '../../lib/workspace.svelte';

  let { cards }: { cards: Card[] } = $props();

  const shared = $derived(shareable(cards));
  let label = $state<'Copy a share link' | 'Copied!' | 'Failed'>('Copy a share link');
  // The link, shown to copy by hand when the clipboard refuses it.
  let fallback = $state<string | null>(null);

  async function copy(): Promise<void> {
    const link = await shareLink(shared.projects, window.location.origin);
    try {
      await navigator.clipboard.writeText(link);
      label = 'Copied!';
      fallback = null;
    } catch {
      label = 'Failed';
      fallback = link;
    }
    setTimeout(() => (label = 'Copy a share link'), 2000);
  }
</script>

<section class="share" aria-labelledby="share-title">
  <h3 id="share-title">Share</h3>
  <button class="hs-button" type="button" disabled={!shared.projects.length} onclick={copy}>
    {label}
  </button>
  <p class="detail">
    Opening the link adds these projects to that browser's workspace, with their counts, builds and
    corrections.
    {#if shared.uploads}
      Uploaded documents stay in this browser, so {count(shared.uploads, 'upload')}
      {shared.uploads === 1 ? "isn't" : "aren't"} in the link.
    {/if}
  </p>
  {#if fallback}
    <label class="fallback">
      <span>Copy this link</span>
      <input readonly value={fallback} onfocus={(e) => e.currentTarget.select()} />
    </label>
  {/if}
</section>

<style>
  .share {
    display: grid;
    gap: 10px;
    justify-items: start;
  }
  h3 {
    margin: 0;
    font-size: var(--fs-h4);
    font-weight: 500;
  }
  .detail {
    margin: 0;
    color: var(--muted);
    font-size: var(--fs-ui);
    line-height: 1.6;
  }
  .fallback {
    display: grid;
    gap: 6px;
    width: 100%;
  }
  .fallback span {
    color: var(--muted);
    font-family: var(--mono);
    font-size: var(--fs-label);
    letter-spacing: 0.08em;
    text-transform: uppercase;
  }
  .fallback input {
    width: 100%;
    padding: 10px 12px;
    border: 1px solid var(--line);
    border-radius: 5px;
    color: var(--text);
    background: var(--panel);
    font-family: var(--mono);
    font-size: var(--fs-ui);
  }
  button:focus-visible,
  input:focus-visible {
    outline: 2px solid var(--accent);
    outline-offset: 2px;
  }
</style>

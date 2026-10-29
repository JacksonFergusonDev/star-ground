<script lang="ts">
  // Checking a project against its document: the pages on one side, each part
  // outlined where it was read, and the parts on the other, in the
  // document's order. Choosing a part in either place shows it in the other.
  import { onMount } from 'svelte';
  import type { ProjectResult } from '../../lib/api';
  import { readingOrder } from '../../lib/reading-order';
  import type { Card, Check } from '../../lib/workspace.svelte';
  import { checksOf, count } from '../../lib/workspace.svelte';
  import type { Box } from './DocumentViewer.svelte';
  import DocumentViewer from './DocumentViewer.svelte';
  import Icon from './Icon.svelte';
  import ReviewPart from './ReviewPart.svelte';

  let {
    card,
    project,
    focus,
    load,
    onCheck,
    onBack,
  }: {
    card: Card;
    /** The project as the master list has it; null while it updates. */
    project: ProjectResult | null;
    /** The part to show first, as a warning's link asks. */
    focus: string | null;
    load: () => Promise<ArrayBuffer>;
    onCheck: (ref: string, check: Check | null) => void;
    onBack: () => void;
  } = $props();

  // The part shown first; the builder chooses after.
  // svelte-ignore state_referenced_locally
  let selected = $state<string | null>(focus);
  let onlyFlagged = $state(false);

  const checks = $derived(checksOf(card));
  // What a report names: the link, or the upload's name and content hash.
  const document = $derived(
    card.source.kind === 'url'
      ? card.source.url
      : `${card.source.kind === 'upload' ? card.source.fileName : card.name} (${card.reading?.document ?? ''})`,
  );

  const warnings = $derived.by(() => {
    // eslint-disable-next-line svelte/prefer-svelte-reactivity -- rebuilt by each derivation
    const byRef = new Map<string, string[]>();
    for (const review of project?.reviews ?? [])
      byRef.set(
        review.ref,
        review.warnings.map((w) => w.message),
      );
    for (const read of project?.ocr_reads ?? [])
      byRef.set(read.ref, [...(byRef.get(read.ref) ?? []), read.message]);
    return byRef;
  });

  // In the document's order, down each column, so the list reads beside it.
  const parts = $derived(readingOrder(project?.parts ?? []));
  const flagged = $derived(parts.filter((p) => warnings.has(p.ref) && !checks[p.ref]));
  const shown = $derived(onlyFlagged ? flagged : parts);
  const checked = $derived(parts.filter((p) => checks[p.ref]).length);
  // Warnings about parts the list doesn't have ('R9 may be missing').
  const missing = $derived([...warnings].filter(([ref]) => !parts.some((p) => p.ref === ref)));

  const boxes = $derived<Box[]>(
    parts.flatMap((p) =>
      p.location
        ? [
            {
              ref: p.ref,
              location: p.location,
              tone: checks[p.ref] ? 'checked' : warnings.has(p.ref) ? 'flagged' : 'plain',
            },
          ]
        : [],
    ),
  );

  // A part chosen on the page scrolls into view in the list.
  let list = $state<HTMLUListElement>();
  function choose(ref: string, block: 'nearest' | 'center' = 'nearest'): void {
    selected = ref;
    const row = list?.querySelector(`[data-ref="${CSS.escape(ref)}"]`);
    const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    row?.scrollIntoView({ block, behavior: reduced ? 'auto' : 'smooth' });
  }

  // The review replaces the workspace, so it opens at its own top, or at the
  // part a warning asked to show.
  let section = $state<HTMLElement>();
  onMount(() => {
    section?.scrollIntoView({ block: 'start' });
    if (selected) choose(selected, 'center');
  });
</script>

<section class="review" aria-labelledby="review-title" bind:this={section}>
  <div class="head">
    <button class="back" type="button" onclick={onBack}>
      Back to the workspace <Icon name="arrow-left" size={14} />
    </button>
    <h2 id="review-title">Check {card.name}</h2>
    <p class="note">
      Each part is outlined where Star Ground read it. Compare it with the document, then mark it as
      right or correct it; a correction changes the master list.
    </p>
  </div>

  <div class="panes">
    <div class="document">
      <DocumentViewer {load} {boxes} {selected} onSelect={choose} />
    </div>

    <div class="parts">
      {#if !project}
        <p class="note" role="status">Updating the parts…</p>
      {:else}
        <div class="summary">
          <p class="note">
            {checked} of {count(parts.length, 'part')} checked{flagged.length
              ? `; ${flagged.length} flagged by Star Ground's self-checks`
              : ''}.
          </p>
          {#if flagged.length}
            <label class="filter">
              <input type="checkbox" bind:checked={onlyFlagged} /> Only flagged parts
            </label>
          {/if}
        </div>
        {#if missing.length}
          <ul class="missing">
            {#each missing as [ref, messages] (ref)}
              <li><code>{ref}</code> {messages.join(' ')}</li>
            {/each}
          </ul>
        {/if}
        <ul class="list" bind:this={list}>
          {#each shown as part (part.ref)}
            <ReviewPart
              {part}
              check={checks[part.ref] ?? null}
              warnings={warnings.get(part.ref) ?? []}
              selected={selected === part.ref}
              {document}
              onSelect={() => (selected = part.ref)}
              onCheck={(check) => onCheck(part.ref, check)}
            />
          {/each}
        </ul>
      {/if}
    </div>
  </div>
</section>

<style>
  .review {
    display: grid;
    gap: 20px;
    margin-top: 40px;
    scroll-margin-top: calc(var(--hs-bar-height, 0px) + 16px);
  }
  .head {
    display: grid;
    gap: 10px;
  }
  .back {
    display: inline-flex;
    gap: 8px;
    align-items: center;
    justify-self: start;
    padding: 0;
    border: 0;
    color: var(--muted);
    background: none;
    font: inherit;
    font-size: var(--fs-ui);
    cursor: pointer;
  }
  .back:hover {
    color: var(--accent);
  }
  h2 {
    margin: 0;
    font-size: var(--fs-h3);
    font-weight: 500;
    letter-spacing: -0.8px;
  }
  .note {
    margin: 0;
    color: var(--muted);
    font-size: var(--fs-ui);
    line-height: 1.7;
  }
  .panes {
    display: grid;
    grid-template-columns: minmax(0, 7fr) minmax(0, 5fr);
    gap: 24px;
    align-items: start;
  }
  .document {
    position: sticky;
    top: calc(var(--hs-bar-height, 0px) + 16px);
    height: calc(100vh - var(--hs-bar-height, 0px) - 32px);
  }
  .parts {
    display: grid;
    gap: 12px;
    min-width: 0;
  }
  .summary {
    display: flex;
    flex-wrap: wrap;
    gap: 6px 18px;
    align-items: baseline;
    justify-content: space-between;
  }
  .filter {
    display: flex;
    gap: 8px;
    align-items: center;
    color: var(--muted);
    font-size: var(--fs-ui);
  }
  .missing {
    margin: 0;
    padding-left: 20px;
    color: var(--muted);
    font-size: var(--fs-ui);
    line-height: 1.7;
  }
  .list {
    display: grid;
    gap: 8px;
    margin: 0;
    padding: 0;
    list-style: none;
  }
  button:focus-visible,
  input:focus-visible {
    outline: 2px solid var(--accent);
    outline-offset: 2px;
  }
  @media (width <= 900px) {
    .panes {
      grid-template-columns: minmax(0, 1fr);
    }
    /* Pinned above the list, so a part chosen in it shows on the page. */
    .document {
      z-index: 1;
      height: 42vh;
      background: var(--bg);
    }
    /* A row scrolled to stops below the pinned document. */
    .list :global(li) {
      scroll-margin-top: calc(var(--hs-bar-height, 0px) + 42vh + 32px);
    }
  }
</style>

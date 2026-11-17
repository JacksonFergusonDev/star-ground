<script lang="ts">
  // The projects page: what the projects add up to, a card for each, and the
  // way to add another. The app passes its workspace; the snapshot page
  // (pages/embed/projects.astro) passes fixed projects, drawn the same way.
  import type { ProjectResult } from '../../lib/api';
  import type { Notice } from '../../lib/projects';
  import { buildHref, checkHref, views } from '../../lib/router.svelte';
  import type { Failure, Stock } from '../../lib/server';
  import type { Added, Card } from '../../lib/workspace.svelte';
  import { count, flagged, reviewable } from '../../lib/workspace.svelte';
  import AddProject from './AddProject.svelte';
  import BugNotice from './BugNotice.svelte';
  import EmptyState from './EmptyState.svelte';
  import Icon from './Icon.svelte';
  import PageHead from './PageHead.svelte';
  import ProjectCard from './ProjectCard.svelte';

  let {
    cards,
    projectOf,
    toBuy,
    stock,
    notice = null,
    failure = null,
    onAdd,
    onCount,
    onRemove,
    onRead,
  }: {
    cards: Card[];
    /** A card's project on the shopping list; null until it's listed. */
    projectOf: (id: string) => ProjectResult | null;
    /** The shopping list's rows to buy; null before it's built. */
    toBuy: number | null;
    stock: Stock | null;
    /** What arriving by a catalog or share link did. */
    notice?: Notice | null;
    /** Why the shopping list couldn't be built. */
    failure?: Failure | null;
    onAdd: (added: Added) => Promise<void>;
    onCount: (card: Card, count: number) => void;
    onRemove: (card: Card) => void;
    onRead: (card: Card, ocr: boolean) => void;
  } = $props();

  const pedals = $derived(cards.reduce((n, c) => n + c.count, 0));
  const toCheck = $derived(cards.reduce((n, c) => n + flagged(c, projectOf(c.id)), 0));
</script>

{#snippet arrow()}<Icon name="arrow-right" size={14} />{/snippet}

<PageHead
  title="Projects"
  lede={cards.length
    ? `${count(cards.length, 'project')}, ${count(pedals, 'pedal')} to build. The shopping list merges their parts and updates as you go.`
    : "Add each pedal you're building. The shopping list merges their parts and updates as you go."}
>
  {#snippet actions()}
    <a class="hs-button" href="/catalog/">Browse the catalog {@render arrow()}</a>
  {/snippet}
</PageHead>

{#if notice?.problem}
  <p class="notice" role="alert">{notice.message}</p>
{:else if notice}
  <p class="notice" role="status">{notice.message}</p>
{/if}
{#if failure}
  <div class="failure">
    <p role="alert">{failure.message}</p>
    {#if failure.bugCode}
      <BugNotice code={failure.bugCode} document="Building the master list" />
    {/if}
  </div>
{/if}

{#if cards.length}
  <div class="tiles">
    <a class="tile hover-card" href={views.list.path}>
      <span class="tile-label">To buy</span>
      <span class="tile-value">{toBuy ?? '–'}</span>
      <span class="tile-link">Shopping list {@render arrow()}</span>
    </a>
    <a class="tile hover-card" class:attention={toCheck > 0} href={`${views.list.path}#check`}>
      <span class="tile-label">To check</span>
      <span class="tile-value">{toCheck}</span>
      <span class="tile-link"
        >{toCheck ? 'Parts to look at' : 'Nothing flagged'} {@render arrow()}</span
      >
    </a>
    <a class="tile hover-card" href={views.stock.path}>
      <span class="tile-label">Stock</span>
      <span class="tile-value small">{stock ? stock.name : 'None yet'}</span>
      <span class="tile-link"
        >{stock ? 'Subtracted from the list' : 'Upload what you own'}
        {@render arrow()}</span
      >
    </a>
  </div>
{/if}

<div class="projects">
  <section class="cards-column" aria-labelledby="cards-title">
    <h2 id="cards-title" class="column-title">Your projects</h2>
    {#if cards.length}
      <ul class="cards">
        {#each cards as card (card.id)}
          {@const listed = projectOf(card.id)}
          <ProjectCard
            {card}
            project={listed}
            flags={flagged(card, listed)}
            onCount={(count) => onCount(card, count)}
            onRemove={() => onRemove(card)}
            onRead={(ocr) => onRead(card, ocr)}
            checkLink={reviewable(card) && (listed?.parts.length || listed?.counted.length)
              ? checkHref(card.id)
              : null}
            buildLink={listed?.build_order.length ? buildHref(card.id) : null}
          />
        {/each}
      </ul>
    {:else}
      <EmptyState title="Nothing on the bench yet" level={3}>
        Search the catalog for your pedal: its parts lists are already read. Or add a build
        document, a link to one or a parts list.
      </EmptyState>
    {/if}
  </section>
  <div class="adder" class:first={!cards.length}>
    <AddProject {onAdd} />
  </div>
</div>

<style>
  .notice {
    margin: 0 0 20px;
    color: var(--muted);
    font-size: var(--fs-ui);
    line-height: 1.7;
  }
  .failure {
    display: grid;
    gap: 10px;
    margin-bottom: 24px;
    padding: 14px 18px;
    border: 1px solid var(--line);
    border-radius: 5px;
    background: var(--panel);
  }
  .failure p {
    margin: 0;
    font-size: var(--fs-ui);
  }
  .tiles {
    display: grid;
    grid-template-columns: repeat(3, minmax(0, 1fr));
    gap: 16px;
    margin-bottom: 36px;
  }
  .tile {
    display: grid;
    gap: 6px;
    padding: 18px 20px;
    border: 1px solid var(--line);
    border-radius: 5px;
    background: var(--panel);
  }
  .tile-label {
    color: var(--muted);
    font-family: var(--mono);
    font-size: var(--fs-label);
    letter-spacing: 0.08em;
    text-transform: uppercase;
  }
  .tile-value {
    overflow: hidden;
    color: var(--text);
    font-size: var(--fs-h3);
    font-weight: 500;
    font-variant-numeric: tabular-nums;
    letter-spacing: -0.8px;
    line-height: 1.2;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .tile-value.small {
    font-size: var(--fs-h4);
    letter-spacing: 0;
    line-height: calc(var(--fs-h3) * 1.2);
  }
  .attention .tile-value {
    color: var(--accent);
  }
  .tile-link {
    display: inline-flex;
    gap: 8px;
    align-items: center;
    color: var(--muted);
    font-size: var(--fs-ui);
  }
  .tile:hover .tile-link {
    color: var(--accent);
  }
  .projects {
    display: grid;
    grid-template-columns: minmax(0, 7fr) minmax(0, 5fr);
    gap: 32px;
    align-items: start;
  }
  .cards-column {
    display: grid;
    gap: 14px;
  }
  .column-title {
    margin: 0;
    color: var(--muted);
    font-family: var(--mono);
    font-size: var(--fs-label);
    font-weight: 400;
    letter-spacing: 0.08em;
    text-transform: uppercase;
  }
  .cards {
    display: grid;
    grid-template-columns: minmax(0, 1fr);
    gap: 12px;
    margin: 0;
    padding: 0;
    list-style: none;
  }
  .adder {
    position: sticky;
    top: calc(var(--hs-bar-height) + 24px);
  }
  @media (width <= 1100px) {
    .projects {
      grid-template-columns: minmax(0, 1fr);
    }
    .adder {
      position: static;
    }
    /* With nothing added yet, the way to add comes first. */
    .adder.first {
      order: -1;
    }
  }
  @media (width <= 700px) {
    .tiles {
      gap: 8px;
      margin-bottom: 28px;
    }
    .tile {
      padding: 12px 14px;
    }
    .tile-value,
    .tile-value.small {
      font-size: var(--fs-h4);
      letter-spacing: 0;
      line-height: 1.4;
    }
    .tile-link {
      display: none;
    }
  }
</style>

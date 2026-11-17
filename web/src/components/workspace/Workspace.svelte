<script lang="ts">
  // The app: the builder's projects, the shopping list they make, their stock
  // and exports, and each project's check and bench mode, each on its own page
  // (lib/router.svelte.ts). The shopping list updates as the projects change;
  // there's no Generate button. Everything is kept in the browser (see
  // lib/workspace.svelte.ts).
  import { onMount, tick } from 'svelte';
  import type { ExportKind } from '../../lib/api';
  import type { Notice } from '../../lib/projects';
  import {
    addFromCatalog,
    addFromShareLink,
    addProject,
    documentOf,
    exportList as exportFile,
    readProject,
    refreshListing,
  } from '../../lib/projects';
  import type { View } from '../../lib/router.svelte';
  import { buildHref, checkHref, followAppLinks, router, views } from '../../lib/router.svelte';
  import type { Download } from '../../lib/server';
  import type { Stock } from '../../lib/server';
  import { warmUp } from '../../lib/server';
  import { addHave, readStock, withRows } from '../../lib/stock';
  import type { Added } from '../../lib/workspace.svelte';
  import {
    blocker,
    listing,
    projectOf,
    ready,
    reviewable,
    workspace as pageWorkspace,
  } from '../../lib/workspace.svelte';
  import Bench from './Bench.svelte';
  import EmptyState from './EmptyState.svelte';
  import Exports from './Exports.svelte';
  import HaveList from './HaveList.svelte';
  import Icon from './Icon.svelte';
  import Identify from './Identify.svelte';
  import MasterList from './MasterList.svelte';
  import PageHead from './PageHead.svelte';
  import Projects from './Projects.svelte';
  import Review from './Review.svelte';
  import ShareLink from './ShareLink.svelte';
  import StockCount from './StockCount.svelte';
  import StockDrawers from './StockDrawers.svelte';
  import StockPanel from './StockPanel.svelte';

  const workspace = pageWorkspace();

  const view = $derived<View>(router.view ?? 'projects');
  // The project a check or bench page is about.
  const card = $derived.by(() => {
    const id = router.param('project');
    return id ? workspace.find(id) : undefined;
  });
  const project = $derived(card ? projectOf(card.id) : null);

  const toBuy = $derived(listing.result?.shopping_list.filter((r) => r.buy_qty > 0).length ?? null);

  const title = $derived.by(() => {
    if (view === 'check' && card) return `Check ${card.name}`;
    if (view === 'build' && card) return `Build ${project?.label ?? card.name}`;
    return views[view].title;
  });
  $effect(() => {
    document.title = `${title} | Star Ground`;
  });

  // Arriving at a page shows its top, or the part of it the address names,
  // and focuses its title so a screen reader announces it. The first page
  // loaded keeps the browser's own focus.
  $effect(() => {
    const url = router.url;
    if (!url || !router.moved) return;
    void tick().then(() => {
      const target = url.hash
        ? document.getElementById(decodeURIComponent(url.hash.slice(1)))
        : null;
      if (target) target.scrollIntoView({ block: 'start' });
      else {
        // The page's own column scrolls on a laptop, the window on a phone.
        document.querySelector('[data-app-scroll]')?.scrollTo(0, 0);
        window.scrollTo(0, 0);
      }
      document.querySelector<HTMLElement>('main h1')?.focus({ preventScroll: true });
    });
  });

  // What arriving by a catalog or share link did, or why it couldn't.
  let notice = $state<Notice | null>(null);
  const show = (arrived: Notice | null): void => {
    if (arrived) notice = arrived;
  };

  onMount(() => {
    warmUp();
    void addFromCatalog(workspace).then(show);
    void addFromShareLink(workspace).then(show);
    return followAppLinks();
  });

  const add = (added: Added): Promise<void> => addProject(workspace, added);
  const read = (id: string, ocr: boolean): Promise<void> => readProject(workspace, id, ocr);
  const setStock = (stock: Stock | null): void => {
    workspace.stock = stock;
  };
  // A part identified on the identifier page, added to what the stock holds.
  const addToStock = (category: string, value: string, qty: number): void => {
    const rows = workspace.stock ? readStock(workspace.stock.text) : [];
    setStock(withRows(workspace.stock, addHave(rows, category, value, qty)));
  };
  const exportList = (kind: ExportKind, excel: boolean): Promise<Download> =>
    exportFile(workspace, kind, excel);

  // Rebuild the shopping list whenever what it's built from changes (see
  // `refreshListing`); there's no Generate button.
  $effect(() => refreshListing(workspace));
</script>

{#snippet arrow()}<Icon name="arrow-right" size={14} />{/snippet}

{#if view === 'check' || view === 'build'}
  {#if !card}
    <PageHead
      title="This project isn't here"
      lede="It isn't in this browser's workspace. It may have been removed, or added in another browser."
    >
      {#snippet actions()}
        <a class="hs-button" href={views.projects.path}>Your projects {@render arrow()}</a>
      {/snippet}
    </PageHead>
  {:else}
    {@const canCheck = reviewable(card)}
    {@const canBuild = !!project?.build_order.length}
    <PageHead {title}>
      {#snippet crumbs()}
        <li><a href={views.projects.path}>Projects</a></li>
        <li>{card.name}</li>
      {/snippet}
      <nav class="app-tabs" aria-label="Project pages">
        {#if canCheck}
          <a href={checkHref(card.id)} aria-current={view === 'check' ? 'page' : undefined}>
            Check parts
          </a>
        {:else}
          <span aria-disabled="true">Check parts</span>
        {/if}
        {#if canBuild}
          <a href={buildHref(card.id)} aria-current={view === 'build' ? 'page' : undefined}>
            Build
          </a>
        {:else}
          <span aria-disabled="true">Build</span>
        {/if}
      </nav>
    </PageHead>

    {#if view === 'check'}
      {#if canCheck}
        {#key card.id}
          <Review
            {card}
            {project}
            focus={router.param('part')}
            load={() => documentOf(card)}
            onCheck={(ref, check) => workspace.check(card, ref, check)}
          />
        {/key}
      {:else}
        <p class="note">
          Only an uploaded document or a link can be checked against its document. This project's
          parts come from a pasted list.
        </p>
      {/if}
    {:else if project && canBuild}
      {#key card.id}
        <Bench
          {card}
          {project}
          display={workspace.display}
          load={reviewable(card) ? () => documentOf(card) : null}
          onPlace={(ref, placed) => workspace.place(card, ref, placed)}
          onStartOver={() => workspace.startOver(card)}
        />
      {/key}
    {:else}
      <p class="note" role="status">
        {blocker(card) ??
          (listing.updating || !listing.result
            ? 'Updating the parts…'
            : 'This project has no parts to build yet.')}
      </p>
    {/if}
  {/if}
{:else if view === 'list'}
  <PageHead
    title="Shopping list"
    lede="Every project's parts in one list, less the stock you own. It updates as your projects change."
  >
    {#snippet actions()}
      {#if listing.result}
        <a class="hs-button" href={views.have.path}>Tick off what you have {@render arrow()}</a>
      {/if}
      <a class="hs-button" href={views.export.path}>Export {@render arrow()}</a>
    {/snippet}
  </PageHead>
  <MasterList
    result={listing.result}
    updating={listing.updating}
    error={listing.error}
    options={workspace.options}
    display={workspace.display}
    showHref={(index, ref) => {
      const id = listing.listed[index];
      const listed = id ? workspace.find(id) : undefined;
      return listed && reviewable(listed) ? checkHref(listed.id, ref) : null;
    }}
  />
{:else if view === 'have'}
  <PageHead
    title="What you have"
    lede="Say which of this order's parts you already have, without counting your whole stock. The shopping list buys only the rest."
  >
    {#snippet crumbs()}
      <li><a href={views.list.path}>Shopping list</a></li>
      <li>What you have</li>
    {/snippet}
  </PageHead>
  <HaveList
    result={listing.result}
    updating={listing.updating}
    stock={workspace.stock}
    notation={workspace.display.notation}
    onChange={setStock}
  />
{:else if view === 'count'}
  <PageHead
    title="Count your parts"
    lede="Type how many you have of each part, and download the stock CSV when you're done. The shopping list subtracts your counts as you go."
  >
    {#snippet crumbs()}
      <li><a href={views.stock.path}>Stock</a></li>
      <li>Count your parts</li>
    {/snippet}
  </PageHead>
  <StockCount
    stock={workspace.stock}
    notation={workspace.display.notation}
    bind:bands={workspace.display.bands}
    onChange={setStock}
  />
{:else if view === 'identify'}
  <PageHead
    title="Identify a part"
    lede="Read a resistor's colour bands, the code printed on a capacitor or pot, and where other parts print what they are."
  />
  <Identify bind:bands={workspace.display.bands} onAdd={addToStock} />
{:else if view === 'stock'}
  <PageHead
    title="Stock"
    lede="The shopping list subtracts the parts you already own, so you buy only what's missing."
  >
    {#snippet actions()}
      <a class="hs-button" href={views.count.path}>Count your parts {@render arrow()}</a>
    {/snippet}
  </PageHead>
  <div class="stock">
    <StockPanel stock={workspace.stock} onChange={setStock} />
    <StockDrawers stock={workspace.stock} notation={workspace.display.notation} />
  </div>
{:else if view === 'export'}
  <PageHead
    title="Export"
    lede="Files for ordering and building, and a link that shares these projects."
  />
  <div class="export">
    {#if ready(workspace.cards).length}
      <Exports onExport={exportList} />
    {:else}
      <EmptyState title="Nothing to export yet">
        Add a project, and its shopping list, cart files and build checklists appear here.
        {#snippet action()}
          <a class="hs-button" href={views.projects.path}>Add a project {@render arrow()}</a>
        {/snippet}
      </EmptyState>
    {/if}
    {#if workspace.cards.length}
      <ShareLink cards={workspace.cards} />
    {/if}
  </div>
{:else}
  <Projects
    cards={workspace.cards}
    {projectOf}
    {toBuy}
    stock={workspace.stock}
    {notice}
    failure={listing.error}
    onAdd={add}
    onCount={(card, count) => workspace.setCount(card, count)}
    onRemove={(card) => workspace.remove(card.id)}
    onRead={(card, ocr) => read(card.id, ocr)}
  />
{/if}

<style>
  .note {
    margin: 0 0 20px;
    color: var(--muted);
    font-size: var(--fs-ui);
    line-height: 1.7;
  }
  .stock {
    display: grid;
    grid-template-columns: minmax(0, 1fr);
    gap: 28px;
  }
  /* The upload field and the drawers shrink to a phone's column. */
  .stock > :global(*) {
    min-width: 0;
  }
  .export {
    display: grid;
    gap: 40px;
    max-width: 760px;
  }
</style>

<script lang="ts">
  // The app: the builder's projects, the shopping list they make, their stock
  // and exports, and each project's check and bench mode, each on its own page
  // (lib/router.svelte.ts). The shopping list updates as the projects change;
  // there's no Generate button. Everything is kept in the browser (see
  // lib/workspace.svelte.ts).
  import { onMount, tick } from 'svelte';
  import type { ExportKind } from '../../lib/api';
  import type { Download } from '../../lib/server';
  import { bugOf, exportFile, linkedPdf, masterList, readDocument, warmUp } from '../../lib/server';
  import type { View } from '../../lib/router.svelte';
  import { buildHref, checkHref, followAppLinks, router, views } from '../../lib/router.svelte';
  import type { SharedProject } from '../../lib/share';
  import { sharedProjects } from '../../lib/share';
  import type { Added, Card } from '../../lib/workspace.svelte';
  import {
    blocker,
    builds,
    count,
    files,
    flagged,
    keepListing,
    listing,
    listingCurrent,
    listingKey,
    projectOf,
    ready,
    request,
    reviewable,
    workspace as pageWorkspace,
  } from '../../lib/workspace.svelte';
  import AddProject from './AddProject.svelte';
  import Bench from './Bench.svelte';
  import BugNotice from './BugNotice.svelte';
  import EmptyState from './EmptyState.svelte';
  import Exports from './Exports.svelte';
  import Icon from './Icon.svelte';
  import MasterList from './MasterList.svelte';
  import PageHead from './PageHead.svelte';
  import ProjectCard from './ProjectCard.svelte';
  import Review from './Review.svelte';
  import ShareLink from './ShareLink.svelte';
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

  const pedals = $derived(workspace.cards.reduce((n, c) => n + c.count, 0));
  const toBuy = $derived(listing.result?.shopping_list.filter((r) => r.buy_qty > 0).length ?? null);
  const toCheck = $derived(workspace.cards.reduce((n, c) => n + flagged(c, projectOf(c.id)), 0));

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

  // The document's bytes: an upload from this browser, a link through the API.
  async function load(card: Card): Promise<ArrayBuffer> {
    if (card.source.kind === 'url') return linkedPdf(card.source.url);
    const file = await files.get(card.id);
    if (!file) throw new Error('The uploaded file is no longer stored here. Add it again.');
    return file.arrayBuffer();
  }

  // A catalog build's "Add to build" opens `/app/?catalog=<publisher>/<name>`.
  // It's added once: the address loses the parameter, so a reload doesn't add it again.
  // What arriving by a catalog or share link did, or why it couldn't.
  let notice = $state<{ message: string; problem: boolean } | null>(null);
  async function addFromCatalog(): Promise<void> {
    const address = new URL(window.location.href);
    const id = address.searchParams.get('catalog');
    if (id === null) return;
    address.searchParams.delete('catalog');
    router.replace(address.pathname + address.search + address.hash);
    try {
      if (!/^[a-z]+\/[a-z0-9-]+$/.test(id)) throw new Error(id);
      const response = await fetch(`/catalog/${id}.json`);
      if (!response.ok) throw new Error(id);
      const build = (await response.json()) as { title: string; document: string };
      await add({ kind: 'url', url: build.document, name: build.title, catalog: id });
    } catch {
      notice = {
        message: "That catalog build couldn't be found. It may have been taken out of the catalog.",
        problem: true,
      };
    }
  }

  // A share link (`/app/#share=…`) adds its projects beside the ones already here.
  async function addFromShareLink(): Promise<void> {
    let projects: SharedProject[] | null;
    try {
      projects = await sharedProjects(window.location.hash);
    } catch (e) {
      notice = { message: (e as Error).message, problem: true };
      projects = [];
    }
    if (projects === null) return;
    router.replace(window.location.pathname + window.location.search);
    for (const project of projects) addShared(project);
    if (projects.length)
      notice = {
        message: `Added ${count(projects.length, 'project')} from a shared link.`,
        problem: false,
      };
  }

  function addShared(project: SharedProject): void {
    const reading = project.source.kind === 'url';
    const card = workspace.add(
      { name: project.name, count: 1, source: project.source },
      reading ? { state: 'reading' } : undefined,
    );
    workspace.setCount(card, project.count);
    card.checks[project.variant ?? ''] = Object.fromEntries(
      project.corrections.map(({ ref, ...check }) => [ref, check]),
    );
    if (!reading) return;
    // The build is chosen once the document is read and its builds are known.
    void read(card.id, project.useOcr).then(() => {
      const current = workspace.find(card.id);
      if (current && project.variant && builds(current).includes(project.variant))
        current.variant = project.variant;
    });
  }

  onMount(() => {
    warmUp();
    void addFromCatalog();
    void addFromShareLink();
    return followAppLinks();
  });

  const stem = (name: string): string => name.replace(/\.[^.]+$/, '');

  async function read(id: string, ocr: boolean): Promise<void> {
    const card = workspace.find(id);
    if (!card || (card.source.kind !== 'upload' && card.source.kind !== 'url')) return;
    card.read = { state: 'reading' };
    try {
      let source: { file: File } | { url: string };
      if (card.source.kind === 'upload') {
        const file = await files.get(id);
        if (!file) throw new Error('The uploaded file is no longer stored here. Add it again.');
        source = { file };
      } else {
        source = { url: card.source.url };
      }
      const reading = await readDocument(source, ocr, (event) => {
        const current = workspace.find(id);
        if (!current) return;
        current.read =
          event.event === 'queued'
            ? { state: 'queued', position: event.position }
            : { state: 'reading' };
      });
      const current = workspace.find(id);
      if (!current) return;
      // A name still as it was added takes the title the document prints.
      const added = current.source.kind === 'upload' ? stem(current.source.fileName) : 'Link';
      if (reading.title && current.name === added) current.name = reading.title;
      current.reading = reading;
      if (ocr && reading.ocr?.has_parts) current.useOcr = true;
      current.read = { state: 'ready' };
    } catch (e) {
      const current = workspace.find(id);
      if (current)
        current.read = { state: 'failed', message: (e as Error).message, bugCode: bugOf(e) };
    }
  }

  async function add(added: Added): Promise<void> {
    switch (added.kind) {
      case 'paste':
        workspace.add({ name: added.name, count: 1, source: { kind: 'paste', text: added.text } });
        break;
      case 'url': {
        const card = workspace.add(
          {
            name: added.name ?? 'Link',
            count: 1,
            source: { kind: 'url', url: added.url, catalog: added.catalog },
          },
          { state: 'reading' },
        );
        void read(card.id, false);
        break;
      }
      case 'upload': {
        const card = workspace.add(
          {
            name: stem(added.file.name),
            count: 1,
            source: { kind: 'upload', fileName: added.file.name },
          },
          { state: 'reading' },
        );
        await files.put(card.id, added.file);
        void read(card.id, false);
        break;
      }
    }
  }

  // The file a reading's `document` names, for a server that no longer has it.
  async function fileFor(document: string): Promise<File | undefined> {
    const card = workspace.cards.find((c: Card) => c.reading?.document === document);
    return card ? files.get(card.id) : undefined;
  }

  // Exports the list as the cards stand now, with the same requests the list makes.
  function exportList(kind: ExportKind, excel: boolean): Promise<Download> {
    const next = request(workspace.cards, workspace.options);
    if (!next) return Promise.reject(new Error('Add a project first.'));
    return exportFile(kind, excel, next, fileFor, workspace.stock);
  }

  // Rebuild the shopping list whenever what it's built from changes, after a
  // short pause so typing a name or a count sends one request, not one per
  // keystroke. A newer request supersedes an older one still in flight. A
  // change the list doesn't depend on (a paint, a part placed) sends nothing,
  // and a reload reuses the list kept from the last visit (lib/workspace).
  let sequence = 0;
  $effect(() => {
    // Saving reads every card deeply, so any change re-runs this.
    workspace.save();
    const next = request(workspace.cards, workspace.options);
    const ids = ready(workspace.cards).map((c) => c.id);
    const stock = workspace.stock;
    const mine = ++sequence;
    if (!next) {
      Object.assign(listing, { result: null, error: null, updating: false, key: null });
      return;
    }
    const key = listingKey(next, stock);
    if (listingCurrent(key)) {
      listing.updating = false;
      return;
    }
    listing.updating = true;
    const timer = setTimeout(() => {
      masterList(next, fileFor, stock)
        .then((r) => {
          if (mine === sequence) keepListing(key, r, ids);
        })
        .catch((e: Error) => {
          if (mine === sequence) listing.error = { message: e.message, bugCode: bugOf(e) };
        })
        .finally(() => {
          if (mine === sequence) listing.updating = false;
        });
    }, 400);
    return () => clearTimeout(timer);
  });
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
            load={() => load(card)}
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
          load={reviewable(card) ? () => load(card) : null}
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
{:else if view === 'stock'}
  <PageHead
    title="Stock"
    lede="The shopping list subtracts the parts you already own, so you buy only what's missing."
  />
  <div class="stock">
    <StockPanel stock={workspace.stock} onChange={(stock) => (workspace.stock = stock)} />
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
  <PageHead
    title="Projects"
    lede={workspace.cards.length
      ? `${count(workspace.cards.length, 'project')}, ${count(pedals, 'pedal')} to build. The shopping list merges their parts and updates as you go.`
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
  {#if listing.error}
    <div class="failure">
      <p role="alert">{listing.error.message}</p>
      {#if listing.error.bugCode}
        <BugNotice code={listing.error.bugCode} document="Building the master list" />
      {/if}
    </div>
  {/if}

  {#if workspace.cards.length}
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
        <span class="tile-value small">{workspace.stock ? workspace.stock.name : 'None yet'}</span>
        <span class="tile-link"
          >{workspace.stock ? 'Subtracted from the list' : 'Upload what you own'}
          {@render arrow()}</span
        >
      </a>
    </div>
  {/if}

  <div class="projects">
    <section class="cards-column" aria-labelledby="cards-title">
      <h2 id="cards-title" class="column-title">Your projects</h2>
      {#if workspace.cards.length}
        <ul class="cards">
          {#each workspace.cards as card (card.id)}
            {@const listed = projectOf(card.id)}
            <ProjectCard
              {card}
              project={listed}
              flags={flagged(card, listed)}
              onCount={(count) => workspace.setCount(card, count)}
              onRemove={() => workspace.remove(card.id)}
              onRead={(ocr) => read(card.id, ocr)}
              checkLink={reviewable(card) && (listed?.parts.length || listed?.counted.length)
                ? checkHref(card.id)
                : null}
              buildLink={listed?.build_order.length ? buildHref(card.id) : null}
            />
          {/each}
        </ul>
      {:else}
        <EmptyState title="Nothing on the bench yet" level={3}>
          Add a build document, a link to one or a parts list. Or find your pedal in the circuit
          catalog, whose parts lists are already read.
          {#snippet action()}
            <a class="hs-button" href="/catalog/">Browse the catalog {@render arrow()}</a>
          {/snippet}
        </EmptyState>
      {/if}
    </section>
    <div class="adder" class:first={!workspace.cards.length}>
      <AddProject onAdd={add} />
    </div>
  </div>
{/if}

<style>
  .note,
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

<script lang="ts">
  // The build workspace: project cards on one side, the master list they make
  // on the other. The list updates as the cards change; there's no Generate
  // button. Everything is kept in the browser (see lib/workspace.svelte.ts).
  import { onMount } from 'svelte';
  import type { ExportKind, MasterListResult, PresetCatalog, ProjectResult } from '../../lib/api';
  import type { Download, Failure } from '../../lib/server';
  import {
    bugOf,
    exportFile,
    linkedPdf,
    masterList,
    presets,
    readDocument,
    warmUp,
  } from '../../lib/server';
  import type { SharedProject } from '../../lib/share';
  import { sharedProjects } from '../../lib/share';
  import type { Added, Card } from '../../lib/workspace.svelte';
  import {
    builds,
    count,
    files,
    ready,
    request,
    reviewable,
    Workspace,
  } from '../../lib/workspace.svelte';
  import AddProject from './AddProject.svelte';
  import MasterList from './MasterList.svelte';
  import ProjectCard from './ProjectCard.svelte';
  import Review from './Review.svelte';
  import ShareLink from './ShareLink.svelte';
  import StockPanel from './StockPanel.svelte';

  const workspace = new Workspace();
  let catalog = $state<PresetCatalog | null>(null);
  let result = $state<MasterListResult | null>(null);
  let updating = $state(false);
  let error = $state<Failure | null>(null);
  // The cards the master list's projects came from, in its order.
  let listed = $state<string[]>([]);
  // The card being checked against its document, and the part to show first.
  let reviewing = $state<{ id: string; ref: string | null } | null>(null);

  const reviewed = $derived(reviewing ? (workspace.find(reviewing.id) ?? null) : null);
  // A card's project on the master list, as last built: a correction shows on
  // the card's checks at once, and in the project once the list catches up.
  const projectOf = (id: string): ProjectResult | null =>
    result?.projects[listed.indexOf(id)] ?? null;

  function review(id: string, ref: string | null = null): void {
    reviewing = { id, ref };
  }

  // The document's bytes: an upload from this browser, a link through the API.
  async function load(card: Card): Promise<ArrayBuffer> {
    if (card.source.kind === 'url') return linkedPdf(card.source.url);
    const file = await files.get(card.id);
    if (!file) throw new Error('The uploaded file is no longer stored here. Add it again.');
    return file.arrayBuffer();
  }

  const presetName = (key: string): string =>
    catalog?.presets.find((p) => p.key === key)?.name ?? key;

  // A catalog build's "Add to build" opens `/app/?catalog=<publisher>/<name>`.
  // It's added once: the address loses the parameter, so a reload doesn't add it again.
  // What arriving by a catalog or share link did, or why it couldn't.
  let notice = $state<{ message: string; problem: boolean } | null>(null);
  async function addFromCatalog(): Promise<void> {
    const address = new URL(window.location.href);
    const id = address.searchParams.get('catalog');
    if (id === null) return;
    address.searchParams.delete('catalog');
    history.replaceState(history.state, '', address);
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
    history.replaceState(history.state, '', window.location.pathname + window.location.search);
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
    presets()
      .then((c) => (catalog = c))
      .catch(() => (catalog = { sources: [], categories: {}, presets: [] }));
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
      case 'preset':
        workspace.add({
          name: presetName(added.key),
          count: 1,
          source: { kind: 'preset', key: added.key },
        });
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

  // Rebuild the master list whenever the cards change, after a short pause so
  // typing a name or a count sends one request, not one per keystroke. A
  // newer request supersedes an older one still in flight.
  let sequence = 0;
  $effect(() => {
    // Saving reads every card deeply, so any change re-runs this.
    workspace.save();
    const next = request(workspace.cards, workspace.options);
    const ids = ready(workspace.cards).map((c) => c.id);
    const stock = workspace.stock;
    const mine = ++sequence;
    if (!next) {
      result = null;
      error = null;
      updating = false;
      return;
    }
    updating = true;
    const timer = setTimeout(() => {
      masterList(next, fileFor, stock)
        .then((r) => {
          if (mine !== sequence) return;
          result = r;
          listed = ids;
          error = null;
        })
        .catch((e: Error) => {
          if (mine === sequence) error = { message: e.message, bugCode: bugOf(e) };
        })
        .finally(() => {
          if (mine === sequence) updating = false;
        });
    }, 400);
    return () => clearTimeout(timer);
  });
</script>

{#if reviewing && reviewed}
  {#key reviewed.id}
    <Review
      card={reviewed}
      project={projectOf(reviewed.id)}
      focus={reviewing.ref}
      load={() => load(reviewed)}
      onCheck={(ref, check) => workspace.check(reviewed, ref, check)}
      onBack={() => (reviewing = null)}
    />
  {/key}
{:else}
  <div class="workspace">
    <section class="projects" aria-labelledby="projects-title">
      <h2 id="projects-title">Projects</h2>
      {#if notice?.problem}
        <p class="notice" role="alert">{notice.message}</p>
      {:else if notice}
        <p class="notice" role="status">{notice.message}</p>
      {/if}
      {#if workspace.cards.length}
        <ul class="cards">
          {#each workspace.cards as card (card.id)}
            <ProjectCard
              {card}
              {presetName}
              onCount={(count) => workspace.setCount(card, count)}
              onRemove={() => workspace.remove(card.id)}
              onRead={(ocr) => read(card.id, ocr)}
              onReview={reviewable(card) && projectOf(card.id)?.parts.length
                ? () => review(card.id)
                : null}
            />
          {/each}
        </ul>
      {/if}
      <AddProject {catalog} onAdd={add} />
      {#if workspace.cards.length}
        <ShareLink cards={workspace.cards} />
      {/if}
      <StockPanel stock={workspace.stock} onChange={(stock) => (workspace.stock = stock)} />
    </section>

    <MasterList
      {result}
      {updating}
      {error}
      options={workspace.options}
      onExport={exportList}
      onShow={(index, ref) => {
        const id = listed[index];
        const card = id ? workspace.find(id) : undefined;
        return card && reviewable(card) ? () => review(card.id, ref) : null;
      }}
    />
  </div>
{/if}

<style>
  .workspace {
    display: grid;
    grid-template-columns: minmax(0, 5fr) minmax(0, 7fr);
    gap: 40px;
    align-items: start;
    margin-top: 40px;
  }
  .projects {
    display: grid;
    grid-template-columns: minmax(0, 1fr);
    gap: 14px;
  }
  h2 {
    margin: 0;
    font-size: var(--fs-h3);
    font-weight: 500;
    letter-spacing: -0.8px;
  }
  .notice {
    margin: 0;
    color: var(--muted);
  }
  .cards {
    display: grid;
    grid-template-columns: minmax(0, 1fr);
    gap: 12px;
    margin: 0;
    padding: 0;
    list-style: none;
  }
  @media (width <= 900px) {
    .workspace {
      grid-template-columns: minmax(0, 1fr);
    }
  }
</style>

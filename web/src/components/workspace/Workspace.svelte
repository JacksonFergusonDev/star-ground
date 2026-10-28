<script lang="ts">
  // The build workspace: project cards on one side, the master list they make
  // on the other. The list updates as the cards change; there's no Generate
  // button. Everything is kept in the browser (see lib/workspace.svelte.ts).
  import { onMount } from 'svelte';
  import type { MasterListResult, PresetCatalog } from '../../lib/api';
  import { masterList, presets, readDocument, warmUp } from '../../lib/server';
  import type { Added, Card } from '../../lib/workspace.svelte';
  import { files, request, Workspace } from '../../lib/workspace.svelte';
  import AddProject from './AddProject.svelte';
  import MasterList from './MasterList.svelte';
  import ProjectCard from './ProjectCard.svelte';

  const workspace = new Workspace();
  let catalog = $state<PresetCatalog | null>(null);
  let result = $state<MasterListResult | null>(null);
  let updating = $state(false);
  let error = $state<string | null>(null);

  const presetName = (key: string): string =>
    catalog?.presets.find((p) => p.key === key)?.name ?? key;

  onMount(() => {
    warmUp();
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
      if (current) current.read = { state: 'failed', message: (e as Error).message };
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
          { name: 'Link', count: 1, source: { kind: 'url', url: added.url } },
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

  // Rebuild the master list whenever the cards change, after a short pause so
  // typing a name or a count sends one request, not one per keystroke. A
  // newer request supersedes an older one still in flight.
  let sequence = 0;
  $effect(() => {
    // Saving reads every card deeply, so any change re-runs this.
    workspace.save();
    const next = request(workspace.cards);
    const mine = ++sequence;
    if (!next) {
      result = null;
      error = null;
      updating = false;
      return;
    }
    updating = true;
    const timer = setTimeout(() => {
      masterList(next, fileFor)
        .then((r) => {
          if (mine !== sequence) return;
          result = r;
          error = null;
        })
        .catch((e: Error) => {
          if (mine === sequence) error = e.message;
        })
        .finally(() => {
          if (mine === sequence) updating = false;
        });
    }, 400);
    return () => clearTimeout(timer);
  });
</script>

<div class="workspace">
  <section class="projects" aria-labelledby="projects-title">
    <h2 id="projects-title">Projects</h2>
    {#if workspace.cards.length}
      <ul class="cards">
        {#each workspace.cards as card (card.id)}
          <ProjectCard
            {card}
            {presetName}
            onCount={(count) => workspace.setCount(card, count)}
            onRemove={() => workspace.remove(card.id)}
            onRead={(ocr) => read(card.id, ocr)}
          />
        {/each}
      </ul>
    {/if}
    <AddProject {catalog} onAdd={add} />
  </section>

  <MasterList {result} {updating} {error} />
</div>

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

// What the app does with the builder's projects: adding them (pasted,
// uploaded, linked, from the catalog or a share link), reading their
// documents through the API, and keeping the shopping list built from them.
// The workspace (lib/workspace.svelte.ts) holds the state; the pages only
// show it and call these.

import type { ExportKind } from './api';
import { router } from './router.svelte';
import type { Download } from './server';
import { bugOf, exportFile, linkedPdf, masterList, readDocument } from './server';
import type { SharedProject } from './share';
import { sharedProjects } from './share';
import type { Added, Card, Workspace } from './workspace.svelte';
import {
  builds,
  count,
  files,
  keepListing,
  listing,
  listingCurrent,
  listingKey,
  ready,
  request,
} from './workspace.svelte';

/** What arriving by a catalog or share link did, or why it couldn't. */
export interface Notice {
  message: string;
  problem: boolean;
}

const stem = (name: string): string => name.replace(/\.[^.]+$/, '');

/** Reads a card's upload or link through the API, streaming its progress onto the card. */
export async function readProject(workspace: Workspace, id: string, ocr: boolean): Promise<void> {
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

/** Adds a project as the builder gave it, and starts reading an upload or link. */
export async function addProject(workspace: Workspace, added: Added): Promise<void> {
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
      void readProject(workspace, card.id, false);
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
      void readProject(workspace, card.id, false);
      break;
    }
  }
}

function addShared(workspace: Workspace, project: SharedProject): void {
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
  void readProject(workspace, card.id, project.useOcr).then(() => {
    const current = workspace.find(card.id);
    if (current && project.variant && builds(current).includes(project.variant))
      current.variant = project.variant;
  });
}

/**
 * Adds the catalog build the address names, if any.
 *
 * A catalog build's "Add to build" opens `/app/?catalog=<publisher>/<name>`.
 * It's added once: the address loses the parameter, so a reload doesn't add it again.
 */
export async function addFromCatalog(workspace: Workspace): Promise<Notice | null> {
  const address = new URL(window.location.href);
  const id = address.searchParams.get('catalog');
  if (id === null) return null;
  address.searchParams.delete('catalog');
  router.replace(address.pathname + address.search + address.hash);
  try {
    if (!/^[a-z]+\/[a-z0-9-]+$/.test(id)) throw new Error(id);
    const response = await fetch(`/catalog/${id}.json`);
    if (!response.ok) throw new Error(id);
    const build = (await response.json()) as { title: string; document: string };
    await addProject(workspace, {
      kind: 'url',
      url: build.document,
      name: build.title,
      catalog: id,
    });
    return null;
  } catch {
    return {
      message: "That catalog build couldn't be found. It may have been taken out of the catalog.",
      problem: true,
    };
  }
}

/** Adds the projects a share link (`/app/#share=…`) carries, beside the ones already here. */
export async function addFromShareLink(workspace: Workspace): Promise<Notice | null> {
  let projects: SharedProject[] | null;
  let notice: Notice | null = null;
  try {
    projects = await sharedProjects(window.location.hash);
  } catch (e) {
    notice = { message: (e as Error).message, problem: true };
    projects = [];
  }
  if (projects === null) return null;
  router.replace(window.location.pathname + window.location.search);
  for (const project of projects) addShared(workspace, project);
  if (projects.length)
    notice = {
      message: `Added ${count(projects.length, 'project')} from a shared link.`,
      problem: false,
    };
  return notice;
}

/** The document's bytes: an upload from this browser, a link through the API. */
export async function documentOf(card: Card): Promise<ArrayBuffer> {
  if (card.source.kind === 'url') return linkedPdf(card.source.url);
  const file = await files.get(card.id);
  if (!file) throw new Error('The uploaded file is no longer stored here. Add it again.');
  return file.arrayBuffer();
}

/** The file a reading's `document` names, for a server that no longer has it. */
function fileFor(workspace: Workspace): (document: string) => Promise<File | undefined> {
  return async (document) => {
    const card = workspace.cards.find((c: Card) => c.reading?.document === document);
    return card ? files.get(card.id) : undefined;
  };
}

/** Exports the list as the cards stand now, with the same requests the list makes. */
export function exportList(
  workspace: Workspace,
  kind: ExportKind,
  excel: boolean,
): Promise<Download> {
  const next = request(workspace.cards, workspace.options);
  if (!next) return Promise.reject(new Error('Add a project first.'));
  return exportFile(kind, excel, next, fileFor(workspace), workspace.stock);
}

// Each rebuild's number; a newer one supersedes an older one still in flight.
let sequence = 0;
const PAUSE_MS = 400;

/**
 * Rebuilds the shopping list from the workspace as it stands, unless the list
 * showing was built from the same request. Call it from an effect: it reads
 * (and saves) the whole workspace, so any change re-runs it.
 *
 * It waits a short pause, so typing a name or a count sends one request, not
 * one per keystroke, and returns the cleanup that cancels a pending rebuild.
 * A change the list doesn't depend on (a paint, a part placed) sends nothing,
 * and a reload reuses the list kept from the last visit.
 */
export function refreshListing(workspace: Workspace): (() => void) | undefined {
  // Saving reads every card deeply, so an effect calling this tracks them all.
  workspace.save();
  const next = request(workspace.cards, workspace.options);
  const ids = ready(workspace.cards).map((c) => c.id);
  const stock = workspace.stock;
  const mine = ++sequence;
  if (!next) {
    Object.assign(listing, { result: null, error: null, updating: false, key: null });
    return undefined;
  }
  const key = listingKey(next, stock);
  if (listingCurrent(key)) {
    listing.updating = false;
    return undefined;
  }
  listing.updating = true;
  const timer = setTimeout(() => {
    masterList(next, fileFor(workspace), stock)
      .then((r) => {
        if (mine === sequence) keepListing(key, r, ids);
      })
      .catch((e: Error) => {
        if (mine === sequence) listing.error = { message: e.message, bugCode: bugOf(e) };
      })
      .finally(() => {
        if (mine === sequence) listing.updating = false;
      });
  }, PAUSE_MS);
  return () => clearTimeout(timer);
}

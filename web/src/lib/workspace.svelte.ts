// The build workspace's state: the builder's projects, kept in the browser.
// Cards and their readings live in localStorage; uploaded files, which can be
// tens of megabytes, live in IndexedDB. Either can be unavailable (a private
// window, blocked storage), and the workspace then works for the visit only.

import type {
  ComponentCategory,
  DocumentReading,
  MasterListRequest,
  MasterListResult,
  ProjectResult,
} from './api';
import type { Finish, Paint } from './enclosure';
import type { BandCount, Notation } from './parts';
import type { Failure, Stock } from './server';

/** Where a project's parts list comes from. */
type Source =
  | { kind: 'paste'; text: string }
  | { kind: 'preset'; key: string }
  | { kind: 'upload'; fileName: string }
  /** A link; `catalog` names the catalog build it was added from ('<publisher>/<name>'). */
  | { kind: 'url'; url: string; catalog?: string };

/** A project as the builder adds it, before it becomes a card. */
export type Added =
  | { kind: 'paste'; name: string; text: string }
  | { kind: 'preset'; key: string }
  | { kind: 'upload'; file: File }
  | { kind: 'url'; url: string; name?: string; catalog?: string };

/** Reading an upload or link: under way, waiting for OCR, done, or failed. */
export type ReadState =
  | { state: 'reading' }
  | { state: 'queued'; position: number }
  | { state: 'ready' }
  | { state: 'failed'; message: string; bugCode?: string | null };

/** A part the builder checked against its document. */
export type Check =
  | { state: 'confirmed'; value: string }
  | { state: 'corrected'; read: string; category: ComponentCategory; value: string };

/** One project card. */
export interface Card {
  id: string;
  name: string;
  count: number;
  source: Source;
  /** The chosen build, for a document describing several. */
  variant: string | null;
  /** Whether the builder accepted OCR's parts. */
  useOcr: boolean;
  /** The upload or link as the API read it; none for text and presets. */
  reading: DocumentReading | null;
  read: ReadState;
  /** The parts checked, by build ('' for a document with one), then designator. */
  checks: Record<string, Record<string, Check>>;
  /** The parts placed in bench mode, by build ('' for a document with one). */
  placed: Record<string, string[]>;
  /** The enclosure's paint; none until the builder picks one (see `paintOf`). */
  paint?: Paint | null;
  /** The paint's finish; matte until the builder picks another. */
  finish?: Finish | null;
}

const STORAGE_KEY = 'star-ground.workspace.v1';
const MAX_COUNT = 100;

/** The builds to choose from, given whether OCR's parts are accepted. */
export function builds(card: Card): string[] {
  if (!card.reading) return [];
  return card.useOcr ? card.reading.ocr_builds : card.reading.builds;
}

/** Whether a card's parts can be checked against its document: an upload or link, read. */
export function reviewable(card: Card): boolean {
  return (
    (card.source.kind === 'upload' || card.source.kind === 'url') &&
    card.read.state === 'ready' &&
    card.reading !== null
  );
}

/** The checks for the card's chosen build. */
export function checksOf(card: Card): Record<string, Check> {
  return card.checks[card.variant ?? ''] ?? {};
}

/** The parts placed in bench mode for the card's chosen build. */
export function placedOf(card: Card): string[] {
  return card.placed[card.variant ?? ''] ?? [];
}

/** Why a card can't go on the master list yet, or null when it can. */
export function blocker(card: Card): string | null {
  if (card.read.state === 'reading' || card.read.state === 'queued') return 'Reading…';
  if (card.read.state === 'failed') return card.read.message;
  if (builds(card).length && !card.variant) return 'Choose a build to add it.';
  return null;
}

/** What the master list lists besides the projects' own parts. */
export interface Options {
  /** The hardware kit: enclosure, jacks, footswitch, and so on. */
  hardware: boolean;
  /** Sockets and adapters. */
  extras: boolean;
}

/** How the builder likes parts shown: values in decimal or '4k7' form, and resistors' bands. */
export interface Display {
  notation: Notation;
  bands: BandCount;
}

/** The cards that can go on the master list, in order: its projects. */
export function ready(cards: Card[]): Card[] {
  return cards.filter((c) => blocker(c) === null);
}

/** The master list request for the cards that are ready. */
export function request(cards: Card[], options: Options): MasterListRequest | null {
  const listed = ready(cards);
  if (!listed.length) return null;
  return {
    show_hardware: options.hardware,
    show_extras: options.extras,
    projects: listed.map((c) => {
      const corrections = Object.entries(checksOf(c)).flatMap(([ref, check]) =>
        check.state === 'corrected' ? [{ ref, category: check.category, value: check.value }] : [],
      );
      const base = {
        name: c.name,
        count: c.count,
        variant: c.variant,
        use_ocr: c.useOcr,
        corrections,
      };
      switch (c.source.kind) {
        case 'paste':
          return { ...base, method: 'paste', text: c.source.text };
        case 'preset':
          return { ...base, method: 'preset', preset: c.source.key };
        case 'url':
          return { ...base, method: 'url', url: c.source.url };
        case 'upload':
          return { ...base, method: 'upload', document: c.reading?.document ?? null };
      }
    }),
  };
}

export class Workspace {
  cards = $state<Card[]>([]);
  stock = $state<Stock | null>(null);
  options = $state<Options>({ hardware: true, extras: true });
  display = $state<Display>({ notation: 'decimal', bands: 5 });

  constructor() {
    const saved = load();
    this.cards = saved.cards ?? [];
    this.stock = saved.stock ?? null;
    this.options = { ...this.options, ...saved.options };
    this.display = { ...this.display, ...saved.display };
    // A reading interrupted by a reload can't resume; offer to read again.
    for (const card of this.cards) {
      card.checks ??= {};
      card.placed ??= {};
      if (card.read.state === 'reading' || card.read.state === 'queued')
        card.read = { state: 'failed', message: 'Reading was interrupted. Read it again.' };
    }
  }

  add(
    card: Omit<Card, 'id' | 'variant' | 'useOcr' | 'reading' | 'read' | 'checks' | 'placed'>,
    read?: ReadState,
  ): Card {
    this.cards.push({
      ...card,
      id: crypto.randomUUID(),
      variant: null,
      useOcr: false,
      reading: null,
      read: read ?? { state: 'ready' },
      checks: {},
      placed: {},
    });
    return this.cards[this.cards.length - 1] as Card;
  }

  find(id: string): Card | undefined {
    return this.cards.find((c) => c.id === id);
  }

  remove(id: string): void {
    this.cards = this.cards.filter((c) => c.id !== id);
    void files.delete(id);
  }

  /** Records a part the builder checked, or forgets it (`null`). */
  check(card: Card, ref: string, check: Check | null): void {
    const build = card.variant ?? '';
    const checks = { ...(card.checks[build] ?? {}) };
    if (check) checks[ref] = check;
    else delete checks[ref];
    card.checks[build] = checks;
  }

  /** Records a part placed in bench mode, or takes it off. */
  place(card: Card, ref: string, placed: boolean): void {
    const build = card.variant ?? '';
    const refs = (card.placed[build] ?? []).filter((r) => r !== ref);
    card.placed[build] = placed ? [...refs, ref] : refs;
  }

  /** Forgets the card's bench progress for its chosen build. */
  startOver(card: Card): void {
    card.placed[card.variant ?? ''] = [];
  }

  setCount(card: Card, count: number): void {
    card.count = Math.min(MAX_COUNT, Math.max(1, Math.round(count) || 1));
  }

  save(): void {
    try {
      const saved: Saved = {
        cards: this.cards,
        stock: this.stock,
        options: this.options,
        display: this.display,
      };
      localStorage.setItem(STORAGE_KEY, JSON.stringify(saved));
    } catch {
      // Storage is full or blocked: the workspace lasts for this visit.
    }
  }
}

let shared: Workspace | undefined;

/**
 * The page's one workspace. The app and its sidebar are separate islands, and
 * both read this one, so the sidebar follows every change the app makes.
 */
export function workspace(): Workspace {
  if (!shared) {
    shared = new Workspace();
    // The list as last built, if the projects haven't changed since, so a
    // reload shows it at once instead of reading every document again.
    const next = request(shared.cards, shared.options);
    const saved = next ? savedListing(listingKey(next, shared.stock)) : null;
    if (saved) Object.assign(listing, saved);
  }
  return shared;
}

/** The shopping list as last built from the workspace, shared like it. */
export const listing = $state<{
  result: MasterListResult | null;
  updating: boolean;
  error: Failure | null;
  /** The cards the list's projects came from, in its order. */
  listed: string[];
  /** What the list was built from (`listingKey`), and when; null before the first. */
  key: string | null;
  builtAt: number;
}>({ result: null, updating: false, error: null, listed: [], key: null, builtAt: 0 });

// The last list built, kept in the browser beside the workspace. The API
// gives the same list for the same request, so it's reused until the request
// changes, or a day has passed and a publisher may have changed a document.
const LISTING_KEY = 'star-ground.listing.v1';
const LISTING_LIFE_MS = 24 * 60 * 60 * 1000;

/** What a list is built from: the request and the stock it subtracts. */
export function listingKey(next: MasterListRequest, stock: Stock | null): string {
  return JSON.stringify([next, stock?.text ?? null]);
}

/** Whether the list showing was built from this key within the last day. */
export function listingCurrent(key: string): boolean {
  return (
    listing.key === key && listing.result !== null && Date.now() - listing.builtAt < LISTING_LIFE_MS
  );
}

/** Shows a list just built, and keeps it for the next visit. */
export function keepListing(key: string, result: MasterListResult, listed: string[]): void {
  Object.assign(listing, { result, listed, key, builtAt: Date.now(), error: null });
  try {
    const saved: SavedListing = { key, builtAt: listing.builtAt, result, listed };
    localStorage.setItem(LISTING_KEY, JSON.stringify(saved));
  } catch {
    // Storage is full or blocked: the next visit builds the list again.
  }
}

interface SavedListing {
  key: string;
  builtAt: number;
  result: MasterListResult;
  listed: string[];
}

/** The list kept from a last visit, if it was built from this key. */
function savedListing(key: string): SavedListing | null {
  try {
    const saved = JSON.parse(localStorage.getItem(LISTING_KEY) ?? 'null') as SavedListing | null;
    return saved?.key === key && saved.result ? saved : null;
  } catch {
    return null;
  }
}

/** A card's project on the shopping list, as last built. */
export function projectOf(id: string): ProjectResult | null {
  return listing.result?.projects[listing.listed.indexOf(id)] ?? null;
}

/** The parts Star Ground's self-checks flagged on a card that the builder hasn't checked yet. */
export function flagged(card: Card, project: ProjectResult | null): number {
  if (!project) return 0;
  // eslint-disable-next-line svelte/prefer-svelte-reactivity -- rebuilt by each call
  const refs = new Set([
    ...project.reviews.filter((r) => r.warnings.length).map((r) => r.ref),
    ...project.ocr_reads.map((r) => r.ref),
  ]);
  const checks = checksOf(card);
  return [...refs].filter((ref) => !checks[ref]).length;
}

interface Saved {
  cards: Card[];
  stock: Stock | null;
  options: Options;
  display: Display;
}

function load(): Partial<Saved> {
  try {
    const saved = JSON.parse(localStorage.getItem(STORAGE_KEY) ?? '{}') as unknown;
    return saved && typeof saved === 'object' && !Array.isArray(saved) ? (saved as Saved) : {};
  } catch {
    return {};
  }
}

// Uploaded files, by card id.
const DB = 'star-ground';
const STORE = 'files';

function database(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const open = indexedDB.open(DB, 1);
    open.onupgradeneeded = () => open.result.createObjectStore(STORE);
    open.onsuccess = () => resolve(open.result);
    open.onerror = () => reject(open.error ?? new Error('IndexedDB is unavailable.'));
  });
}

async function transact<T>(
  mode: IDBTransactionMode,
  run: (store: IDBObjectStore) => IDBRequest,
): Promise<T | undefined> {
  try {
    const db = await database();
    return await new Promise((resolve, reject) => {
      const request = run(db.transaction(STORE, mode).objectStore(STORE));
      request.onsuccess = () => resolve(request.result as T);
      request.onerror = () => reject(request.error ?? new Error('IndexedDB failed.'));
    });
  } catch {
    return undefined;
  }
}

// Files read this visit, in case IndexedDB is unavailable.
// eslint-disable-next-line svelte/prefer-svelte-reactivity -- a cache, not UI state
const memory = new Map<string, File>();

export const files = {
  async put(id: string, file: File): Promise<void> {
    memory.set(id, file);
    await transact('readwrite', (s) => s.put(file, id));
  },
  async get(id: string): Promise<File | undefined> {
    return memory.get(id) ?? (await transact<File>('readonly', (s) => s.get(id)));
  },
  async delete(id: string): Promise<void> {
    memory.delete(id);
    await transact('readwrite', (s) => s.delete(id));
  },
};

/** A count with its noun: '1 part', '3 parts'. */
export function count(n: number, noun: string): string {
  return `${n} ${noun}${n === 1 ? '' : 's'}`;
}

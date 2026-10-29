// The build workspace's state: the builder's projects, kept in the browser.
// Cards and their readings live in localStorage; uploaded files, which can be
// tens of megabytes, live in IndexedDB. Either can be unavailable (a private
// window, blocked storage), and the workspace then works for the visit only.

import type { DocumentReading, MasterListRequest } from './api';
import type { Stock } from './server';

/** Where a project's parts list comes from. */
type Source =
  | { kind: 'paste'; text: string }
  | { kind: 'preset'; key: string }
  | { kind: 'upload'; fileName: string }
  | { kind: 'url'; url: string };

/** A project as the builder adds it, before it becomes a card. */
export type Added =
  | { kind: 'paste'; name: string; text: string }
  | { kind: 'preset'; key: string }
  | { kind: 'upload'; file: File }
  | { kind: 'url'; url: string };

/** Reading an upload or link: under way, waiting for OCR, done, or failed. */
export type ReadState =
  | { state: 'reading' }
  | { state: 'queued'; position: number }
  | { state: 'ready' }
  | { state: 'failed'; message: string; bugCode?: string | null };

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
}

const STORAGE_KEY = 'star-ground.workspace.v1';
const MAX_COUNT = 100;

/** The builds to choose from, given whether OCR's parts are accepted. */
export function builds(card: Card): string[] {
  if (!card.reading) return [];
  return card.useOcr ? card.reading.ocr_builds : card.reading.builds;
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

/** The master list request for the cards that are ready. */
export function request(cards: Card[], options: Options): MasterListRequest | null {
  const ready = cards.filter((c) => blocker(c) === null);
  if (!ready.length) return null;
  return {
    show_hardware: options.hardware,
    show_extras: options.extras,
    projects: ready.map((c) => {
      const base = { name: c.name, count: c.count, variant: c.variant, use_ocr: c.useOcr };
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

  constructor() {
    const saved = load();
    this.cards = saved.cards ?? [];
    this.stock = saved.stock ?? null;
    this.options = { ...this.options, ...saved.options };
    // A reading interrupted by a reload can't resume; offer to read again.
    for (const card of this.cards) {
      if (card.read.state === 'reading' || card.read.state === 'queued')
        card.read = { state: 'failed', message: 'Reading was interrupted. Read it again.' };
    }
  }

  add(card: Omit<Card, 'id' | 'variant' | 'useOcr' | 'reading' | 'read'>, read?: ReadState): Card {
    this.cards.push({
      ...card,
      id: crypto.randomUUID(),
      variant: null,
      useOcr: false,
      reading: null,
      read: read ?? { state: 'ready' },
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

  setCount(card: Card, count: number): void {
    card.count = Math.min(MAX_COUNT, Math.max(1, Math.round(count) || 1));
  }

  save(): void {
    try {
      const saved: Saved = { cards: this.cards, stock: this.stock, options: this.options };
      localStorage.setItem(STORAGE_KEY, JSON.stringify(saved));
    } catch {
      // Storage is full or blocked: the workspace lasts for this visit.
    }
  }
}

interface Saved {
  cards: Card[];
  stock: Stock | null;
  options: Options;
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

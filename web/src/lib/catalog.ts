// The circuit catalog's pages: publishers' and effects' names, the order the
// catalog lists builds in, and where each build's page on the publisher's
// site leads.

import type { CollectionEntry } from 'astro:content';

export type CatalogEntry = CollectionEntry<'catalog'>;
export type Publisher = CatalogEntry['data']['publisher'];
export type Effect = CatalogEntry['data']['effects'][number];

/** Publishers as the pages name them, in the order the catalog lists them. */
export const publishers: Record<Publisher, string> = {
  pedalpcb: 'PedalPCB',
  guitarpcb: 'GuitarPCB',
  tonepad: 'Tonepad',
  fivecats: 'Five Cats',
};

/** Kinds of effect as the filter names them, in the order it lists them. */
export const effects: Record<Effect, string> = {
  fuzz: 'Fuzz',
  overdrive: 'Overdrive',
  distortion: 'Distortion',
  boost: 'Boost',
  dynamics: 'Compressor & gate',
  modulation: 'Modulation',
  delay: 'Delay',
  reverb: 'Reverb',
  filter: 'Filter & EQ',
  amp: 'Amp & preamp',
  utility: 'Utility',
};

/** A build's page on this site: its entry id is '<publisher>/<id>'. */
export const buildPath = (entry: CatalogEntry): string => `/catalog/${entry.id}/`;

/**
 * What the build's page on the publisher's site is. A product page sells the
 * PCB; Tonepad's projects are free plans, and GuitarPCB's document lists only
 * link the document.
 */
export function publisherLink(entry: CatalogEntry): string {
  const name = publishers[entry.data.publisher];
  if (new URL(entry.data.page).pathname.includes('/product/')) return `Buy the PCB from ${name}`;
  if (entry.data.publisher === 'tonepad') return `${name}'s project page`;
  return `Where ${name} lists it`;
}

/** The number of parts in a build document, counting its largest build. */
export const partCount = (entry: CatalogEntry): number =>
  Math.max(0, ...entry.data.builds.map((b) => b.parts.reduce((sum, p) => sum + p.count, 0)));

// A title sorts by its first letter or digit: '“2112” - Dual Combo' under 2.
const sortTitle = (title: string): string => title.replace(/^[^\p{L}\p{N}]+/u, '') || title;

// Names that start with a letter come first, then numbers, then anything else.
const group = (title: string): number =>
  /^\p{L}/u.test(title) ? 0 : /^\p{N}/u.test(title) ? 1 : 2;

/** Entries A to Z, then those named by a number, then the rest; then by publisher. */
export function byTitle(a: CatalogEntry, b: CatalogEntry): number {
  const [x, y] = [sortTitle(a.data.title), sortTitle(b.data.title)];
  return (
    group(x) - group(y) ||
    x.localeCompare(y, 'en', { sensitivity: 'base', numeric: true }) ||
    a.data.publisher.localeCompare(b.data.publisher)
  );
}

/** The kinds of effect a first build may be: simple circuits, a few transistors or one chip. */
const FIRST_EFFECTS: readonly Effect[] = ['fuzz', 'overdrive', 'distortion', 'boost'];

/**
 * A pedal's name says what it is. Daughterboards, power boards and other
 * add-ons have the fewest parts, so the part count alone would put them
 * first; their names never say fuzz or drive.
 */
const PEDAL_NAME = /\b(?:fuzz|drive|overdrive|distortion|boost|booster)\b/i;

/** A first build's size, in parts and chips. */
export const FIRST_BUILD = { minParts: 10, maxParts: 30, maxChips: 1 } as const;

/**
 * Whether a build suits a first-time builder: checked by hand, read without
 * OCR, a single build with no versions to choose between, a simple kind of
 * effect that its name also says, and few parts and at most one chip.
 */
export function isFirstBuild(entry: CatalogEntry): boolean {
  const { data } = entry;
  const build = data.builds.length === 1 ? data.builds[0] : undefined;
  if (!build || !data.verified || data.read_by_ocr) return false;
  if (!data.effects.length || !data.effects.every((e) => FIRST_EFFECTS.includes(e))) return false;
  if (!PEDAL_NAME.test(data.title)) return false;
  const parts = partCount(entry);
  const chips = build.parts.filter((p) => p.category === 'ICs').reduce((n, p) => n + p.count, 0);
  return (
    parts >= FIRST_BUILD.minParts && parts <= FIRST_BUILD.maxParts && chips <= FIRST_BUILD.maxChips
  );
}

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

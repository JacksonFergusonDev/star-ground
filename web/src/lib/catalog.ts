// The circuit catalog's pages: publishers' names, and where each build's
// page on the publisher's site leads.

import type { CollectionEntry } from 'astro:content';

export type CatalogEntry = CollectionEntry<'catalog'>;
export type Publisher = CatalogEntry['data']['publisher'];

/** Publishers as the pages name them, in the order the catalog lists them. */
export const publishers: Record<Publisher, string> = {
  pedalpcb: 'PedalPCB',
  guitarpcb: 'GuitarPCB',
  tonepad: 'Tonepad',
  fivecats: 'Five Cats',
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

/** Entries by title, then publisher. */
export const byTitle = (a: CatalogEntry, b: CatalogEntry): number =>
  a.data.title.localeCompare(b.data.title, 'en', { sensitivity: 'base' }) ||
  a.data.publisher.localeCompare(b.data.publisher);

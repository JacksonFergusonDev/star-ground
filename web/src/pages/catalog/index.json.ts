// The catalog in brief, for the add box's search (`AddProject.svelte`): each
// build's name, publisher, effects and part count, and the document it adds.
// Fetched only when the search is first used.

import type { APIRoute } from 'astro';
import { getCollection } from 'astro:content';
import { byTitle, effects, partCount, publishers } from '../../lib/catalog';
import type { CatalogItem } from '../../lib/catalog-search';

export const GET: APIRoute = async () => {
  const entries = (await getCollection('catalog')).sort(byTitle);
  const items: CatalogItem[] = entries.map((entry) => ({
    id: entry.id,
    title: entry.data.title,
    publisher: publishers[entry.data.publisher],
    effects: entry.data.effects.map((e) => effects[e]),
    parts: partCount(entry),
    document: entry.data.document,
  }));
  return Response.json(items);
};

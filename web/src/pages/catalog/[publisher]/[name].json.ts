// What the workspace needs to add a catalog build as a card: fetched by
// `/app/?catalog=<publisher>/<name>`, which the build's "Add to build" opens.

import type { APIRoute, GetStaticPaths } from 'astro';
import { getCollection } from 'astro:content';
import type { CatalogEntry } from '../../../lib/catalog';

export const getStaticPaths = (async () => {
  const entries = await getCollection('catalog');
  return entries.map((entry) => {
    const [publisher, name] = entry.id.split('/');
    return { params: { publisher, name }, props: { entry } };
  });
}) satisfies GetStaticPaths;

export const GET: APIRoute = ({ props }) => {
  const { data } = (props as { entry: CatalogEntry }).entry;
  return Response.json({ title: data.title, document: data.document });
};

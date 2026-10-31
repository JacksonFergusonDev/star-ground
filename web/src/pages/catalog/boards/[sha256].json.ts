// Where a catalog document draws each part on its board and schematic, by
// the document's hash: the workspace hashes the PDF it draws and asks for
// `/catalog/boards/<hash>.json`, so an upload or link of the same file is
// highlighted too, and a document changed since isn't.

import type { APIRoute, GetStaticPaths } from 'astro';
import { getCollection } from 'astro:content';

export const getStaticPaths = (async () => {
  const entries = await getCollection('catalog');
  // One file per document: the same file can be listed on two pages.
  const byHash = new Map(
    entries.flatMap((e) =>
      e.data.boards && Object.keys(e.data.boards).length
        ? [[e.data.sha256, e.data.boards] as const]
        : [],
    ),
  );
  return [...byHash].map(([sha256, marks]) => ({ params: { sha256 }, props: { marks } }));
}) satisfies GetStaticPaths;

export const GET: APIRoute = ({ props }) => Response.json({ marks: props.marks });

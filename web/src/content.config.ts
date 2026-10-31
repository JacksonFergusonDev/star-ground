// The circuit catalog: one entry per listed build document, written by the
// catalog job (`python -m src.catalog`) to `catalog/builds/<publisher>/<id>.json`.
// The schema checks the job's format at build time, so a change to it fails
// the build instead of drawing a broken page.
//
// CATALOG_DIR points the build at other entries, relative to `web/`: the
// browser tests build with fixed ones, since the real catalog changes nightly.

import { defineCollection } from 'astro:content';
import { glob } from 'astro/loaders';
import { z } from 'astro/zod';

const location = z.object({
  page: z.number(),
  x0: z.number(),
  top: z.number(),
  x1: z.number(),
  bottom: z.number(),
});

const catalog = defineCollection({
  // Ids are '<publisher>/<id>', the entry's path without '.json'.
  loader: glob({ pattern: '*/*.json', base: process.env.CATALOG_DIR ?? '../catalog/builds' }),
  schema: z.object({
    id: z.string(),
    publisher: z.enum(['pedalpcb', 'guitarpcb', 'tonepad', 'fivecats']),
    title: z.string(),
    document: z.url(),
    page: z.url(),
    file_name: z.string(),
    sha256: z.string(),
    checked: z.string(),
    read_by_ocr: z.boolean(),
    verified: z.boolean(),
    builds: z.array(
      z.object({
        name: z.string().nullable(),
        parts: z.array(
          z.object({
            ref: z.string(),
            category: z.string(),
            value: z.string(),
            location: location.nullable(),
          }),
        ),
      }),
    ),
  }),
});

export const collections = { catalog };

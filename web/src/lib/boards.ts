// Where a document draws each part on its board and schematic. Only catalog
// documents have these (step 7 of PLAN.md): the catalog job finds them, and
// the site publishes them by the document's hash.

import type { SourceLocation } from './api';

/** Each designator's places on the document's board and schematic. */
export type BoardMarks = Record<string, SourceLocation[]>;

const END = new TextEncoder().encode('%%EOF');

/**
 * A PDF's SHA-256 up to and including its final `%%EOF`, as the catalog job's
 * `document_hash` computes it: Tonepad stamps each download after the marker.
 */
async function documentHash(bytes: ArrayBuffer): Promise<string> {
  const data = new Uint8Array(bytes);
  let end = data.length;
  search: for (let at = data.length - END.length; at >= 0; at--) {
    for (let i = 0; i < END.length; i++) if (data[at + i] !== END[i]) continue search;
    end = at + END.length;
    break;
  }
  const digest = await crypto.subtle.digest('SHA-256', data.subarray(0, end));
  return [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, '0')).join('');
}

/** The board marks for this document, or none when the catalog has no such file. */
export async function boardMarks(bytes: ArrayBuffer): Promise<BoardMarks | null> {
  try {
    const response = await fetch(`/catalog/boards/${await documentHash(bytes)}.json`);
    if (!response.ok) return null;
    return ((await response.json()) as { marks: BoardMarks }).marks;
  } catch {
    return null;
  }
}

/** Each place the board draws a part, as the document viewer outlines it. */
export function boardBoxes<T extends string>(
  marks: BoardMarks | null,
  tone: (ref: string) => T,
): { ref: string; location: SourceLocation; tone: T; board: true }[] {
  return Object.entries(marks ?? {}).flatMap(([ref, places]) =>
    places.map((location) => ({ ref, location, tone: tone(ref), board: true as const })),
  );
}

/** The part a pointer is over in a list of rows marked `data-ref`, if any. */
export function rowRef(event: Event): string | null {
  const row = (event.target as HTMLElement | null)?.closest<HTMLElement>('[data-ref]');
  return row?.dataset.ref ?? null;
}

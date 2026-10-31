// Share links: a workspace's projects in a URL, so opening it on another
// browser adds the same projects there. The projects ride in the fragment
// (`/app/#share=…`), which browsers never send to a server, compressed and
// base64url-encoded.
//
// An upload can't travel: its file stays in the browser that added it. A link
// carries everything else a card needs (its source, name, count, chosen
// build, OCR choice and corrections); the other side reads its documents
// again, as the original did.

import type { Card, Check } from './workspace.svelte';

/** A correction, as it travels: the part's designator and its fix. */
type SharedCorrection = { ref: string } & Extract<Check, { state: 'corrected' }>;

/** One project in a share link. */
export interface SharedProject {
  name: string;
  count: number;
  source:
    | { kind: 'paste'; text: string }
    | { kind: 'preset'; key: string }
    | { kind: 'url'; url: string; catalog?: string };
  variant: string | null;
  useOcr: boolean;
  corrections: SharedCorrection[];
}

interface Payload {
  version: 1;
  projects: SharedProject[];
}

const PREFIX = '#share=';

/** The projects of these cards that a link can carry, and how many uploads it can't. */
export function shareable(cards: Card[]): { projects: SharedProject[]; uploads: number } {
  const projects: SharedProject[] = [];
  let uploads = 0;
  for (const card of cards) {
    if (card.source.kind === 'upload') {
      uploads += 1;
      continue;
    }
    const checks = card.checks[card.variant ?? ''] ?? {};
    projects.push({
      name: card.name,
      count: card.count,
      source: card.source,
      variant: card.variant,
      useOcr: card.useOcr,
      corrections: Object.entries(checks).flatMap(([ref, check]) =>
        check.state === 'corrected' ? [{ ref, ...check }] : [],
      ),
    });
  }
  return { projects, uploads };
}

/** A link to the workspace that adds these projects. */
export async function shareLink(projects: SharedProject[], origin: string): Promise<string> {
  const payload: Payload = { version: 1, projects };
  const bytes = new TextEncoder().encode(JSON.stringify(payload));
  const packed = await transform(bytes, new CompressionStream('deflate-raw'));
  return `${origin}/app/${PREFIX}${toBase64Url(packed)}`;
}

/**
 * The projects in an address's fragment: none when it isn't a share link.
 *
 * @throws Error when it's a share link that can't be read (cut short in
 *   copying, or edited).
 */
export async function sharedProjects(hash: string): Promise<SharedProject[] | null> {
  if (!hash.startsWith(PREFIX)) return null;
  try {
    const packed = fromBase64Url(hash.slice(PREFIX.length));
    const bytes = await transform(packed, new DecompressionStream('deflate-raw'));
    const payload = JSON.parse(new TextDecoder().decode(bytes)) as Payload;
    if (payload.version !== 1 || !Array.isArray(payload.projects)) throw new Error('version');
    return payload.projects.filter(valid);
  } catch {
    throw new Error(
      "This share link couldn't be read. It may have been cut short when it was copied.",
    );
  }
}

function valid(project: SharedProject): boolean {
  const { source } = project;
  return (
    typeof project.name === 'string' &&
    typeof project.count === 'number' &&
    Array.isArray(project.corrections) &&
    ((source.kind === 'paste' && typeof source.text === 'string') ||
      (source.kind === 'preset' && typeof source.key === 'string') ||
      (source.kind === 'url' && /^https?:\/\//.test(source.url)))
  );
}

async function transform(
  bytes: Uint8Array,
  stream: CompressionStream | DecompressionStream,
): Promise<Uint8Array> {
  const piped = new Blob([bytes as BlobPart]).stream().pipeThrough(stream);
  return new Uint8Array(await new Response(piped).arrayBuffer());
}

function toBase64Url(bytes: Uint8Array): string {
  let binary = '';
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

function fromBase64Url(text: string): Uint8Array {
  const binary = atob(text.replace(/-/g, '+').replace(/_/g, '/'));
  return Uint8Array.from(binary, (c) => c.charCodeAt(0));
}

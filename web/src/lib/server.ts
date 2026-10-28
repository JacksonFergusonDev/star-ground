// Calls to Star Ground's API. The site and the API share an origin when the
// Docker image serves both; on GitHub Pages, PUBLIC_API_URL names the API.

import { PUBLIC_API_URL } from 'astro:env/client';
import type {
  DocumentReading,
  DocumentsMissing,
  MasterListRequest,
  MasterListResult,
  PresetCatalog,
  Problem,
  ReadEvent,
} from './api';

const api = (path: string): string => `${PUBLIC_API_URL}/api/${path}`;

const BUSY = 'Star Ground is busy right now. Try again in a minute.';
const UNREACHABLE = "Couldn't reach Star Ground's server. Check your connection and try again.";

// Whether the API answered the page's warm-up call. When Cloud Run is at its
// instance cap it refuses requests itself, without the header that lets the
// site read the answer, so the browser reports a network failure. If the API
// answered a moment ago, that failure almost certainly means "busy".
let reachable = false;

/** A refusal the reader should see, with the API's message. */
class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly body: Partial<Problem> & Record<string, unknown> = {},
  ) {
    super(message);
  }
}

/** Wakes the API, which scales to zero, while the reader is still typing. */
export function warmUp(): void {
  fetch(api('health'))
    .then((response) => {
      reachable = response.ok;
    })
    .catch(() => {});
}

async function send(path: string, init?: RequestInit): Promise<Response> {
  let response: Response;
  try {
    response = await fetch(api(path), init);
  } catch {
    throw new ApiError(reachable ? BUSY : UNREACHABLE, 0);
  }
  if (response.ok) return response;
  const body = (await response.json().catch(() => null)) as ApiError['body'] | null;
  if (body?.message) throw new ApiError(body.message, response.status, body);
  // Cloud Run's own refusals (no capacity, an overloaded instance) carry no message.
  if (response.status === 429 || response.status >= 500) throw new ApiError(BUSY, response.status);
  throw new ApiError(`The server answered ${response.status}. Try again.`, response.status);
}

/** The preset library. */
export async function presets(): Promise<PresetCatalog> {
  return (await (await send('presets')).json()) as PresetCatalog;
}

/** Where a document comes from: an upload or a link. */
export type DocumentSource = { file: File } | { url: string };

/**
 * Reads an upload or link ahead of the master list, reporting progress.
 *
 * @param source The upload or link.
 * @param ocr Read image pages by OCR; only once the builder accepts.
 * @param progress Called with each event before the result.
 */
export async function readDocument(
  source: DocumentSource,
  ocr: boolean,
  progress: (event: ReadEvent) => void = () => {},
): Promise<DocumentReading> {
  const body = new FormData();
  if ('file' in source) body.set('file', source.file);
  else body.set('url', source.url);
  if (ocr) body.set('ocr', 'true');
  const response = await send('documents/read', { method: 'POST', body });
  const reader = response.body?.pipeThrough(new TextDecoderStream()).getReader();
  if (!reader) throw new ApiError('The server sent no reading. Try again.', response.status);
  let buffer = '';
  for (;;) {
    const { done, value } = await reader.read();
    buffer += value ?? '';
    const lines = buffer.split('\n');
    buffer = done ? '' : (lines.pop() ?? '');
    for (const line of lines) {
      if (!line.startsWith('data: ')) continue;
      const event = JSON.parse(line.slice(6)) as ReadEvent;
      if (event.event === 'result') return event.reading;
      if (event.event === 'error') throw new ApiError(event.message, response.status);
      progress(event);
    }
    if (done) throw new ApiError('The reading stopped early. Try again.', response.status);
  }
}

/**
 * Merges the projects into one master list.
 *
 * Uploads are named by their reading's `document`, so files aren't sent with
 * every change. If the server no longer holds one, it's sent once as a file.
 *
 * @param request The projects, with uploads named by `document`.
 * @param file The file a `document` names, to send when the server lacks it.
 */
export async function masterList(
  request: MasterListRequest,
  file: (document: string) => Promise<File | undefined>,
): Promise<MasterListResult> {
  const post = async (body: FormData) =>
    (await (await send('master-list', { method: 'POST', body })).json()) as MasterListResult;
  try {
    return await post(form(request, []));
  } catch (error) {
    if (!(error instanceof ApiError) || error.status !== 409) throw error;
    const missing = new Set((error.body as Partial<DocumentsMissing>).documents ?? []);
    const files: File[] = [];
    const projects = await Promise.all(
      request.projects.map(async (project) => {
        if (project.method !== 'upload' || !project.document || !missing.has(project.document))
          return project;
        const found = await file(project.document);
        if (!found) throw new ApiError('An uploaded document is missing. Add it again.', 409);
        files.push(found);
        return { ...project, file: found.name, document: null };
      }),
    );
    return post(form({ ...request, projects }, files));
  }
}

function form(request: MasterListRequest, files: File[]): FormData {
  const body = new FormData();
  body.set('request', JSON.stringify(request));
  const sent = new Set<string>();
  for (const file of files) {
    if (sent.has(file.name)) continue;
    sent.add(file.name);
    body.append('files', file);
  }
  return body;
}

// Calls to Star Ground's API. The site and the API share an origin when the
// Docker image serves both; on GitHub Pages, PUBLIC_API_URL names the API.

import { PUBLIC_API_URL } from 'astro:env/client';
import type {
  DocumentReading,
  DocumentsMissing,
  ExportKind,
  MasterListRequest,
  MasterListResult,
  PresetCatalog,
  Problem,
  ReadEvent,
  ReportIn,
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
  if (body?.message) throw new ApiError(explain(body as Problem), response.status, body);
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
      if (event.event === 'error') throw new ApiError(explain(event), response.status, event);
      progress(event);
    }
    if (done) throw new ApiError('The reading stopped early. Try again.', response.status);
  }
}

/** The builder's stock CSV (columns Category, Part, Qty), sent with each request. */
export interface Stock {
  name: string;
  text: string;
}

/** The file a reading's `document` names, for a server that no longer holds it. */
type FileFor = (document: string) => Promise<File | undefined>;

/**
 * Posts a master list request, to build the list or export it.
 *
 * Uploads are named by their reading's `document`, so files aren't sent with
 * every change. If the server no longer holds one, it's sent once as a file.
 */
async function submit(
  path: string,
  request: MasterListRequest,
  file: FileFor,
  stock: Stock | null,
): Promise<Response> {
  try {
    return await send(path, { method: 'POST', body: form(request, [], stock) });
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
    return send(path, { method: 'POST', body: form({ ...request, projects }, files, stock) });
  }
}

/** Merges the projects into one master list. */
export async function masterList(
  request: MasterListRequest,
  file: FileFor,
  stock: Stock | null,
): Promise<MasterListResult> {
  return (await (await submit('master-list', request, file, stock)).json()) as MasterListResult;
}

/** A file to save: an export of the master list. */
export interface Download {
  blob: Blob;
  filename: string;
}

/**
 * Exports the master list as a CSV or ZIP.
 *
 * @param kind Which file.
 * @param excel Write supplier links as Excel `HYPERLINK`s rather than URLs.
 */
export async function exportFile(
  kind: ExportKind,
  excel: boolean,
  request: MasterListRequest,
  file: FileFor,
  stock: Stock | null,
): Promise<Download> {
  const response = await submit(
    `exports/${kind}?links=${excel ? 'excel' : 'plain'}`,
    request,
    file,
    stock,
  );
  const disposition = response.headers.get('content-disposition') ?? '';
  const filename = /filename="([^"]+)"/.exec(disposition)?.[1] ?? `${kind}`;
  return { blob: await response.blob(), filename };
}

function form(request: MasterListRequest, files: File[], stock: Stock | null): FormData {
  const body = new FormData();
  body.set('request', JSON.stringify(request));
  const sent = new Set<string>();
  for (const file of files) {
    if (sent.has(file.name)) continue;
    sent.add(file.name);
    body.append('files', file);
  }
  if (stock) body.set('stock', new File([stock.text], stock.name, { type: 'text/csv' }));
  return body;
}

/** A problem as the builder reads it: what happened, then what to do. */
export function explain(problem: Problem): string {
  return [problem.message, problem.hint].filter(Boolean).join(' ');
}

/** Sends a builder's bug report: its code and what was being read, never the document. */
export async function sendReport(report: ReportIn): Promise<void> {
  await send('reports', {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(report),
  });
}

/** Whether a refusal means this server can't take reports at all. */
export function reportsUnavailable(error: unknown): boolean {
  return error instanceof ApiError && error.status === 503;
}

/** A request that failed: what to tell the builder, and the bug's code if it was one. */
export interface Failure {
  message: string;
  bugCode: string | null;
}

/** The code of the bug a failed request carries, if it was a bug in Star Ground. */
export function bugOf(error: unknown): string | null {
  return error instanceof ApiError ? (error.body.bug_code ?? null) : null;
}

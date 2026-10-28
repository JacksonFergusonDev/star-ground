// Calls to Star Ground's API. The site and the API share an origin when the
// Docker image serves both; on GitHub Pages, PUBLIC_API_URL names the API.

import { PUBLIC_API_URL } from 'astro:env/client';
import type { MasterListRequest, MasterListResult, Problem } from './api';

const api = (path: string): string => `${PUBLIC_API_URL}/api/${path}`;

const BUSY = 'Star Ground is busy right now. Try again in a minute.';

// Whether the API answered the page's warm-up call. When Cloud Run is at its
// instance cap it refuses requests itself, without the header that lets the
// site read the answer, so the browser reports a network failure. If the API
// answered a moment ago, that failure almost certainly means "busy".
let reachable = false;

/** Wakes the API, which scales to zero, while the reader is still typing. */
export function warmUp(): void {
  fetch(api('health'))
    .then((response) => {
      reachable = response.ok;
    })
    .catch(() => {});
}

/** Merges the projects into one master list, or throws with a message for the reader. */
export async function masterList(request: MasterListRequest): Promise<MasterListResult> {
  const body = new FormData();
  body.set('request', JSON.stringify(request));
  let response: Response;
  try {
    response = await fetch(api('master-list'), { method: 'POST', body });
  } catch {
    throw new Error(
      reachable
        ? BUSY
        : "Couldn't reach Star Ground's server. Check your connection and try again.",
    );
  }
  if (response.ok) return (await response.json()) as MasterListResult;
  const problem = (await response.json().catch(() => null)) as Partial<Problem> | null;
  if (problem?.message) throw new Error(problem.message);
  // Cloud Run's own refusals (no capacity, an overloaded instance) carry no message.
  if (response.status === 429 || response.status >= 500) throw new Error(BUSY);
  throw new Error(`The server answered ${response.status}. Try again.`);
}

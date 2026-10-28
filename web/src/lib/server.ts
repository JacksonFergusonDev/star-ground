// Calls to Star Ground's API. The site and the API share an origin when the
// Docker image serves both; on GitHub Pages, PUBLIC_API_URL names the API.

import { PUBLIC_API_URL } from 'astro:env/client';
import type { MasterListRequest, MasterListResult, Problem } from './api';

const api = (path: string): string => `${PUBLIC_API_URL}/api/${path}`;

/** Wakes the API, which scales to zero, while the reader is still typing. */
export function warmUp(): void {
  fetch(api('health')).catch(() => {});
}

/** Merges the projects into one master list, or throws with the API's message. */
export async function masterList(request: MasterListRequest): Promise<MasterListResult> {
  const body = new FormData();
  body.set('request', JSON.stringify(request));
  let response: Response;
  try {
    response = await fetch(api('master-list'), { method: 'POST', body });
  } catch {
    throw new Error("Couldn't reach Star Ground's server. Check your connection and try again.");
  }
  if (response.ok) return (await response.json()) as MasterListResult;
  const problem = (await response.json().catch(() => null)) as Partial<Problem> | null;
  throw new Error(problem?.message ?? `The server answered ${response.status}. Try again.`);
}

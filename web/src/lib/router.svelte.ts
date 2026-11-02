// The app's pages. Each has its own address under /app/, built as a static
// page, so a reload or a shared address opens it directly. Moving between them
// changes the address without reloading, so the workspace and its shopping
// list stay as they are.

/** One of the app's pages. */
export type View = 'projects' | 'list' | 'stock' | 'export' | 'check' | 'build';

/** Each page's address and title. */
export const views: Record<View, { path: string; title: string }> = {
  projects: { path: '/app/', title: 'Projects' },
  list: { path: '/app/list/', title: 'Shopping list' },
  stock: { path: '/app/stock/', title: 'Stock' },
  export: { path: '/app/export/', title: 'Export' },
  check: { path: '/app/check/', title: 'Check parts' },
  build: { path: '/app/build/', title: 'Build' },
};

/** The page an address shows, or null outside the app. */
function viewOf(pathname: string): View | null {
  const path = pathname.endsWith('/') ? pathname : `${pathname}/`;
  const found = Object.entries(views).find(([, v]) => v.path === path);
  return found ? (found[0] as View) : null;
}

/** The address that checks a project's parts against its document, at a part if given. */
export function checkHref(project: string, part: string | null = null): string {
  const at = part ? `&part=${encodeURIComponent(part)}` : '';
  return `${views.check.path}?project=${encodeURIComponent(project)}${at}`;
}

/** The address that builds a project part by part. */
export function buildHref(project: string): string {
  return `${views.build.path}?project=${encodeURIComponent(project)}`;
}

class Router {
  /** The current address; null while the page is built, outside a browser. */
  url = $state<URL | null>(null);
  /** Whether the reader has moved since the page loaded. */
  moved = $state(false);

  constructor() {
    if (typeof window === 'undefined') return;
    this.url = new URL(window.location.href);
    window.addEventListener('popstate', () => {
      this.url = new URL(window.location.href);
      this.moved = true;
    });
  }

  get view(): View | null {
    return this.url ? viewOf(this.url.pathname) : null;
  }

  param(name: string): string | null {
    return this.url?.searchParams.get(name) ?? null;
  }

  /** Moves to another page of the app. */
  go(href: string): void {
    history.pushState(null, '', href);
    this.url = new URL(window.location.href);
    this.moved = true;
  }

  /** Changes the address without adding a step to the history, as tidying it does. */
  replace(href: string): void {
    history.replaceState(history.state, '', href);
    this.url = new URL(window.location.href);
  }
}

export const router = new Router();

/**
 * Takes over clicks on links to the app's own pages, so moving between them
 * keeps the workspace in memory. A click with a modifier key, or on a link to
 * anywhere else, works as usual. Returns the function that stops it.
 */
export function followAppLinks(): () => void {
  function click(event: MouseEvent): void {
    if (event.defaultPrevented || event.button !== 0) return;
    if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    const link = (event.target as Element | null)?.closest('a');
    if (!link || link.target || link.hasAttribute('download')) return;
    // eslint-disable-next-line svelte/prefer-svelte-reactivity -- read once, not UI state
    const url = new URL(link.href, window.location.href);
    if (url.origin !== window.location.origin || viewOf(url.pathname) === null) return;
    // A link to a part of the same page, such as the skip link, scrolls as usual.
    if (
      url.pathname === window.location.pathname &&
      url.search === window.location.search &&
      url.hash
    )
      return;
    event.preventDefault();
    router.go(url.pathname + url.search + url.hash);
  }
  document.addEventListener('click', click);
  return () => document.removeEventListener('click', click);
}

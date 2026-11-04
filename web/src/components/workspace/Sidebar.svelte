<script lang="ts">
  // The app's navigation, beside every app and catalog page: the workspace's
  // pages, each project with its own pages, and the catalog. It reads the
  // page's one workspace, so it follows every change the app makes. On phones
  // it folds into a bar with a menu.
  import { onMount } from 'svelte';
  import { buildHref, checkHref, router, views } from '../../lib/router.svelte';
  import type { Card, Workspace } from '../../lib/workspace.svelte';
  import { flagged, listing, projectOf, reviewable, workspace } from '../../lib/workspace.svelte';
  import Icon from './Icon.svelte';

  /** The page's path, for the sidebar drawn when the site is built. */
  let { path: builtPath }: { path: string } = $props();

  // The workspace lives in browser storage, so the built sidebar has none.
  let ws = $state.raw<Workspace | null>(null);
  onMount(() => (ws = workspace()));

  const path = $derived(router.url?.pathname ?? builtPath);
  const activeProject = $derived(
    router.view === 'check' || router.view === 'build' ? router.param('project') : null,
  );
  const cards = $derived(ws?.cards ?? []);
  const toBuy = $derived(listing.result?.shopping_list.filter((r) => r.buy_qty > 0).length ?? null);

  const pages = $derived([
    { ...views.projects, label: 'Projects', badge: ws ? String(cards.length) : '' },
    { ...views.list, label: 'Shopping list', badge: toBuy === null ? '' : String(toBuy) },
    { ...views.stock, label: 'Stock', badge: ws?.stock ? 'CSV' : '' },
    { ...views.export, label: 'Export', badge: '' },
  ]);

  // A project opens on its check when it has a document, on bench mode when it
  // has parts, and otherwise at its card.
  function projectHref(card: Card): string {
    if (reviewable(card)) return checkHref(card.id);
    if (projectOf(card.id)?.build_order.length) return buildHref(card.id);
    return `${views.projects.path}#project-${card.id}`;
  }

  let open = $state(false);
  // Moving to another page closes the menu.
  $effect(() => {
    void router.url;
    open = false;
  });
</script>

<svelte:window
  onkeydown={(e) => {
    if (open && e.key === 'Escape') open = false;
  }}
/>

<aside class="side" class:open aria-label="Star Ground">
  <div class="top">
    <a class="mark" href="/">
      <img src="/favicon.svg" alt="" width="26" height="26" />
      Star Ground
    </a>
    <button
      class="menu"
      type="button"
      aria-expanded={open}
      aria-controls="app-nav"
      onclick={() => (open = !open)}
    >
      {#if open}<Icon name="close" size={14} />{/if}
      Menu
    </button>
  </div>

  <div class="drawer" id="app-nav">
    <nav aria-label="App">
      <p class="group">Workspace</p>
      <ul>
        {#each pages as page (page.path)}
          <li>
            <a class="item" href={page.path} aria-current={path === page.path ? 'page' : undefined}>
              <span class="label">{page.label}</span>
              {#if page.badge}<span class="badge">{page.badge}</span>{/if}
            </a>
          </li>
        {/each}
      </ul>

      {#if cards.length}
        <p class="group">Your projects</p>
        <ul>
          {#each cards as card (card.id)}
            {@const project = projectOf(card.id)}
            {@const flags = flagged(card, project)}
            {@const active = activeProject === card.id}
            <li>
              <a
                class="item project"
                href={projectHref(card)}
                aria-current={active ? 'true' : undefined}
              >
                <span class="label">{card.name}</span>
                {#if card.read.state === 'reading' || card.read.state === 'queued'}
                  <span class="badge">Reading</span>
                {:else if flags}
                  <span class="badge flag" title="Parts to check">{flags}</span>
                {:else if card.count > 1}
                  <span class="badge">×{card.count}</span>
                {/if}
              </a>
              {#if active}
                <ul class="sub">
                  {#if reviewable(card)}
                    <li>
                      <a
                        class="item"
                        href={checkHref(card.id)}
                        aria-current={router.view === 'check' ? 'page' : undefined}
                      >
                        Check parts
                      </a>
                    </li>
                  {/if}
                  {#if project?.build_order.length}
                    <li>
                      <a
                        class="item"
                        href={buildHref(card.id)}
                        aria-current={router.view === 'build' ? 'page' : undefined}
                      >
                        Build
                      </a>
                    </li>
                  {/if}
                </ul>
              {/if}
            </li>
          {/each}
        </ul>
      {/if}

      <p class="group">Browse</p>
      <ul>
        <li>
          <a
            class="item"
            href="/catalog/"
            aria-current={path.startsWith('/catalog/') ? 'page' : undefined}
          >
            <span class="label">Circuit catalog</span>
          </a>
        </li>
      </ul>
    </nav>

    <div class="foot">
      <p>Your projects stay in this browser.</p>
      <a href="/"><Icon name="star-ground" size={16} /> About Star Ground</a>
      <a href="https://github.com/JacksonFergusonDev/star-ground">
        <Icon name="brand-github" size={14} /> GitHub
      </a>
      <a href="https://jacksonferguson.me/">
        <Icon name="jacksonferguson" size={16} /> jacksonferguson.me
      </a>
    </div>
  </div>
</aside>

<style>
  .side {
    position: sticky;
    top: 0;
    display: flex;
    flex-direction: column;
    height: 100vh;
    height: 100dvh;
    border-right: 1px solid var(--line);
    background: var(--panel);
  }
  .top {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 20px 20px 12px;
  }
  .mark {
    display: inline-flex;
    gap: 10px;
    align-items: center;
    color: var(--text);
    font-size: var(--fs-h4);
    font-weight: 500;
    letter-spacing: -0.3px;
  }
  .mark:hover {
    color: var(--text);
  }
  .menu {
    display: none;
  }
  .drawer {
    display: flex;
    flex: 1;
    flex-direction: column;
    justify-content: space-between;
    min-height: 0;
    overflow-y: auto;
    padding: 0 12px 20px;
  }
  .group {
    margin: 22px 10px 6px;
    color: var(--muted);
    font-family: var(--mono);
    font-size: var(--fs-label);
    letter-spacing: 0.08em;
    text-transform: uppercase;
  }
  ul {
    display: grid;
    gap: 1px;
    margin: 0;
    padding: 0;
    list-style: none;
  }
  .item {
    position: relative;
    display: flex;
    gap: 10px;
    align-items: center;
    justify-content: space-between;
    padding: 7px 10px 7px 18px;
    border-radius: 5px;
    color: var(--muted);
    font-size: var(--fs-ui);
  }
  .item:hover {
    color: var(--text);
    background: color-mix(in srgb, var(--muted) 8%, transparent);
  }
  .item[aria-current] {
    color: var(--text);
    background: color-mix(in srgb, var(--muted) 8%, transparent);
  }
  .item[aria-current='page'] {
    background: var(--accent-soft);
  }
  /* The current page ends a trace run in from the sidebar's edge, at a pad. */
  .item[aria-current='page']::before {
    position: absolute;
    top: calc(50% - 1px);
    left: -12px;
    width: 16px;
    height: 2px;
    background: var(--accent);
    content: '';
  }
  .item[aria-current='page']::after {
    position: absolute;
    top: calc(50% - 4px);
    left: 3px;
    width: 8px;
    height: 8px;
    border: 2px solid var(--accent);
    border-radius: 50%;
    background: var(--panel);
    box-sizing: border-box;
    content: '';
  }
  /* A project's own pages branch off its line. */
  .sub .item[aria-current='page']::before {
    left: -9px;
    width: 13px;
  }
  .label {
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .badge {
    flex-shrink: 0;
    color: var(--muted);
    font-family: var(--mono);
    font-size: var(--fs-label);
    font-variant-numeric: tabular-nums;
  }
  .flag {
    min-width: 20px;
    padding: 1px 6px;
    border: 1px solid var(--accent-border);
    border-radius: 999px;
    color: var(--accent);
    text-align: center;
  }
  .sub {
    margin: 1px 0 4px 12px;
    padding-left: 8px;
    border-left: 1px solid var(--line);
  }
  .foot {
    display: grid;
    gap: 8px;
    margin: 28px 10px 0;
    padding-top: 18px;
    border-top: 1px solid var(--line);
    font-size: var(--fs-ui);
  }
  .foot p {
    margin: 0 0 4px;
    color: var(--muted);
    line-height: 1.5;
  }
  .foot a {
    display: inline-flex;
    gap: 8px;
    align-items: center;
    justify-self: start;
    color: var(--muted);
  }
  .foot a:hover {
    color: var(--accent);
  }
  a:focus-visible,
  button:focus-visible {
    outline: 2px solid var(--accent);
    outline-offset: 2px;
  }

  /* On phones the sidebar is a bar along the top, and its menu opens below it. */
  @media (width <= 900px) {
    .side {
      z-index: 3;
      height: auto;
      border-right: 0;
      border-bottom: 1px solid var(--line);
    }
    .top {
      height: var(--hs-bar-height);
      padding: 0 16px;
    }
    .menu {
      display: inline-flex;
      gap: 6px;
      align-items: center;
      padding: 7px 12px;
      border: 1px solid var(--line);
      border-radius: 5px;
      color: var(--text);
      background: none;
      font: inherit;
      font-size: var(--fs-ui);
      cursor: pointer;
    }
    .drawer {
      position: absolute;
      top: 100%;
      right: 0;
      left: 0;
      display: none;
      max-height: calc(100dvh - var(--hs-bar-height));
      padding: 0 16px 20px;
      border-bottom: 1px solid var(--line);
      background: var(--panel);
    }
    .open .drawer {
      display: flex;
    }
  }
</style>

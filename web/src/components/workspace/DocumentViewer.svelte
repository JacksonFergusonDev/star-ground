<script lang="ts" module>
  import type { SourceLocation } from '../../lib/api';

  /** A part outlined where it was read, or where the board draws it. */
  export interface Box {
    ref: string;
    location: SourceLocation;
    /** Flagged by a self-check, checked by the builder, or neither. */
    tone: 'flagged' | 'checked' | 'plain';
    /** A place on the board or schematic: shown for the part at hand. */
    board?: boolean;
  }
</script>

<script lang="ts">
  // A build document's pages, with each part outlined where it was read, and
  // for a catalog document, lit up where its board and schematic draw it.
  // Pages draw as they scroll near, at the panel's width times the zoom; the
  // boxes are placed in points, so they scale with the page.
  import type { PDFDocumentProxy } from 'pdfjs-dist';
  import { onDestroy, onMount } from 'svelte';
  import type { PageSize } from '../../lib/pdf';
  import { drawPage, openPdf, pageSizes } from '../../lib/pdf';

  let {
    load,
    boxes,
    selected,
    onSelect,
    hovered = null,
    onHover = () => {},
    prefer = 'list',
  }: {
    load: () => Promise<ArrayBuffer>;
    boxes: Box[];
    selected: string | null;
    onSelect: (ref: string) => void;
    /** A part pointed at in the list, lit up here too. */
    hovered?: string | null;
    /** Pointing at a part here, or away (null). */
    onHover?: (ref: string | null) => void;
    /** Where to bring the chosen part into view: its row, or its board. */
    prefer?: 'list' | 'board';
  } = $props();

  const ZOOMS = [1, 1.5, 2, 3];

  let pdf = $state.raw<PDFDocumentProxy | null>(null);
  let pages = $state.raw<PageSize[]>([]);
  let problem = $state('');
  let zoom = $state(1);
  let width = $state(0);
  let scroller = $state<HTMLDivElement>();
  const canvases: HTMLCanvasElement[] = [];
  const pageElements: HTMLDivElement[] = [];
  let near = $state.raw(new Set<number>());

  const pageWidth = $derived(Math.max(0, width - 2) * zoom);
  // The width pages are drawn at, once a resize settles: drawing at every
  // step of a resize would redraw each page dozens of times.
  let drawWidth = $state(0);
  const byPage = $derived.by(() => {
    const grouped: Box[][] = pages.map(() => []);
    for (const box of boxes) grouped[box.location.page]?.push(box);
    return grouped;
  });

  onMount(() => {
    load()
      .then(openPdf)
      .then(async (opened) => {
        pages = await pageSizes(opened);
        pdf = opened;
      })
      .catch((e: Error) => {
        problem = e.message || "This document couldn't be drawn.";
      });
  });

  onDestroy(() => {
    for (const page of drawn.values()) page.cancel();
    void pdf?.loadingTask.destroy();
  });

  // Draw only the pages near the view: a long document has dozens.
  $effect(() => {
    if (!scroller || !pages.length) return;
    const observer = new IntersectionObserver(
      (entries) => {
        // A new set each time, assigned to state: nothing mutates it later.
        // eslint-disable-next-line svelte/prefer-svelte-reactivity
        const next = new Set(near);
        for (const entry of entries) {
          const index = Number((entry.target as HTMLElement).dataset.page);
          if (entry.isIntersecting) next.add(index);
          else next.delete(index);
        }
        near = next;
      },
      { root: scroller, rootMargin: '600px 0px' },
    );
    for (const element of pageElements) if (element) observer.observe(element);
    return () => observer.disconnect();
  });

  $effect(() => {
    const next = pageWidth;
    const timer = setTimeout(() => (drawWidth = next), drawWidth ? 150 : 0);
    return () => clearTimeout(timer);
  });

  // Each page drawn, and at what width. A page scrolled far away is cleared,
  // so a long document doesn't hold every page's bitmap.
  // eslint-disable-next-line svelte/prefer-svelte-reactivity -- bookkeeping, not UI state
  const drawn = new Map<number, { width: number; cancel: () => void }>();
  $effect(() => {
    if (!pdf || !drawWidth) return;
    for (const [index, page] of drawn) {
      if (near.has(index)) continue;
      page.cancel();
      const canvas = canvases[index];
      if (canvas) canvas.width = canvas.height = 0;
      drawn.delete(index);
    }
    for (const index of near) {
      const canvas = canvases[index];
      if (!canvas || drawn.get(index)?.width === drawWidth) continue;
      drawn.get(index)?.cancel();
      drawn.set(index, { width: drawWidth, cancel: drawPage(pdf, index, canvas, drawWidth) });
    }
  });

  // Bring the chosen part into the middle of the view.
  $effect(() => {
    const mine = boxes.filter((b) => b.ref === selected);
    const box = mine.find((b) => !!b.board === (prefer === 'board')) ?? mine[0];
    const page = box && pageElements[box.location.page];
    const size = box && pages[box.location.page];
    if (!box || !page || !size || !scroller || !pageWidth) return;
    const scale = pageWidth / size.width;
    const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    scroller.scrollTo({
      top: page.offsetTop + box.location.top * scale - scroller.clientHeight / 2,
      left: page.offsetLeft + box.location.x0 * scale - scroller.clientWidth / 3,
      behavior: reduced ? 'auto' : 'smooth',
    });
  });

  const percent = (n: number, of: number): string => `${(n / of) * 100}%`;
</script>

<div class="viewer">
  <div class="tools">
    <span class="label">
      {pages.length ? `${pages.length} ${pages.length === 1 ? 'page' : 'pages'}` : ''}
    </span>
    <div class="zoom" role="group" aria-label="Zoom">
      <button
        type="button"
        disabled={zoom === ZOOMS[0]}
        onclick={() => (zoom = ZOOMS[ZOOMS.indexOf(zoom) - 1] ?? zoom)}
        aria-label="Zoom out">−</button
      >
      <span class="label" aria-live="polite">{Math.round(zoom * 100)}%</span>
      <button
        type="button"
        disabled={zoom === ZOOMS[ZOOMS.length - 1]}
        onclick={() => (zoom = ZOOMS[ZOOMS.indexOf(zoom) + 1] ?? zoom)}
        aria-label="Zoom in">+</button
      >
    </div>
  </div>
  <div class="pages" bind:this={scroller} bind:clientWidth={width}>
    {#if problem}
      <p class="note" role="alert">{problem}</p>
    {:else if !pages.length}
      <p class="note" role="status">Opening the document…</p>
    {/if}
    {#each pages as size, index (index)}
      <div
        class="page"
        data-page={index}
        bind:this={pageElements[index]}
        style:width={`${pageWidth}px`}
        style:aspect-ratio={`${size.width} / ${size.height}`}
      >
        <canvas bind:this={canvases[index]} aria-label={`Page ${index + 1}`}></canvas>
        {#each byPage[index] ?? [] as box, i (`${box.ref}:${i}`)}
          <button
            type="button"
            tabindex="-1"
            class={`box ${box.tone}`}
            class:board={box.board}
            class:selected={box.ref === selected}
            class:hovered={box.ref === hovered}
            style:left={percent(box.location.x0 - 2, size.width)}
            style:top={percent(box.location.top - 2, size.height)}
            style:width={percent(box.location.x1 - box.location.x0 + 4, size.width)}
            style:height={percent(box.location.bottom - box.location.top + 4, size.height)}
            aria-label={box.board
              ? `${box.ref} on the board: show it in the parts list`
              : `Show ${box.ref} in the parts list`}
            onclick={() => onSelect(box.ref)}
            onmouseenter={() => onHover(box.ref)}
            onmouseleave={() => onHover(null)}
          ></button>
        {/each}
      </div>
    {/each}
  </div>
</div>

<style>
  .viewer {
    display: grid;
    grid-template-rows: auto minmax(0, 1fr);
    height: 100%;
    min-height: 0;
    border: 1px solid var(--line);
    border-radius: 5px;
    background: var(--panel);
  }
  .tools {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    padding: 8px 12px;
    border-bottom: 1px solid var(--line);
  }
  .label {
    color: var(--muted);
    font-family: var(--mono);
    font-size: var(--fs-label);
    letter-spacing: 0.08em;
    text-transform: uppercase;
  }
  .zoom {
    display: flex;
    align-items: center;
    gap: 10px;
  }
  .zoom .label {
    min-width: 4ch;
    text-align: center;
  }
  .zoom button {
    width: 28px;
    height: 28px;
    border: 1px solid var(--line);
    border-radius: 3px;
    color: var(--text);
    background: var(--bg);
    font-family: var(--mono);
    font-size: var(--fs-ui);
    cursor: pointer;
  }
  .zoom button:hover:not(:disabled) {
    border-color: var(--accent-border);
    color: var(--accent);
  }
  .zoom button:disabled {
    opacity: 0.4;
    cursor: default;
  }
  .pages {
    position: relative;
    display: grid;
    align-content: start;
    gap: 12px;
    overflow: auto;
    padding: 12px 0;
  }
  .note {
    margin: 0;
    padding: 0 14px;
    color: var(--muted);
    font-size: var(--fs-ui);
  }
  .page {
    position: relative;
    justify-self: center;
    background: #fff;
  }
  canvas {
    display: block;
    width: 100%;
    height: 100%;
  }
  .box {
    position: absolute;
    padding: 0;
    border: 1px solid var(--accent-border);
    border-radius: 2px;
    background: none;
    cursor: pointer;
  }
  .box.flagged {
    border: 1px dashed var(--bg);
  }
  .box.checked {
    border-color: transparent;
  }
  .box:hover,
  .box.hovered {
    border-color: var(--accent);
  }
  /* A board's labels are many and small: each shows only when pointed at,
     or as the part at hand. */
  .box.board {
    border-color: transparent;
  }
  .box.board:hover,
  .box.board.hovered {
    border-color: var(--accent);
    background: var(--accent-soft);
  }
  .box.selected {
    border: 2px solid var(--accent);
    background: var(--accent-soft);
    box-shadow: 0 0 0 9999px rgb(0 0 0 / 0.12);
  }
  button:focus-visible {
    outline: 2px solid var(--accent);
    outline-offset: 2px;
  }
</style>

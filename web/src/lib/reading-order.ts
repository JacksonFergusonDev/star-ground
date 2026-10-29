// The order a builder reads a parts list in: down each column, then the next
// column to the right. Celestial Drive prints resistors, capacitors, chips and
// pots as four columns side by side; sorting by line would read across them.

import type { SourceLocation } from './api';

// Designators starting within this many points share a column (OCR's boxes
// jitter by a point or two).
const COLUMN_PT = 6;
// A gap this many times a page's usual line spacing ends a column: another
// table starts below, whose columns may line up with this one's.
const BREAK_LINES = 3;
// The line spacing assumed on a page with too few lines to measure it.
const DEFAULT_LINE_PT = 15;

interface Located<T> {
  item: T;
  at: SourceLocation;
}

function median(values: number[]): number | null {
  if (!values.length) return null;
  const sorted = [...values].sort((a, b) => a - b);
  return sorted[Math.floor(sorted.length / 2)] ?? null;
}

/** Splits a page's parts into columns, each top to bottom. */
function columns<T>(parts: Located<T>[]): Located<T>[][] {
  const found: Located<T>[][] = [];
  for (const part of [...parts].sort((a, b) => a.at.x0 - b.at.x0)) {
    const last = found.at(-1);
    const start = last?.[0]?.at.x0;
    if (last && start !== undefined && part.at.x0 - start <= COLUMN_PT) last.push(part);
    else found.push([part]);
  }
  return found.map((column) => column.sort((a, b) => a.at.top - b.at.top));
}

/** One page's parts, down each column, block by block. */
function pageOrder<T>(parts: Located<T>[]): T[] {
  const cols = columns(parts);
  const steps = cols.flatMap((c) =>
    c.slice(1).map((p, i) => p.at.top - (c[i] as Located<T>).at.top),
  );
  const line = median(steps.filter((s) => s > 1)) ?? DEFAULT_LINE_PT;

  // Each column's runs of closely spaced rows.
  const runs: Located<T>[][] = [];
  for (const column of cols) {
    let run: Located<T>[] = [];
    for (const part of column) {
      const previous = run.at(-1);
      if (previous && part.at.top - previous.at.top > BREAK_LINES * line) {
        runs.push(run);
        run = [];
      }
      run.push(part);
    }
    if (run.length) runs.push(run);
  }

  // Runs that share rows form one block, read left to right.
  const span = (run: Located<T>[]): [number, number] => [
    (run[0] as Located<T>).at.top,
    (run.at(-1) as Located<T>).at.bottom,
  ];
  runs.sort((a, b) => span(a)[0] - span(b)[0]);
  const blocks: { bottom: number; runs: Located<T>[][] }[] = [];
  for (const run of runs) {
    const [top, bottom] = span(run);
    const block = blocks.at(-1);
    if (block && top <= block.bottom) {
      block.runs.push(run);
      block.bottom = Math.max(block.bottom, bottom);
    } else {
      blocks.push({ bottom, runs: [run] });
    }
  }
  return blocks.flatMap((block) =>
    block.runs
      .sort((a, b) => (a[0] as Located<T>).at.x0 - (b[0] as Located<T>).at.x0)
      .flatMap((run) => run.map((p) => p.item)),
  );
}

/**
 * Orders parts as their document lists them: page by page, down each column.
 * Parts with no location come last, in the order given.
 */
export function readingOrder<T extends { location?: SourceLocation | null }>(parts: T[]): T[] {
  const pages = new Map<number, Located<T>[]>();
  const unplaced: T[] = [];
  for (const item of parts) {
    if (!item.location) {
      unplaced.push(item);
      continue;
    }
    const page = pages.get(item.location.page) ?? [];
    page.push({ item, at: item.location });
    pages.set(item.location.page, page);
  }
  return [...pages.keys()]
    .sort((a, b) => a - b)
    .flatMap((page) => pageOrder(pages.get(page) ?? []))
    .concat(unplaced);
}

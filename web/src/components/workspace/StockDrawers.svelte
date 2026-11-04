<script lang="ts">
  // The builder's stock as a parts cabinet: a drawer for each part, labelled
  // as builders label their bins, with how many are in it, grouped by
  // category. Read from the stock CSV in the browser; the server still does
  // the subtracting. With no stock yet, the cabinet stands empty.
  import type { Notation } from '../../lib/parts';
  import { formatValue, kindOfCategory } from '../../lib/parts';
  import type { Stock } from '../../lib/server';
  import PartSymbol from './PartSymbol.svelte';

  let { stock, notation }: { stock: Stock | null; notation: Notation } = $props();

  type Drawer = { part: string; qty: string };

  // Enough drawers to see what's in stock; the CSV can hold more.
  const MOST = 240;

  /** Splits a CSV or TSV line, minding quoted fields. */
  function cells(line: string, tab: boolean): string[] {
    if (tab) return line.split('\t').map((c) => c.trim());
    const out: string[] = [];
    let cell = '';
    let quoted = false;
    for (const ch of line) {
      if (ch === '"') quoted = !quoted;
      else if (ch === ',' && !quoted) {
        out.push(cell.trim());
        cell = '';
      } else cell += ch;
    }
    out.push(cell.trim());
    return out;
  }

  const cabinet = $derived.by(() => {
    if (!stock) return null;
    const lines = stock.text.split(/\r?\n/).filter((l) => l.trim());
    const tab = (lines[0] ?? '').includes('\t');
    const head = cells(lines[0] ?? '', tab).map((h) => h.toLowerCase());
    const col = (name: string) => head.indexOf(name);
    const [category, part, qty] = [col('category'), col('part'), col('qty')];
    if (part < 0) return null;
    // eslint-disable-next-line svelte/prefer-svelte-reactivity -- rebuilt by each run
    const groups = new Map<string, Drawer[]>();
    let shown = 0;
    let total = 0;
    for (const line of lines.slice(1)) {
      const row = cells(line, tab);
      const name = row[part];
      if (!name) continue;
      total += 1;
      if (shown >= MOST) continue;
      const group = (category >= 0 ? row[category] : '') || 'Other parts';
      groups.set(group, [...(groups.get(group) ?? []), { part: name, qty: row[qty] ?? '' }]);
      shown += 1;
    }
    return { groups: [...groups], shown, total };
  });
</script>

{#if cabinet}
  <section class="cabinet" aria-labelledby="cabinet-title">
    <h2 id="cabinet-title">In your drawers</h2>
    {#if cabinet.shown < cabinet.total}
      <p class="note">The first {cabinet.shown} of {cabinet.total} parts.</p>
    {/if}
    {#each cabinet.groups as [group, drawers] (group)}
      <h3><PartSymbol kind={kindOfCategory(group)} size={21} />{group}</h3>
      <ul class="drawers">
        {#each drawers as drawer, i (i)}
          <li class="drawer">
            <span class="label">{formatValue(drawer.part, group, notation)}</span>
            <span class="qty">{drawer.qty ? `×${drawer.qty}` : ''}</span>
            <span class="handle" aria-hidden="true"></span>
          </li>
        {/each}
      </ul>
    {/each}
  </section>
{:else if !stock}
  <div class="bare" aria-hidden="true">
    {#each { length: 12 }, i (i)}
      <span class="drawer empty"><span class="handle"></span></span>
    {/each}
  </div>
{/if}

<style>
  .cabinet {
    display: grid;
    gap: 12px;
  }
  h2 {
    max-width: none;
    margin: 12px 0 0;
    font-size: var(--fs-h4);
    font-weight: 500;
    letter-spacing: 0;
  }
  h3 {
    display: inline-flex;
    gap: 8px;
    align-items: center;
    margin: 8px 0 0;
    color: var(--muted);
    font-family: var(--mono);
    font-size: var(--fs-label);
    font-weight: 400;
    letter-spacing: 0.08em;
    text-transform: uppercase;
  }
  .note {
    margin: 0;
    color: var(--muted);
    font-size: var(--fs-ui);
  }
  /* A wrapping row, not an auto-fill grid, so a phone's narrow column holds it. */
  .drawers,
  .bare {
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
    margin: 0;
    padding: 0;
    list-style: none;
  }
  .bare {
    max-width: 760px;
  }
  .drawer {
    flex: 0 1 132px;
    min-width: 0;
    display: grid;
    grid-template-columns: minmax(0, 1fr) auto;
    gap: 2px 8px;
    align-items: baseline;
    padding: 8px 10px 6px;
    border: 1px solid var(--line);
    border-radius: 3px;
    background: var(--panel);
  }
  .drawer.empty {
    height: 44px;
    border-style: dashed;
    background: none;
  }
  /* The bin's paper label. */
  .label {
    overflow: hidden;
    padding: 1px 5px;
    border-radius: 2px;
    color: var(--text);
    background: color-mix(in srgb, var(--text) 9%, transparent);
    font-family: var(--mono);
    font-size: var(--fs-label);
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .qty {
    color: var(--muted);
    font-family: var(--mono);
    font-size: var(--fs-label);
    font-variant-numeric: tabular-nums;
  }
  .handle {
    grid-column: 1 / -1;
    justify-self: center;
    width: 26px;
    height: 4px;
    margin-top: 6px;
    border: 1px solid var(--line);
    border-radius: 3px;
  }
  .empty .handle {
    display: block;
    margin-top: 28px;
  }
</style>

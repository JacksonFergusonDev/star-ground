<script lang="ts">
  // The builder's stock as a parts cabinet: a drawer for each part, labelled
  // as builders label their bins, with how many are in it, grouped by
  // category. Read from the stock CSV in the browser; the server still does
  // the subtracting. With no stock yet, the cabinet stands empty.
  import type { Notation } from '../../lib/parts';
  import { formatValue, kindOfCategory } from '../../lib/parts';
  import type { Stock } from '../../lib/server';
  import { readStock } from '../../lib/stock';
  import PartSymbol from './PartSymbol.svelte';

  let { stock, notation }: { stock: Stock | null; notation: Notation } = $props();

  type Drawer = { part: string; qty: number };

  // Enough drawers to see what's in stock; the CSV can hold more.
  const MOST = 240;

  const cabinet = $derived.by(() => {
    if (!stock) return null;
    const rows = readStock(stock.text);
    if (!rows.length) return null;
    // eslint-disable-next-line svelte/prefer-svelte-reactivity -- rebuilt by each run
    const groups = new Map<string, Drawer[]>();
    for (const row of rows.slice(0, MOST)) {
      const group = row.category || 'Other parts';
      groups.set(group, [...(groups.get(group) ?? []), { part: row.part, qty: row.qty }]);
    }
    return { groups: [...groups], shown: Math.min(rows.length, MOST), total: rows.length };
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

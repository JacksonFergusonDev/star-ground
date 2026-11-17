<script lang="ts">
  // What a builder already has of this order's parts, for a builder who
  // doesn't want to count every drawer: each part the projects need, and
  // whether they have none, some or all of it. The answers go into the stock,
  // so the shopping list buys only the rest.
  import type { MasterListResult } from '../../lib/api';
  import type { Notation } from '../../lib/parts';
  import { formatValue, kindOfCategory } from '../../lib/parts';
  import { views } from '../../lib/router.svelte';
  import type { Stock } from '../../lib/server';
  import { have, readStock, setHave, withRows } from '../../lib/stock';
  import EmptyState from './EmptyState.svelte';
  import Icon from './Icon.svelte';
  import PartSymbol from './PartSymbol.svelte';
  import SignalPulse from './SignalPulse.svelte';

  let {
    result,
    updating,
    stock,
    notation,
    onChange,
  }: {
    result: MasterListResult | null;
    updating: boolean;
    stock: Stock | null;
    notation: Notation;
    onChange: (stock: Stock | null) => void;
  } = $props();

  const rows = $derived(stock ? readStock(stock.text) : []);

  // The parts to answer for: what the builds use, less what comes with a PCB.
  const parts = $derived(
    (result?.shopping_list ?? [])
      .map((r) => ({ ...r, need: r.bom_qty - r.included }))
      .filter((r) => r.need > 0 && r.category !== 'PCB'),
  );
  const toBuy = $derived(result?.shopping_list.filter((r) => r.buy_qty > 0).length ?? 0);

  type Part = (typeof parts)[number];
  const set = (row: Part, qty: number): void =>
    onChange(withRows(stock, setHave(rows, row.category, row.part, qty)));
</script>

{#if !parts.length}
  <EmptyState title="Nothing to answer for yet">
    Add a project, and the parts it needs appear here to tick off against what you have.
    {#snippet action()}
      <a class="hs-button" href={views.projects.path}
        >Add a project <Icon name="arrow-right" size={14} /></a
      >
    {/snippet}
  </EmptyState>
{:else}
  <div class="have">
    <p class="summary" aria-live="polite">
      {#if updating}<SignalPulse label="Updating…" />{:else}
        Buying {toBuy} of {parts.length} parts.
        <a href={views.list.path}>See the shopping list</a>
      {/if}
    </p>
    <p class="note">
      For each part, say whether you have none, some or all the builds need. <strong>All</strong>
      means you have at least that many; Star Ground then buys none.
    </p>
    <ul class="parts">
      {#each parts as row (row.category + row.part)}
        {@const n = have(rows, row.category, row.part)}
        {@const answer = n <= 0 ? 'none' : n >= row.need ? 'all' : 'some'}
        {@const key = `${row.category}-${row.part}`}
        <li class:answered={n > 0}>
          <span class="part">
            <PartSymbol kind={kindOfCategory(row.category)} />
            <span class="value">{formatValue(row.part, row.category, notation)}</span>
            <span class="need">needs {row.need}</span>
          </span>
          <fieldset>
            <legend class="hs-visually-hidden">
              How many {row.part} ({row.category}) you have
            </legend>
            <label>
              <input
                type="radio"
                name={key}
                checked={answer === 'none'}
                onchange={() => set(row, 0)}
              />
              <span>None</span>
            </label>
            {#if row.need > 1}
              <label>
                <input
                  type="radio"
                  name={key}
                  checked={answer === 'some'}
                  onchange={() => set(row, Math.max(1, Math.min(n, row.need - 1)))}
                />
                <span>Some</span>
              </label>
            {/if}
            <label>
              <input
                type="radio"
                name={key}
                checked={answer === 'all'}
                onchange={() => set(row, Math.max(n, row.need))}
              />
              <span>All {row.need}</span>
            </label>
            {#if answer === 'some'}
              <input
                class="qty"
                type="number"
                min="0"
                max="100000"
                value={n}
                aria-label={`How many ${row.part} you have`}
                onchange={(e) =>
                  set(row, Math.max(0, Math.floor(e.currentTarget.valueAsNumber) || 0))}
              />
            {/if}
          </fieldset>
        </li>
      {/each}
    </ul>
  </div>
{/if}

<style>
  .have {
    display: grid;
    grid-template-columns: minmax(0, 1fr);
    gap: 16px;
    max-width: 860px;
  }
  .summary {
    display: flex;
    gap: 12px;
    align-items: center;
    min-height: 1.6em;
    margin: 0;
    font-size: var(--fs-ui);
  }
  .summary a {
    text-decoration: underline;
    text-underline-offset: 3px;
  }
  .note {
    max-width: 72ch;
    margin: 0;
    color: var(--muted);
    font-size: var(--fs-ui);
    line-height: 1.6;
  }
  .note strong {
    color: var(--text);
    font-weight: 500;
  }
  .parts {
    display: grid;
    gap: 6px;
    margin: 0;
    padding: 0;
    list-style: none;
  }
  li {
    display: flex;
    flex-wrap: wrap;
    gap: 8px 16px;
    align-items: center;
    justify-content: space-between;
    padding: 8px 12px;
    border: 1px solid var(--line);
    border-radius: 5px;
    background: var(--panel);
  }
  li.answered {
    border-color: var(--accent-border);
  }
  .part {
    display: inline-flex;
    gap: 10px;
    align-items: center;
  }
  .value {
    font-family: var(--mono);
    font-size: var(--fs-ui);
  }
  .need {
    color: var(--muted);
    font-size: var(--fs-ui);
  }
  fieldset {
    display: inline-flex;
    flex-wrap: wrap;
    gap: 4px;
    align-items: center;
    margin: 0;
    padding: 0;
    border: 0;
  }
  fieldset label {
    position: relative;
    cursor: pointer;
  }
  fieldset label input {
    position: absolute;
    opacity: 0;
    pointer-events: none;
  }
  fieldset label span {
    display: inline-block;
    padding: 5px 10px;
    border: 1px solid var(--line);
    border-radius: 5px;
    color: var(--muted);
    font-family: var(--mono);
    font-size: var(--fs-label);
    letter-spacing: 0.06em;
    text-transform: uppercase;
  }
  fieldset label:has(input:checked) span {
    border-color: var(--accent-border);
    color: var(--text);
    background: var(--accent-soft);
  }
  fieldset label:has(input:focus-visible) span {
    outline: 2px solid var(--accent);
    outline-offset: 2px;
  }
  .qty {
    width: 6em;
    padding: 5px 8px;
    border: 1px solid var(--line);
    border-radius: 5px;
    color: var(--text);
    background: var(--bg);
    font-family: var(--mono);
    font-size: var(--fs-ui);
  }
</style>

<script lang="ts">
  // Counting the parts a builder owns, for a builder with no stock CSV: a
  // drawer of common values for each kind of part, a count to type in each,
  // and anything else added by name. Every count changes the stock at once,
  // so the shopping list follows, and the stock downloads as the CSV a later
  // visit can upload.
  import type { ComponentCategory } from '../../lib/api';
  import type { BandCount, Notation } from '../../lib/parts';
  import { formatValue, kindOfCategory } from '../../lib/parts';
  import type { Stock } from '../../lib/server';
  import type { StockRow } from '../../lib/stock';
  import {
    CATEGORIES,
    addHave,
    have,
    readStock,
    samePart,
    setHave,
    withRows,
    writeStock,
  } from '../../lib/stock';
  import { ROUGH_COUNTS, VALUE_GROUPS } from '../../lib/stock-values';
  import { views } from '../../lib/router.svelte';
  import CapacitorCode from './CapacitorCode.svelte';
  import Icon from './Icon.svelte';
  import PartSymbol from './PartSymbol.svelte';
  import ResistorBands from './ResistorBands.svelte';
  import ResistorCalculator from './ResistorCalculator.svelte';

  let {
    stock,
    notation,
    bands = $bindable(),
    onChange,
  }: {
    stock: Stock | null;
    notation: Notation;
    bands: BandCount;
    onChange: (stock: Stock | null) => void;
  } = $props();

  const rows = $derived(stock ? readStock(stock.text) : []);
  const total = $derived(rows.reduce((sum, r) => sum + r.qty, 0));

  // Parts the drawers don't hold, listed below them to change or remove.
  const others = $derived(
    rows.filter(
      (r) =>
        !VALUE_GROUPS.some(
          (g) => g.category === r.category && g.values.some((v) => samePart(r.category, r.part, v)),
        ),
    ),
  );

  const write = (next: StockRow[]): void => onChange(withRows(stock, next));
  const set = (category: string, part: string, qty: number): void =>
    write(setHave(rows, category, part, qty));
  const add = (category: string, part: string, qty: number): void =>
    write(addHave(rows, category, part, qty));

  /** A field's count as a whole number, 0 when it's empty or not a number. */
  const counted = (field: HTMLInputElement): number =>
    Math.max(0, Math.floor(field.valueAsNumber) || 0);

  function download(): void {
    const name = stock?.name.toLowerCase().endsWith('.csv') ? stock.name : 'stock.csv';
    const url = URL.createObjectURL(new Blob([writeStock(rows)], { type: 'text/csv' }));
    const link = document.createElement('a');
    link.href = url;
    link.download = name;
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  // Adding a part the drawers don't list.
  let category = $state<ComponentCategory>('Hardware/Misc');
  let part = $state('');
  let qty = $state(1);
</script>

<div class="count">
  <div class="summary">
    <p role="status">
      {#if rows.length}
        {rows.length} different {rows.length === 1 ? 'part' : 'parts'}, {total} in all. The shopping list
        subtracts them.
      {:else}
        Nothing counted yet. Type how many you have under each value; leave the rest empty.
      {/if}
    </p>
    <button class="hs-button" type="button" disabled={!rows.length} onclick={download}>
      Download the stock CSV
    </button>
  </div>
  <p class="note">
    A rough count is fine: the field offers {ROUGH_COUNTS.slice(0, -1).join(', ')} and {ROUGH_COUNTS.at(
      -1,
    )}. It only changes how many spares Star Ground buys. Your counts stay in this browser; download
    the CSV to keep them or use them on another device.
  </p>
  <datalist id="rough-counts">
    {#each ROUGH_COUNTS as n (n)}<option value={n}></option>{/each}
  </datalist>

  <details class="helper">
    <summary>Read a resistor's colour bands</summary>
    <ResistorCalculator name="count" bind:count={bands} onAdd={(v, n) => add('Resistors', v, n)} />
  </details>
  <details class="helper">
    <summary>Read a capacitor's code</summary>
    <CapacitorCode id="count-cap" onAdd={(v, n) => add('Capacitors', v, n)} />
  </details>
  <p class="note">
    <a class="text-link" href={views.identify.path}>
      How to identify every kind of part <Icon name="arrow-right" size={14} />
    </a>
  </p>

  {#each VALUE_GROUPS as group (group.title)}
    {@const slug = group.title.toLowerCase().replaceAll(' ', '-')}
    <section aria-labelledby={`group-${slug}`}>
      <h2 id={`group-${slug}`}>
        <PartSymbol kind={kindOfCategory(group.category)} size={21} />{group.title}
      </h2>
      <p class="note">{group.hint}</p>
      <ul class="drawers">
        {#each group.values as value (value)}
          {@const n = have(rows, group.category, value)}
          <li class:stocked={n > 0}>
            <label>
              <span class="value">{formatValue(value, group.category, notation)}</span>
              {#if group.category === 'Resistors'}
                <span class="bands"><ResistorBands {value} count={bands} /></span>
              {/if}
              <input
                type="number"
                min="0"
                max="100000"
                inputmode="numeric"
                list="rough-counts"
                placeholder="0"
                value={n || ''}
                aria-label={`${group.title}: ${value}`}
                onchange={(e) => set(group.category, value, counted(e.currentTarget))}
              />
            </label>
          </li>
        {/each}
      </ul>
    </section>
  {/each}

  <section aria-labelledby="group-other">
    <h2 id="group-other">Other parts</h2>
    <p class="note">
      Anything the drawers above don't list: other values, switches, jacks, LEDs, enclosures.
    </p>
    {#if others.length}
      <ul class="others">
        {#each others as row (row.category + row.part)}
          <li>
            <span class="value">{row.part}</span>
            <span class="category">{row.category || 'No category'}</span>
            <input
              type="number"
              min="0"
              max="100000"
              list="rough-counts"
              value={row.qty}
              aria-label={`How many ${row.part}`}
              onchange={(e) => set(row.category, row.part, counted(e.currentTarget))}
            />
            <button
              class="text-button"
              type="button"
              onclick={() => set(row.category, row.part, 0)}
              aria-label={`Remove ${row.part}`}
            >
              Remove
            </button>
          </li>
        {/each}
      </ul>
    {/if}
    <form
      class="add"
      onsubmit={(e) => {
        e.preventDefault();
        if (!part.trim()) return;
        add(category, part.trim(), qty);
        part = '';
        qty = 1;
      }}
    >
      <label>
        <span>Kind</span>
        <select bind:value={category}>
          {#each CATEGORIES as c (c)}<option value={c}>{c}</option>{/each}
        </select>
      </label>
      <label class="grow">
        <span>Part</span>
        <input bind:value={part} required autocomplete="off" placeholder="3PDT footswitch" />
      </label>
      <label>
        <span>How many</span>
        <input type="number" min="1" max="100000" list="rough-counts" bind:value={qty} required />
      </label>
      <button class="hs-button" type="submit">Add</button>
    </form>
  </section>
</div>

<style>
  .count {
    display: grid;
    grid-template-columns: minmax(0, 1fr);
    gap: 22px;
    max-width: 980px;
  }
  .summary {
    display: flex;
    flex-wrap: wrap;
    gap: 12px 20px;
    align-items: center;
    justify-content: space-between;
  }
  .summary p {
    margin: 0;
    font-size: var(--fs-ui);
  }
  .note {
    max-width: 72ch;
    margin: 0;
    color: var(--muted);
    font-size: var(--fs-ui);
    line-height: 1.6;
  }
  .helper {
    padding: 12px 16px;
    border: 1px solid var(--line);
    border-radius: 5px;
    background: var(--panel);
  }
  .helper summary {
    font-size: var(--fs-ui);
    cursor: pointer;
  }
  .helper[open] summary {
    margin-bottom: 14px;
  }
  section {
    display: grid;
    gap: 10px;
  }
  h2 {
    display: flex;
    gap: 10px;
    align-items: center;
    max-width: none;
    margin: 0;
    font-size: var(--fs-h4);
    font-weight: 500;
    letter-spacing: 0;
  }
  .drawers {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(150px, 1fr));
    gap: 8px;
    margin: 0;
    padding: 0;
    list-style: none;
  }
  .drawers li {
    border: 1px solid var(--line);
    border-radius: 5px;
    background: var(--panel);
  }
  .drawers li.stocked {
    border-color: var(--accent-border);
  }
  .drawers label {
    display: grid;
    grid-template-columns: 1fr auto;
    gap: 6px 8px;
    align-items: center;
    padding: 8px 10px;
  }
  .value {
    font-family: var(--mono);
    font-size: var(--fs-ui);
  }
  .bands {
    justify-self: end;
  }
  .drawers input {
    grid-column: 1 / -1;
  }
  input,
  select {
    width: 100%;
    padding: 6px 8px;
    border: 1px solid var(--line);
    border-radius: 5px;
    color: var(--text);
    background: var(--bg);
    font-family: var(--mono);
    font-size: var(--fs-ui);
  }
  .others {
    display: grid;
    gap: 6px;
    margin: 0;
    padding: 0;
    list-style: none;
  }
  .others li {
    display: grid;
    grid-template-columns: minmax(0, 1fr) auto 7em auto;
    gap: 12px;
    align-items: center;
    padding: 6px 10px;
    border: 1px solid var(--line);
    border-radius: 5px;
    background: var(--panel);
  }
  .category {
    color: var(--muted);
    font-size: var(--fs-ui);
  }
  .add {
    display: flex;
    flex-wrap: wrap;
    gap: 10px;
    align-items: end;
  }
  .add label {
    display: grid;
    gap: 6px;
  }
  .add .grow {
    flex: 1 1 14em;
  }
  .add label span {
    color: var(--muted);
    font-family: var(--mono);
    font-size: var(--fs-label);
    letter-spacing: 0.08em;
    text-transform: uppercase;
  }
  .add input[type='number'] {
    width: 7em;
  }
  @media (width <= 600px) {
    .others li {
      grid-template-columns: minmax(0, 1fr) 6em auto;
    }
    .others .category {
      display: none;
    }
  }
</style>

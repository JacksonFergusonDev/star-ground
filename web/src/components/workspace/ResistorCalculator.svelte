<script lang="ts">
  // The resistor colour code as a table to pick from: a column for each band,
  // a row for each colour, and in each cell what that colour means in that
  // band. Picking a colour in each column reads the resistor, so the table is
  // both the calculator and the full code to look things up in.
  import type { Snippet } from 'svelte';
  import type { Band, BandCount } from '../../lib/parts';
  import { DIGITS } from '../../lib/parts';
  import { COLOUR_NAMES, COLOURS, MULTIPLIERS, TOLERANCES, readBands } from '../../lib/identify';
  import Choice from './Choice.svelte';
  import Icon from './Icon.svelte';
  import ResistorBands from './ResistorBands.svelte';

  let {
    name,
    count = $bindable(5),
    onAdd,
    stockHref,
    intro,
  }: {
    /** Names the radio groups, unique on the page. */
    name: string;
    count?: BandCount;
    /** Adds the resistor read to the stock; none on a page without one. */
    onAdd?: (value: string, qty: number) => void;
    /** Where the stock is, linked beside the add button. */
    stockHref?: string;
    /** The heading and words above the bands, beside the reading when there's room. */
    intro?: Snippet;
  } = $props();

  type Column = { key: string; title: string; colours: Band[] };
  const columns = $derived<Column[]>([
    { key: 'd1', title: '1st digit', colours: DIGITS },
    { key: 'd2', title: '2nd digit', colours: DIGITS },
    ...(count === 5 ? [{ key: 'd3', title: '3rd digit', colours: DIGITS }] : []),
    {
      key: 'm',
      title: 'Multiplier',
      colours: COLOURS.filter((c) => MULTIPLIERS[c] !== undefined),
    },
    {
      key: 't',
      title: 'Tolerance',
      colours: COLOURS.filter((c) => TOLERANCES[c] !== undefined),
    },
  ]);

  // The calculator starts on a 10k resistor, the value pedals use most, as
  // that many bands show it: brown, black, orange, gold on four.
  const tenK = (bands: BandCount): Record<string, Band> => ({
    d1: 'brown',
    d2: 'black',
    d3: 'black',
    m: bands === 4 ? 'orange' : 'red',
    t: bands === 4 ? 'gold' : 'brown',
  });
  let picked = $state<Record<string, Band>>(tenK(count));
  function recount(next: BandCount): void {
    count = next;
    picked = tenK(next);
  }

  const reading = $derived(
    readBands({
      digits: [picked.d1, picked.d2, ...(count === 5 ? [picked.d3] : [])] as Band[],
      multiplier: picked.m as Band,
      tolerance: picked.t as Band,
    }),
  );

  /** What a colour means in a column: '4', '×1k', '±1%'. */
  function meaning(column: string, colour: Band): string {
    if (column === 'm') {
      const power = MULTIPLIERS[colour] ?? 0;
      return `×${power < 0 ? 10 ** power : ohmsShort(10 ** power)}`;
    }
    if (column === 't') return `±${TOLERANCES[colour]}%`;
    return String(DIGITS.indexOf(colour));
  }
  const ohmsShort = (n: number): string =>
    n >= 1e9 ? `${n / 1e9}G` : n >= 1e6 ? `${n / 1e6}M` : n >= 1e3 ? `${n / 1e3}k` : String(n);

  let qty = $state(1);
</script>

<div class="calculator">
  <div class="top">
    <div class="lead">
      {@render intro?.()}
      <Choice
        legend="Bands"
        name={`${name}-count`}
        options={[
          { value: 4, label: '4' },
          { value: 5, label: '5' },
        ]}
        bind:value={() => count, recount}
      />
    </div>
    <p class="read" aria-live="polite">
      <ResistorBands value={reading.value} {count} large />
      <span class="value"
        ><strong>{reading.value} Ω</strong> <span>±{reading.tolerance}%</span></span
      >
    </p>
  </div>

  <div class="table">
    <table>
      <caption class="hs-visually-hidden">Resistor colour code</caption>
      <thead>
        <tr>
          <th scope="col">Colour</th>
          {#each columns as column (column.key)}
            <th scope="col">{column.title}</th>
          {/each}
        </tr>
      </thead>
      <tbody>
        {#each COLOURS as colour (colour)}
          <tr>
            <th scope="row">
              <span class="colour"
                ><span class="swatch" data-colour={colour}></span>{COLOUR_NAMES[colour]}</span
              >
            </th>
            {#each columns as column (column.key)}
              <td>
                {#if column.colours.includes(colour)}
                  <label class="pick">
                    <input
                      type="radio"
                      name={`${name}-${column.key}`}
                      value={colour}
                      bind:group={picked[column.key]}
                    />
                    <span class="hs-visually-hidden">{column.title}: {COLOUR_NAMES[colour]},</span>
                    <span>{meaning(column.key, colour)}</span>
                  </label>
                {/if}
              </td>
            {/each}
          </tr>
        {/each}
      </tbody>
    </table>
  </div>
  <p class="note">
    Read from the end away from the gold, silver or brown band: the tolerance band sits a little
    apart from the others. A resistor with no tolerance band is ±20%.
  </p>

  {#if onAdd}
    <form
      class="add"
      onsubmit={(e) => {
        e.preventDefault();
        onAdd(reading.value, qty);
      }}
    >
      <label>
        <span>How many</span>
        <input type="number" min="1" max="10000" bind:value={qty} required />
      </label>
      <span class="actions">
        <button class="hs-button" type="submit">Add {reading.value} to your stock</button>
        {#if stockHref}
          <a class="text-link" href={stockHref}
            >See your stock <Icon name="arrow-right" size={14} /></a
          >
        {/if}
      </span>
    </form>
  {/if}
</div>

<style>
  .calculator {
    display: grid;
    gap: 14px;
    min-width: 0;
  }
  .top {
    display: flex;
    flex-wrap: wrap;
    gap: 12px 24px;
    align-items: center;
    justify-content: space-between;
  }
  /* The heading, words and band count in one column, the reading in another,
     each centred in the row; the reading moves below when there isn't room. */
  .lead {
    display: grid;
    flex: 1 1 260px;
    gap: 14px;
    min-width: 0;
  }
  /* The resistor sits above its value, both held to the right edge, so a
     longer value doesn't move the drawing. */
  .read {
    display: grid;
    gap: 6px;
    justify-items: end;
    margin: 0 0 0 auto;
    font-family: var(--mono);
    font-size: var(--fs-ui);
  }
  .read strong {
    color: var(--text);
    font-size: var(--fs-h4);
    font-weight: 500;
  }
  .value {
    display: inline-flex;
    gap: 10px;
    align-items: baseline;
  }
  .value span {
    color: var(--muted);
  }
  .table {
    overflow-x: auto;
    border: 1px solid var(--line);
    border-radius: 5px;
  }
  table {
    width: 100%;
    border-collapse: collapse;
    font-size: var(--fs-ui);
  }
  th,
  td {
    padding: 3px 6px;
    border-bottom: 1px solid var(--line);
    text-align: left;
    white-space: nowrap;
  }
  tr:last-child > * {
    border-bottom: 0;
  }
  thead th {
    padding: 8px 6px;
    color: var(--muted);
    font-family: var(--mono);
    font-size: var(--fs-label);
    font-weight: 400;
    letter-spacing: 0.08em;
    text-transform: uppercase;
  }
  tbody th {
    color: var(--muted);
    font-weight: 400;
  }
  .colour {
    display: inline-flex;
    gap: 8px;
    align-items: center;
  }
  /* The band colours are the part's data, as on ResistorBands; the name beside each carries it. */
  .swatch {
    flex-shrink: 0;
    width: 14px;
    height: 14px;
    border: 1px solid var(--line);
    border-radius: 3px;
  }
  .swatch[data-colour='black'] {
    background: #111;
  }
  .swatch[data-colour='brown'] {
    background: #7b4a24;
  }
  .swatch[data-colour='red'] {
    background: #d03a2f;
  }
  .swatch[data-colour='orange'] {
    background: #ee8a1f;
  }
  .swatch[data-colour='yellow'] {
    background: #f1d23a;
  }
  .swatch[data-colour='green'] {
    background: #2f9e4f;
  }
  .swatch[data-colour='blue'] {
    background: #2f6fd0;
  }
  .swatch[data-colour='violet'] {
    background: #8a4fc9;
  }
  .swatch[data-colour='grey'] {
    background: #8b8b8b;
  }
  .swatch[data-colour='white'] {
    background: #f4f4f4;
  }
  .swatch[data-colour='gold'] {
    background: #c9a227;
  }
  .swatch[data-colour='silver'] {
    background: #c0c4c8;
  }
  .pick {
    position: relative;
    display: block;
    padding: 4px 8px;
    border: 1px solid transparent;
    border-radius: 4px;
    color: var(--muted);
    font-family: var(--mono);
    cursor: pointer;
  }
  .pick input {
    position: absolute;
    opacity: 0;
    pointer-events: none;
  }
  .pick:has(input:checked) {
    border-color: var(--accent-border);
    color: var(--text);
    background: var(--accent-soft);
  }
  .pick:has(input:focus-visible) {
    outline: 2px solid var(--accent);
    outline-offset: 1px;
  }
  .note {
    margin: 0;
    color: var(--muted);
    font-size: var(--fs-ui);
    line-height: 1.6;
  }
  .add {
    display: flex;
    flex-wrap: wrap;
    gap: 10px;
    align-items: end;
  }
  /* The link's text lines up with the button's. */
  .actions {
    display: inline-flex;
    flex-wrap: wrap;
    gap: 10px 16px;
    align-items: center;
  }
  .add label {
    display: grid;
    gap: 6px;
  }
  .add label span {
    color: var(--muted);
    font-family: var(--mono);
    font-size: var(--fs-label);
    letter-spacing: 0.08em;
    text-transform: uppercase;
  }
  .add input {
    width: 7em;
    padding: 8px 10px;
    border: 1px solid var(--line);
    border-radius: 5px;
    color: var(--text);
    background: var(--bg);
    font-family: var(--mono);
    font-size: var(--fs-ui);
  }
</style>

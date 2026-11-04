<script lang="ts">
  // The master list: what to check, then what to buy across every project,
  // and the files to download.
  import type { MasterListResult } from '../../lib/api';
  import type { Failure } from '../../lib/server';
  import { explain } from '../../lib/server';
  import { formatValue, kindOfCategory, readPot } from '../../lib/parts';
  import type { Display, Options } from '../../lib/workspace.svelte';
  import { count } from '../../lib/workspace.svelte';
  import BugNotice from './BugNotice.svelte';
  import Icon from './Icon.svelte';
  import NotationChoice from './NotationChoice.svelte';
  import PartSymbol from './PartSymbol.svelte';
  import TaperCurve from './TaperCurve.svelte';

  let {
    result,
    updating,
    error,
    options,
    display,
    showHref,
  }: {
    result: MasterListResult | null;
    updating: boolean;
    error: Failure | null;
    options: Options;
    display: Display;
    /** Where a project's part (by its place on the list) shows in its document, if it has one. */
    showHref: (project: number, ref: string) => string | null;
  } = $props();

  const checks = $derived(
    (result?.projects ?? [])
      .map((p, index) => ({
        index,
        label: p.label,
        items: [
          ...p.reviews.flatMap((r) => r.warnings.map((w) => ({ ref: r.ref, message: w.message }))),
          ...p.ocr_reads.map((w) => ({ ref: w.ref, message: w.message })),
        ],
      }))
      .filter((p) => p.items.length),
  );
  const choices = $derived((result?.projects ?? []).filter((p) => p.options.length));
  const unread = $derived((result?.projects ?? []).filter((p) => p.errors.length));
  const rows = $derived(result?.shopping_list ?? []);
  const toBuy = $derived(rows.filter((r) => r.buy_qty > 0).length);

  // A note longer than this folds to one line, the rest a click away.
  const LONG_NOTE = 60;
</script>

<div class="master" aria-busy={updating}>
  <p class="state" aria-live="polite">{updating ? 'Updating…' : ''}</p>

  {#if error}
    <p class="problem" role="alert">{error.message}</p>
    {#if error.bugCode}
      <BugNotice code={error.bugCode} document="Building the master list" />
    {/if}
  {/if}

  {#if !result}
    <div class="empty">
      <p>Add a project and its parts appear here, merged with every other project's.</p>
      <a class="hs-button" href="/app/">Add a project <Icon name="arrow-right" size={14} /></a>
    </div>
  {:else}
    <p class="summary">
      {count(result.stats.parts_found, 'part')} read from {count(result.stats.lines_read, 'line')}
      across {count(result.projects.length, 'project')}; {count(rows.length, 'different part')},
      {toBuy} to buy{result.has_stock ? ' after your stock' : ''}.
    </p>

    {#each unread as project (project.label)}
      <h2>Couldn't read {project.label}</h2>
      {#each project.errors as problem, i (i)}
        <p class="note">{explain(problem)}</p>
        {#if problem.bug_code}
          <BugNotice code={problem.bug_code} document={project.label} />
        {/if}
      {/each}
    {/each}

    {#if checks.length}
      <h2 id="check">Check these parts</h2>
      <p class="note">
        Star Ground's self-checks found places where the list may not match the document. Compare
        these against it before ordering.
      </p>
      {#each checks as project (project.label)}
        <h3>{project.label}</h3>
        <ul class="notes">
          {#each project.items as item, i (i)}
            {@const show = showHref(project.index, item.ref)}
            <li>
              <code>{item.ref}</code>
              {item.message}
              {#if show}
                <a class="show" href={show}>
                  Show in the document <Icon name="arrow-right" size={14} />
                </a>
              {/if}
            </li>
          {/each}
        </ul>
      {/each}
    {/if}

    {#if choices.length}
      <h2>Interchangeable parts</h2>
      <p class="note">These documents offer alternatives. The first option listed was used.</p>
      {#each choices as project (project.label)}
        <h3>{project.label}</h3>
        <ul class="notes">
          {#each project.options as text, i (i)}
            <li>{text}</li>
          {/each}
        </ul>
      {/each}
    {/if}

    {#if result.ignored_lines.length}
      <details>
        <summary>
          {result.ignored_lines.length} skipped
          {result.ignored_lines.length === 1 ? 'line' : 'lines'} that looked like parts
        </summary>
        <ul class="notes">
          {#each result.ignored_lines as line, i (i)}
            <li><code>{line}</code></li>
          {/each}
        </ul>
      </details>
    {/if}

    <h2>Parts</h2>
    <div class="options">
      <label><input type="checkbox" bind:checked={options.hardware} /> Hardware kit</label>
      <label><input type="checkbox" bind:checked={options.extras} /> Sockets and adapters</label>
      <span class="spacer"></span>
      <NotationChoice bind:notation={display.notation} />
    </div>
    <div class="table">
      <table>
        <thead>
          <tr>
            <th scope="col">Part</th>
            <th scope="col">Category</th>
            {#if result.has_stock}
              <th scope="col" class="qty">Needed</th>
              <th scope="col" class="qty">In stock</th>
            {/if}
            <th scope="col" class="qty">Buy</th>
            <th scope="col">Notes</th>
            <th scope="col">Buy from</th>
          </tr>
        </thead>
        <tbody>
          {#each rows as row (row.origin + row.category + row.part)}
            {@const taper = row.category === 'Potentiometers' ? readPot(row.part).taper : null}
            <tr class:covered={row.buy_qty === 0}>
              <td class="part">
                <span class="value">
                  {formatValue(row.part, row.category, display.notation)}
                  {#if taper}<TaperCurve {taper} />{/if}
                </span>
              </td>
              <td class="category">
                <span class="kind"
                  ><PartSymbol kind={kindOfCategory(row.category)} />{row.category}</span
                >
              </td>
              {#if result.has_stock}
                <td class="qty">{row.bom_qty}</td>
                <td class="qty">{row.in_stock ?? 0}</td>
              {/if}
              <td class="qty">{row.buy_qty}</td>
              <td class="notes-cell">
                {#if row.notes.length > LONG_NOTE}
                  <details>
                    <summary>{row.notes}</summary>
                  </details>
                {:else}
                  {row.notes}
                {/if}
              </td>
              <td class="link">
                {#if row.supplier_url && row.buy_qty > 0}
                  <a href={row.supplier_url} target="_blank" rel="noopener noreferrer">
                    Tayda <Icon name="arrow-up-right" size={14} />
                  </a>
                {/if}
              </td>
            </tr>
          {/each}
        </tbody>
      </table>
    </div>
  {/if}
</div>

<style>
  .master {
    display: grid;
    align-content: start;
    gap: 14px;
    min-width: 0;
  }
  h2 {
    max-width: none;
    margin: 20px 0 0;
    font-size: var(--fs-h4);
    font-weight: 500;
    letter-spacing: 0;
    scroll-margin-top: calc(var(--hs-bar-height) + 24px);
  }
  h3 {
    margin: 4px 0 0;
    color: var(--muted);
    font-family: var(--mono);
    font-size: var(--fs-label);
    font-weight: 400;
    letter-spacing: 0.08em;
    text-transform: uppercase;
  }
  .state:empty {
    display: none;
  }
  .empty {
    display: grid;
    gap: 14px;
    justify-items: start;
    padding: 28px;
    border: 1px dashed var(--line);
    border-radius: 5px;
  }
  .state,
  .summary,
  .empty p,
  .note,
  .notes,
  details {
    margin: 0;
    color: var(--muted);
    font-size: var(--fs-ui);
    line-height: 1.7;
  }
  .problem {
    margin: 0;
    color: var(--text);
  }
  .notes {
    padding-left: 20px;
  }
  summary {
    cursor: pointer;
  }
  .show {
    display: inline-flex;
    gap: 6px;
    align-items: center;
    margin-left: 6px;
    padding: 0;
    border: 0;
    color: var(--text);
    background: none;
    font: inherit;
    cursor: pointer;
  }
  .show:hover {
    color: var(--accent);
  }
  .empty a:focus-visible,
  .show:focus-visible {
    outline: 2px solid var(--accent);
    outline-offset: 2px;
  }
  .options {
    display: flex;
    flex-wrap: wrap;
    gap: 6px 20px;
    color: var(--muted);
    font-size: var(--fs-ui);
  }
  .spacer {
    flex: 1;
  }
  .options label {
    display: flex;
    gap: 8px;
    align-items: center;
  }
  .covered td {
    color: var(--muted);
  }
  .table {
    overflow-x: auto;
    border: 1px solid var(--line);
    border-radius: 5px;
    background: var(--panel);
  }
  table {
    width: 100%;
    border-collapse: collapse;
    font-size: var(--fs-ui);
  }
  th,
  td {
    padding: 9px 14px;
    border-bottom: 1px solid var(--line);
    text-align: left;
    vertical-align: top;
  }
  th {
    color: var(--muted);
    font-family: var(--mono);
    font-size: var(--fs-label);
    font-weight: 400;
    letter-spacing: 0.08em;
    text-transform: uppercase;
  }
  tbody tr:last-child td {
    border-bottom: 0;
  }
  .part,
  .qty {
    font-family: var(--mono);
    white-space: nowrap;
  }
  .qty {
    text-align: right;
    font-variant-numeric: tabular-nums;
  }
  .value,
  .kind {
    display: inline-flex;
    gap: 8px;
    align-items: center;
  }
  .category {
    white-space: nowrap;
  }
  .kind {
    color: var(--text);
  }
  .kind :global(.symbol) {
    color: var(--muted);
  }
  .notes-cell {
    min-width: 16ch;
    max-width: 40ch;
    color: var(--muted);
  }
  /* A long note shows its first line; opening it shows the rest. */
  .notes-cell details {
    color: inherit;
    font-size: inherit;
  }
  .notes-cell summary {
    display: block;
    cursor: pointer;
  }
  .notes-cell details:not([open]) summary {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .notes-cell summary:hover {
    color: var(--text);
  }
  .notes-cell summary:focus-visible {
    outline: 2px solid var(--accent);
    outline-offset: 2px;
  }
  .link a {
    display: inline-flex;
    gap: 6px;
    align-items: center;
    color: var(--text);
    white-space: nowrap;
  }
  .link a:hover {
    color: var(--accent);
  }
</style>

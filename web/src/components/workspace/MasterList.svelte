<script lang="ts">
  // The master list: what to check, then what to buy across every project,
  // and the files to download.
  import type { ExportKind, MasterListResult } from '../../lib/api';
  import type { Download } from '../../lib/server';
  import type { Options } from '../../lib/workspace.svelte';
  import { count } from '../../lib/workspace.svelte';
  import BugNotice from './BugNotice.svelte';
  import Exports from './Exports.svelte';
  import Icon from './Icon.svelte';

  let {
    result,
    updating,
    error,
    options,
    onExport,
  }: {
    result: MasterListResult | null;
    updating: boolean;
    error: string | null;
    options: Options;
    onExport: (kind: ExportKind, excel: boolean) => Promise<Download>;
  } = $props();

  const checks = $derived(
    (result?.projects ?? [])
      .map((p) => ({
        label: p.label,
        items: [
          ...p.reviews.flatMap((r) => r.warnings.map((w) => ({ ref: r.ref, message: w.message }))),
          ...p.ocr_reads.map((w) => ({ ref: w.ref, message: w.message })),
        ],
      }))
      .filter((p) => p.items.length),
  );
  const choices = $derived((result?.projects ?? []).filter((p) => p.options.length));
  const unread = $derived((result?.projects ?? []).filter((p) => p.errors.length || p.bugs.length));
  const rows = $derived(result?.shopping_list ?? []);
  const toBuy = $derived(rows.filter((r) => r.buy_qty > 0).length);
</script>

<section class="master" aria-labelledby="master-title" aria-busy={updating}>
  <div class="title">
    <h2 id="master-title">Master list</h2>
    <p class="state" aria-live="polite">{updating ? 'Updating…' : ''}</p>
  </div>

  {#if error}
    <p class="problem" role="alert">{error}</p>
  {/if}

  {#if !result}
    <p class="empty">Add a project and its parts appear here, merged with every other project's.</p>
  {:else}
    <p class="summary">
      {count(result.stats.parts_found, 'part')} read from {count(result.stats.lines_read, 'line')}
      across {count(result.projects.length, 'project')}; {count(rows.length, 'different part')},
      {toBuy} to buy{result.has_stock ? ' after your stock' : ''}.
    </p>

    {#each unread as project (project.label)}
      <h3>Couldn't read {project.label}</h3>
      {#each project.errors as message, i (i)}
        <p class="note">{message}</p>
      {/each}
      {#each project.bugs as bug (bug.code)}
        <BugNotice {bug} document={project.label} />
      {/each}
    {/each}

    {#if checks.length}
      <h3>Check these parts</h3>
      <p class="note">
        Star Ground's self-checks found places where the list may not match the document. Compare
        these against it before ordering.
      </p>
      {#each checks as project (project.label)}
        <h4>{project.label}</h4>
        <ul class="notes">
          {#each project.items as item, i (i)}
            <li><code>{item.ref}</code> {item.message}</li>
          {/each}
        </ul>
      {/each}
    {/if}

    {#if choices.length}
      <h3>Interchangeable parts</h3>
      <p class="note">These documents offer alternatives. The first option listed was used.</p>
      {#each choices as project (project.label)}
        <h4>{project.label}</h4>
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

    <h3>Shopping list</h3>
    <div class="options">
      <label><input type="checkbox" bind:checked={options.hardware} /> Hardware kit</label>
      <label><input type="checkbox" bind:checked={options.extras} /> Sockets and adapters</label>
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
            <th scope="col"><span class="visually-hidden">Supplier</span></th>
          </tr>
        </thead>
        <tbody>
          {#each rows as row (row.origin + row.category + row.part)}
            <tr class:covered={row.buy_qty === 0}>
              <td class="part">{row.part}</td>
              <td>{row.category}</td>
              {#if result.has_stock}
                <td class="qty">{row.bom_qty}</td>
                <td class="qty">{row.in_stock ?? 0}</td>
              {/if}
              <td class="qty">{row.buy_qty}</td>
              <td class="notes-cell">{row.notes}</td>
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

    <Exports {onExport} />
  {/if}
</section>

<style>
  .master {
    display: grid;
    align-content: start;
    gap: 14px;
    min-width: 0;
  }
  .title {
    display: flex;
    align-items: baseline;
    justify-content: space-between;
    gap: 12px;
  }
  h2 {
    margin: 0;
    font-size: var(--fs-h3);
    font-weight: 500;
    letter-spacing: -0.8px;
  }
  h3 {
    margin: 20px 0 0;
    font-size: var(--fs-h4);
    font-weight: 500;
  }
  h4 {
    margin: 4px 0 0;
    color: var(--muted);
    font-family: var(--mono);
    font-size: var(--fs-label);
    font-weight: 400;
    letter-spacing: 0.08em;
    text-transform: uppercase;
  }
  .state,
  .summary,
  .empty,
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
  .options {
    display: flex;
    flex-wrap: wrap;
    gap: 6px 20px;
    color: var(--muted);
    font-size: var(--fs-ui);
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
    /* Holds the hidden column heading, so it scrolls with the table. */
    position: relative;
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
  .notes-cell {
    color: var(--muted);
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
  .visually-hidden {
    position: absolute;
    width: 1px;
    height: 1px;
    overflow: hidden;
    clip-path: inset(50%);
    white-space: nowrap;
  }
</style>

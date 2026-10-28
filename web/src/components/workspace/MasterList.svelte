<script lang="ts">
  // The master list: what to check, then what to buy across every project.
  import type { MasterListResult } from '../../lib/api';
  import Icon from './Icon.svelte';

  let {
    result,
    updating,
    error,
  }: { result: MasterListResult | null; updating: boolean; error: string | null } = $props();

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
  const toBuy = $derived(result?.shopping_list.filter((r) => r.buy_qty > 0) ?? []);
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
      {result.stats.parts_found} parts read from {result.projects.length}
      {result.projects.length === 1 ? 'project' : 'projects'}; {toBuy.length} to buy.
    </p>

    {#if checks.length}
      <h3>Check these parts</h3>
      {#each checks as project (project.label)}
        <h4>{project.label}</h4>
        <ul class="checks">
          {#each project.items as item, i (i)}
            <li><code>{item.ref}</code> {item.message}</li>
          {/each}
        </ul>
      {/each}
    {/if}

    <h3>Shopping list</h3>
    <div class="table">
      <table>
        <thead>
          <tr>
            <th scope="col">Part</th>
            <th scope="col">Category</th>
            <th scope="col" class="qty">Buy</th>
            <th scope="col">Notes</th>
            <th scope="col"><span class="visually-hidden">Supplier</span></th>
          </tr>
        </thead>
        <tbody>
          {#each toBuy as row (row.origin + row.category + row.part)}
            <tr>
              <td class="part">{row.part}</td>
              <td>{row.category}</td>
              <td class="qty">{row.buy_qty}</td>
              <td class="notes">{row.notes}</td>
              <td class="link">
                {#if row.supplier_url}
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
  .checks {
    margin: 0;
    color: var(--muted);
    font-size: var(--fs-ui);
    line-height: 1.7;
  }
  .problem {
    margin: 0;
    color: var(--text);
  }
  .checks {
    padding-left: 20px;
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
  .notes {
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

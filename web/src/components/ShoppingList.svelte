<script lang="ts">
  // The walking skeleton's one path through the app: paste a parts list, get
  // the shopping list. Step 5 of PLAN.md grows this into the build workspace.
  import { onMount } from 'svelte';
  import type { MasterListResult } from '../lib/api';
  import { masterList, warmUp } from '../lib/server';

  const example = 'R1 10k\nC1 100n\nU1 TL072';

  let text = $state('');
  let busy = $state(false);
  let error = $state<string | null>(null);
  let result = $state<MasterListResult | null>(null);

  const reviews = $derived(result?.projects.flatMap((p) => p.reviews) ?? []);

  onMount(warmUp);

  async function submit(event: SubmitEvent): Promise<void> {
    event.preventDefault();
    busy = true;
    error = null;
    try {
      result = await masterList({ projects: [{ method: 'paste', name: 'Pasted list', text }] });
    } catch (e) {
      result = null;
      error = (e as Error).message;
    } finally {
      busy = false;
    }
  }
</script>

<form class="paste" onsubmit={submit}>
  <label for="parts">Parts list</label>
  <textarea id="parts" bind:value={text} rows="10" spellcheck="false" placeholder={example} required
  ></textarea>
  <button class="hs-button hs-button--primary" type="submit" disabled={busy}>
    {busy ? 'Reading…' : 'Make the shopping list'}
  </button>
</form>

<div aria-live="polite">
  {#if error}
    <p class="problem" role="alert">{error}</p>
  {/if}
  {#if result}
    {#if reviews.length}
      <h2>Check these parts</h2>
      <ul class="reviews">
        {#each reviews as review (review.ref)}
          {#each review.warnings as warning (warning.message)}
            <li><code>{review.ref}</code> {warning.message}</li>
          {/each}
        {/each}
      </ul>
    {/if}
    <h2>Shopping list</h2>
    <p class="summary">
      {result.stats.parts_found} parts read from {result.stats.lines_read} lines.
    </p>
    <div class="table">
      <table>
        <thead>
          <tr
            ><th scope="col">Part</th><th scope="col">Category</th><th scope="col">Buy</th><th
              scope="col">Notes</th
            ></tr
          >
        </thead>
        <tbody>
          {#each result.shopping_list as row (row.origin + row.category + row.part)}
            <tr>
              <td>{row.part}</td>
              <td>{row.category}</td>
              <td class="qty">{row.buy_qty}</td>
              <td>{row.notes}</td>
            </tr>
          {/each}
        </tbody>
      </table>
    </div>
  {/if}
</div>

<style>
  .paste {
    display: grid;
    gap: 12px;
    max-width: 640px;
    margin-top: 40px;
  }
  label {
    color: var(--muted);
    font-family: var(--mono);
    font-size: var(--fs-label);
    letter-spacing: 0.08em;
    text-transform: uppercase;
  }
  textarea {
    width: 100%;
    padding: 14px 16px;
    border: 1px solid var(--line);
    border-radius: 5px;
    color: var(--text);
    background: var(--panel);
    font-family: var(--mono);
    font-size: var(--fs-ui);
    line-height: 1.6;
    resize: vertical;
  }
  textarea:focus-visible {
    border-color: var(--accent-border);
    outline: 2px solid var(--accent);
    outline-offset: 2px;
  }
  button {
    justify-self: start;
  }
  button:disabled {
    opacity: 0.6;
    cursor: progress;
  }
  h2 {
    margin: 56px 0 16px;
    font-size: var(--fs-h3);
    font-weight: 500;
    letter-spacing: -0.8px;
  }
  .problem,
  .summary,
  .reviews {
    color: var(--muted);
    font-size: var(--fs-body);
    line-height: 1.7;
  }
  .problem {
    margin: 24px 0 0;
    color: var(--text);
  }
  .summary {
    margin: 0 0 20px;
  }
  .reviews {
    margin: 0;
    padding-left: 20px;
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
    padding: 10px 16px;
    border-bottom: 1px solid var(--line);
    text-align: left;
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
  .qty {
    font-family: var(--mono);
    font-variant-numeric: tabular-nums;
  }
</style>

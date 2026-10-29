<script lang="ts">
  // The builder's stock: a CSV the master list subtracts from what to buy.
  import type { Stock } from '../../lib/server';

  let { stock, onChange }: { stock: Stock | null; onChange: (stock: Stock | null) => void } =
    $props();

  let problem = $state<string | null>(null);

  async function pick(event: Event & { currentTarget: HTMLInputElement }): Promise<void> {
    const file = event.currentTarget.files?.[0];
    event.currentTarget.value = '';
    if (!file) return;
    // A stock list is a few hundred rows; anything far larger isn't one.
    if (file.size > 2_000_000) {
      problem = 'That file is too large to be a stock list. Choose the CSV of what you own.';
      return;
    }
    problem = null;
    onChange({ name: file.name, text: await file.text() });
  }
</script>

<section class="stock" aria-labelledby="stock-title">
  <h2 id="stock-title">Your stock</h2>
  {#if stock}
    <p>
      Subtracting <strong>{stock.name}</strong> from what to buy.
      <button class="text-button" type="button" onclick={() => onChange(null)}>Remove</button>
    </p>
  {:else}
    <p>
      Upload a CSV of the parts you own, with columns Category, Part and Qty, to buy only what's
      missing.
    </p>
  {/if}
  <label for="stock-file">{stock ? 'Replace the stock CSV' : 'Stock CSV'}</label>
  <input id="stock-file" type="file" accept=".csv,.tsv,text/csv" onchange={pick} />
  {#if problem}
    <p class="problem" role="alert">{problem}</p>
  {/if}
</section>

<style>
  .stock {
    display: grid;
    gap: 10px;
    padding: 18px 20px;
    border: 1px solid var(--line);
    border-radius: 5px;
  }
  h2 {
    margin: 0;
    font-size: var(--fs-h4);
    font-weight: 500;
  }
  p {
    margin: 0;
    color: var(--muted);
    font-size: var(--fs-ui);
    line-height: 1.6;
    overflow-wrap: anywhere;
  }
  strong {
    color: var(--text);
    font-weight: 500;
  }
  .problem {
    color: var(--text);
  }
  label {
    color: var(--muted);
    font-family: var(--mono);
    font-size: var(--fs-label);
    letter-spacing: 0.08em;
    text-transform: uppercase;
  }
  input {
    color: var(--muted);
    font-size: var(--fs-ui);
  }
  .text-button {
    padding: 0;
    border: 0;
    color: var(--muted);
    background: none;
    font: inherit;
    text-decoration: underline;
    cursor: pointer;
  }
  .text-button:hover {
    color: var(--accent);
  }
  input:focus-visible,
  button:focus-visible {
    outline: 2px solid var(--accent);
    outline-offset: 2px;
  }
</style>

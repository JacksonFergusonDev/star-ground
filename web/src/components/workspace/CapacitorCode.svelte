<script lang="ts">
  // Reads what's printed on a capacitor as it's typed: '104', '2A473J',
  // '4n7', '.1 63V'. Shows the value in the three units a builder meets, and
  // what the letters and numbers around it mean.
  import { capUnits, readCapCode } from '../../lib/identify';

  let {
    id,
    onAdd,
  }: {
    /** The field's id, unique on the page. */
    id: string;
    /** Adds the capacitor read to the stock; none on a page without one. */
    onAdd?: (value: string, qty: number) => void;
  } = $props();

  let printed = $state('104');
  let qty = $state(1);
  const reading = $derived(readCapCode(printed));
</script>

<form
  class="code"
  onsubmit={(e) => {
    e.preventDefault();
    if (reading && onAdd) onAdd(reading.value, qty);
  }}
>
  <label for={id}>What's printed on it</label>
  <input
    {id}
    bind:value={printed}
    autocomplete="off"
    spellcheck="false"
    placeholder="104, 473J, 4n7, 2A104K"
  />
  <div class="out" aria-live="polite">
    {#if reading}
      <p class="value"><strong>{reading.value}</strong> {capUnits(reading.farads)}</p>
      <p class="how">
        Read as {reading.how}.
        {#if reading.tolerance}Tolerance {reading.tolerance}.{/if}
        {#if reading.volts}Rated {reading.volts} V.{/if}
      </p>
    {:else if printed.trim()}
      <p class="how">
        That doesn't read as a capacitance. Type the code as printed, such as 104 or 4n7. An
        electrolytic prints its value plainly, such as 100µF 25V.
      </p>
    {/if}
  </div>
  {#if onAdd}
    <div class="add">
      <label>
        <span>How many</span>
        <input type="number" min="1" max="10000" bind:value={qty} required />
      </label>
      <button class="hs-button" type="submit" disabled={!reading}>
        Add {reading?.value ?? 'it'} to your stock
      </button>
    </div>
  {/if}
</form>

<style>
  .code {
    display: grid;
    gap: 8px;
    min-width: 0;
  }
  label,
  .add label span {
    color: var(--muted);
    font-family: var(--mono);
    font-size: var(--fs-label);
    letter-spacing: 0.08em;
    text-transform: uppercase;
  }
  input {
    width: 100%;
    max-width: 22em;
    padding: 10px 12px;
    border: 1px solid var(--line);
    border-radius: 5px;
    color: var(--text);
    background: var(--bg);
    font-family: var(--mono);
    font-size: var(--fs-ui);
  }
  .out p {
    margin: 0;
  }
  .value {
    font-family: var(--mono);
    font-size: var(--fs-ui);
    color: var(--muted);
  }
  .value strong {
    margin-right: 10px;
    color: var(--text);
    font-size: var(--fs-h4);
    font-weight: 500;
  }
  .out .how {
    margin-top: 4px;
    color: var(--muted);
    font-size: var(--fs-ui);
    line-height: 1.6;
  }
  .add {
    display: flex;
    flex-wrap: wrap;
    gap: 10px;
    align-items: end;
    margin-top: 6px;
  }
  .add label {
    display: grid;
    gap: 6px;
  }
  .add input {
    width: 7em;
    padding: 8px 10px;
  }
</style>

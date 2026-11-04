<script lang="ts">
  // How values are written: '4.7k', or '4k7' as many build documents and
  // European builders write them. Kept with the workspace, so every page
  // follows the builder's choice.
  import type { Notation } from '../../lib/parts';

  let { notation = $bindable() }: { notation: Notation } = $props();

  const choices: { value: Notation; label: string }[] = [
    { value: 'decimal', label: '4.7k' },
    { value: 'rkm', label: '4k7' },
  ];
</script>

<fieldset class="choice">
  <legend>Values</legend>
  {#each choices as choice (choice.value)}
    <label>
      <input type="radio" name="notation" value={choice.value} bind:group={notation} />
      <span>{choice.label}</span>
    </label>
  {/each}
</fieldset>

<style>
  .choice {
    display: inline-flex;
    gap: 4px;
    align-items: center;
    margin: 0;
    padding: 0;
    border: 0;
  }
  legend {
    float: left;
    margin-right: 6px;
    padding: 0;
    color: var(--muted);
    font-family: var(--mono);
    font-size: var(--fs-label);
    letter-spacing: 0.08em;
    text-transform: uppercase;
  }
  label {
    position: relative;
    cursor: pointer;
  }
  input {
    position: absolute;
    opacity: 0;
    inset: 0;
    margin: 0;
    cursor: pointer;
  }
  span {
    display: block;
    padding: 3px 9px;
    border: 1px solid var(--line);
    border-radius: 5px;
    color: var(--muted);
    font-family: var(--mono);
    font-size: var(--fs-label);
  }
  label:hover span {
    color: var(--text);
  }
  input:checked + span {
    border-color: var(--accent-border);
    color: var(--text);
    background: var(--accent-soft);
  }
  input:focus-visible + span {
    outline: 2px solid var(--accent);
    outline-offset: 2px;
  }
</style>

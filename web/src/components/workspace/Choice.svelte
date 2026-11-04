<script lang="ts" generics="T extends string | number">
  // A small choice of how parts are shown, such as '4.7k' or '4k7' values, or
  // four or five resistor bands. Kept with the workspace, so every page
  // follows the builder's choice.
  let {
    legend,
    name,
    options,
    value = $bindable(),
  }: {
    legend: string;
    /** The radio group's name, unique on the page. */
    name: string;
    options: { value: T; label: string }[];
    value: T;
  } = $props();
</script>

<fieldset class="choice">
  <legend>{legend}</legend>
  {#each options as option (option.value)}
    <label>
      <input type="radio" {name} value={option.value} bind:group={value} />
      <span>{option.label}</span>
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

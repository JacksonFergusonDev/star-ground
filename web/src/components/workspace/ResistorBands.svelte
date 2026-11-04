<script lang="ts">
  // A resistor as it looks in the builder's hand: its body and colour bands,
  // read from its value. A 1% metal film resistor (five bands) has a blue
  // body and a 5% carbon film one (four bands) a beige one, as most are sold.
  // The band colours are the part's data, not the site's palette; the value
  // printed beside it carries the meaning, and the label names each band.
  import type { BandCount } from '../../lib/parts';
  import { bands } from '../../lib/parts';

  let { value, count }: { value: string; count: BandCount } = $props();

  const found = $derived(bands(value, count));
  // Digits first, then a gap before the tolerance band, as printed.
  const xs = $derived(count === 5 ? [13, 17, 21, 25, 32] : [14, 19, 24, 32]);
</script>

{#if found}
  <svg
    class="resistor"
    class:film={count === 5}
    viewBox="0 0 44 12"
    width="44"
    height="12"
    role="img"
    aria-label={`Bands: ${found.join(', ')}`}
  >
    <title>{found.join(', ')}</title>
    <path class="lead" d="M0 6h8M36 6h8" />
    <rect class="body" x="8" y="1" width="28" height="10" rx="4" />
    {#each found as band, i (i)}
      <rect class={`band ${band}`} x={xs[i]} y="1" width="2.4" height="10" />
    {/each}
  </svg>
{/if}

<style>
  .resistor {
    flex-shrink: 0;
    vertical-align: middle;
  }
  .lead {
    fill: none;
    stroke: var(--muted);
    stroke-width: 1.2;
  }
  /* Real resistor bodies and band colours (IEC 60062), which no site token covers. */
  .body {
    fill: #d9c29a;
  }
  .film .body {
    fill: #8fb3df;
  }
  .black {
    fill: #1b1b1b;
  }
  .brown {
    fill: #7a4521;
  }
  .red {
    fill: #d23a2c;
  }
  .orange {
    fill: #f08a1c;
  }
  .yellow {
    fill: #f5d13d;
  }
  .green {
    fill: #2f9e4f;
  }
  .blue {
    fill: #2a62c9;
  }
  .violet {
    fill: #8a4dcc;
  }
  .grey {
    fill: #8c8c8c;
  }
  .white {
    fill: #f4f4f4;
  }
  .gold {
    fill: #c8a03a;
  }
  .silver {
    fill: #b9bec4;
  }
</style>

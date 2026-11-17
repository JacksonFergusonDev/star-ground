<script lang="ts">
  // A resistor as it looks in the builder's hand: its body and colour bands,
  // read from its value. The body is one pale neutral, not the blue or beige
  // real ones come in, since those hide the bands nearest their colour; each
  // band has a dark edge so it shows on the body whatever its colour, and is
  // clipped to the body's rounded outline. The band colours are the part's
  // data, not the site's palette; the label names each band.
  import type { BandCount } from '../../lib/parts';
  import { bands } from '../../lib/parts';

  let {
    value,
    count,
    large = false,
  }: {
    value: string;
    count: BandCount;
    /** Drawn twice the size, as the calculator's answer. */
    large?: boolean;
  } = $props();

  const id = $props.id();
  const found = $derived(bands(value, count));
  // Digits first, then a gap before the tolerance band, as printed.
  const xs = $derived(count === 5 ? [16, 21.5, 27, 32.5, 41] : [17, 23, 29, 40]);
</script>

{#if found}
  <svg
    class="resistor"
    viewBox="0 0 60 16"
    width={large ? 120 : 60}
    height={large ? 32 : 16}
    role="img"
    aria-label={`Bands: ${found.join(', ')}`}
  >
    <title>{found.join(', ')}</title>
    <defs>
      <clipPath id={`${id}-body`}>
        <rect x="10" y="1" width="40" height="14" rx="5" />
      </clipPath>
    </defs>
    <path class="lead" d="M0 8h10M50 8h10" />
    <rect class="body" x="10" y="1" width="40" height="14" rx="5" />
    <g clip-path={`url(#${id}-body)`}>
      {#each found as band, i (i)}
        <rect class={`band ${band}`} x={xs[i]} y="0" width="3.4" height="16" />
      {/each}
    </g>
    <rect class="outline" x="10" y="1" width="40" height="14" rx="5" />
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
  /* A pale neutral every band colour stands out on, and the band colours of
     IEC 60062, which no site token covers. */
  .body {
    fill: #d3cfc5;
  }
  .band {
    stroke: rgb(0 0 0 / 0.55);
    stroke-width: 0.5;
  }
  .outline {
    fill: none;
    stroke: rgb(0 0 0 / 0.35);
    stroke-width: 0.6;
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

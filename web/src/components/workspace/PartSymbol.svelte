<script lang="ts">
  // A small drawing of a kind of part, from its schematic symbol: a zigzag for
  // a resistor, two plates for a capacitor. Star Ground's own artwork, like the
  // hero's circuit, drawn in the text's colour. Decoration only: the category
  // or stage beside it carries the meaning.
  import type { PartKind } from '../../lib/parts';

  let { kind, size = 24 }: { kind: PartKind; size?: number } = $props();
</script>

<svg
  class="symbol"
  viewBox="0 0 24 16"
  width={size}
  height={(size * 2) / 3}
  aria-hidden="true"
  focusable="false"
>
  {#if kind === 'resistor'}
    <path d="M0 8h4l2-4 3 8 3-8 3 8 3-8 2 4h4" />
  {:else if kind === 'pot'}
    <path d="M0 7h4l2-3 3 6 3-6 3 6 3-6 2 3h4" />
    <path d="M12 16v-4.5M10.2 13.3 12 11.5l1.8 1.8" />
  {:else if kind === 'capacitor'}
    <path d="M0 8h10.5M10.5 3v10M13.5 3v10M13.5 8H24" />
  {:else if kind === 'electrolytic'}
    <path d="M0 8h10.5M10.5 3v10M15 3q-2.5 5 0 10M12.8 8H24M5.5 3.5h3M7 2v3" />
  {:else if kind === 'diode'}
    <path d="M0 8h8M8 3.5v9l8-4.5zM16 3.5v9M16 8h8" />
  {:else if kind === 'led'}
    <path d="M0 9h7M7 5v8l7-4zM14 5v8M14 9h10M11 3.5l3-3M12 .5h2v2M15.5 4l3-3M16.5 1h2v2" />
  {:else if kind === 'transistor'}
    <circle cx="12.5" cy="8" r="7" />
    <path d="M2 8h8M10 4.5v7M10 6.2 15 3V0M10 9.8l5 3.2V16M12.6 12.7l2.4.3-.9-2.2" />
  {:else if kind === 'chip'}
    <rect x="4" y="3" width="16" height="10" rx="1" />
    <path
      d="M4 6.2a1.8 1.8 0 0 1 0 3.6M7.5 3V.5M12 3V.5M16.5 3V.5M7.5 13v2.5M12 13v2.5M16.5 13v2.5"
    />
  {:else if kind === 'socket'}
    <rect x="4" y="3" width="16" height="10" rx="1" />
    <rect x="7" y="6" width="10" height="4" rx=".5" />
    <path d="M7.5 3V.5M12 3V.5M16.5 3V.5M7.5 13v2.5M12 13v2.5M16.5 13v2.5" />
  {:else if kind === 'switch'}
    <circle cx="6.5" cy="11" r="1.2" />
    <circle cx="17.5" cy="11" r="1.2" />
    <path d="M0 11h5.3M7.4 10.3 17 4M18.7 11H24" />
  {:else if kind === 'crystal'}
    <path d="M0 8h7M7 3.5v9M17 3.5v9M17 8h7" />
    <rect x="9.5" y="3" width="5" height="10" rx=".5" />
  {:else if kind === 'inductor'}
    <path d="M0 11h2a2.5 2.5 0 0 1 5 0 2.5 2.5 0 0 1 5 0 2.5 2.5 0 0 1 5 0 2.5 2.5 0 0 1 5 0h2" />
  {:else if kind === 'transformer'}
    <path d="M8 1.5a2.2 2.2 0 0 1 0 4.3 2.2 2.2 0 0 1 0 4.3 2.2 2.2 0 0 1 0 4.4" />
    <path d="M16 1.5a2.2 2.2 0 0 0 0 4.3 2.2 2.2 0 0 0 0 4.3 2.2 2.2 0 0 0 0 4.4" />
    <path d="M11.2 1v14M12.8 1v14M0 1.5h8M0 14.5h8M16 1.5h8M16 14.5h8" />
  {:else if kind === 'relay'}
    <rect x="1" y="3" width="8" height="10" />
    <path d="M1 13 9 3M9 8h2.5M13.5 8h1.5M15 12l7-6" />
  {:else if kind === 'board'}
    <rect x="1" y="1.5" width="22" height="13" rx="1.5" />
    <path d="M6.3 11H10l3-5h4.7" />
    <circle cx="5" cy="11" r="1.3" />
    <circle cx="19" cy="6" r="1.3" />
  {:else if kind === 'hardware'}
    <path d="M12 1.5 17.6 4.7v6.6L12 14.5l-5.6-3.2V4.7z" />
    <circle cx="12" cy="8" r="2.4" />
  {:else}
    <circle cx="12" cy="8" r="5.5" stroke-dasharray="2 2" />
  {/if}
</svg>

<style>
  .symbol {
    flex-shrink: 0;
    overflow: visible;
    fill: none;
    stroke: currentcolor;
    stroke-linecap: round;
    stroke-linejoin: round;
    stroke-width: 1.3;
    vertical-align: middle;
  }
</style>

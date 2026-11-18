<script lang="ts" module>
  export type ToolKind =
    | 'iron'
    | 'solder'
    | 'multimeter'
    | 'cutters'
    | 'stripper'
    | 'braid'
    | 'holder'
    | 'flux'
    | 'sucker'
    | 'tweezers'
    | 'fuses';
</script>

<script lang="ts">
  // A line drawing of a bench tool, for the first-build tools page. Star
  // Ground's own artwork, like PartSymbol and the hero's circuit, drawn in the
  // muted text colour. Decoration only: the tool's name beside it carries the
  // meaning.
  let { kind }: { kind: ToolKind } = $props();

  // The braid's weave: a zigzag between the tape's two edges, which run from
  // (78, 44)-(78, 52) to (150, 56)-(150, 64).
  const weave = Array.from({ length: 15 }, (_, i) => {
    const x = 78 + i * 5;
    const y = 44 + ((x - 78) * 12) / 72 + (i % 2 ? 8 : 0);
    return `${i ? 'L' : 'M'}${x} ${y.toFixed(1)}`;
  }).join(' ');
</script>

<svg class="drawing" viewBox="0 0 160 96" aria-hidden="true" focusable="false">
  {#if kind === 'iron'}
    <rect x="12" y="38" width="60" height="44" rx="4" />
    <rect x="20" y="46" width="30" height="16" rx="1.5" />
    <text x="35" y="57.5">330°</text>
    <circle cx="61" cy="54" r="6" />
    <path d="M61 48v4M20 72h30" />
    <g transform="rotate(-40 115 52)">
      <rect x="92" y="49" width="36" height="6" rx="3" />
      <path d="M102 49v6M106 49v6M110 49v6" />
      <rect x="128" y="50.5" width="16" height="3" rx="1" />
      <path class="hot" d="M144 51 156 52l-12 1" />
    </g>
    <path d="M97 67c-6 13-17 10-25 5" />
  {:else if kind === 'solder'}
    <circle cx="56" cy="48" r="32" />
    <circle cx="56" cy="48" r="25" />
    <circle cx="56" cy="48" r="20" />
    <circle cx="56" cy="48" r="15" />
    <circle cx="56" cy="48" r="7" />
    <path d="M81 48c0 18 20 24 40 18s25-14 31-8" />
  {:else if kind === 'multimeter'}
    <rect x="52" y="6" width="56" height="84" rx="7" />
    <rect x="60" y="14" width="40" height="20" rx="2" />
    <text x="80" y="28">9.02 V</text>
    <circle cx="80" cy="56" r="13" />
    <path d="M80 56v-10M64 46l3 2M96 46l-3 2M80 39v-2" />
    <circle cx="66" cy="80" r="3" />
    <circle cx="80" cy="80" r="3" />
    <circle cx="94" cy="80" r="3" />
    <g transform="rotate(-10 22 50)">
      <rect x="18" y="34" width="8" height="30" rx="3" />
      <path class="hot" d="M22 34V22" />
    </g>
    <g transform="rotate(10 138 50)">
      <rect x="134" y="34" width="8" height="30" rx="3" />
      <path class="hot" d="M138 34V22" />
    </g>
    <path d="M66 83c-4 13-36 11-41.6-19M94 83c4 13 36 11 41.6-19" />
  {:else if kind === 'cutters'}
    <path d="M28 48c10-10 28-10 40-4M28 48c10 10 28 10 40 4" />
    <path class="hot" d="M28 48h14" />
    <circle cx="72" cy="48" r="5" />
    <path d="M77 45 148 22q7 0 4 7L79 50M79 46l73 21q3 7-4 7L77 51" />
    <path d="M100 38l42-13M100 58l42 13" />
  {:else if kind === 'stripper'}
    <!-- An automatic stripper: a square head with two flat jaws, a
         gripper between them, and a long handle above and below. -->
    <path d="M10 30h30l4-6h28l4 6v34l-6 8H44l-4-6H10V54h32V42H10z" />
    <path class="hot" d="M16 30v12M16 54v12" />
    <path d="M24 42v4h6v-4M26 54l2-3 2 3 2-3 2 3 2-3 2 3" />
    <circle cx="62" cy="47" r="3" />
    <path d="M70 26c22-4 50-13 78-20q8 0 5 7c-26 10-52 22-77 28" />
    <path d="M76 60c24 6 52 12 74 14q7 2 3 8c-24 3-52-2-83-10" />
    <path class="hot" d="M96 74c18 4 36 6 52 6" />
    <path d="M82 40q7 10 1 18" />
  {:else if kind === 'braid'}
    <circle cx="50" cy="48" r="28" />
    <circle cx="50" cy="48" r="20" />
    <circle cx="50" cy="48" r="8" />
    <path d="M78 44 150 56v8L78 52" />
    <path class="hot" d={weave} />
  {:else if kind === 'holder'}
    <rect x="14" y="80" width="132" height="8" rx="2" />
    <rect x="22" y="30" width="10" height="50" rx="2" />
    <rect x="128" y="30" width="10" height="50" rx="2" />
    <circle cx="27" cy="24" r="5" />
    <circle cx="133" cy="24" r="5" />
    <rect x="34" y="36" width="92" height="28" rx="2" />
    <rect x="31" y="42" width="8" height="16" rx="1.5" />
    <rect x="121" y="42" width="8" height="16" rx="1.5" />
    <path class="hot" d="M50 44h18l8 12h20" />
    <circle cx="50" cy="44" r="2" />
    <circle cx="96" cy="56" r="2" />
    <circle cx="108" cy="56" r="2" />
    <circle cx="62" cy="56" r="2" />
  {:else if kind === 'flux'}
    <g transform="rotate(-20 80 48)">
      <rect x="22" y="42" width="8" height="12" rx="2" />
      <rect x="30" y="40" width="80" height="16" rx="3" />
      <path d="M52 40v16M82 40v16M110 42l14 4v4l-14 4" />
      <text x="67" y="51">FLUX</text>
      <rect class="hot" x="124" y="46.5" width="8" height="3" rx="1" />
    </g>
  {:else if kind === 'sucker'}
    <rect x="8" y="40" width="6" height="16" rx="2" />
    <path d="M14 48h22" />
    <rect x="36" y="38" width="88" height="20" rx="4" />
    <rect x="58" y="31" width="12" height="7" rx="2" />
    <path d="M90 38v20M96 38v20M102 38v20" />
    <path class="hot" d="M124 42l22 4v4l-22 4" />
  {:else if kind === 'tweezers'}
    <!-- One pair, mirrored about its middle: each arm is an outer and an inner
         edge that meet at the joined back end and again at its tip, a
         little apart from the other arm's. -->
    <path d="M18 42 108 36l42 10.5M18 54l90 6 42-10.5M18 42q-7 6 0 12" />
    <path d="M26 46.5 108 41l42 5.5M26 49.5l82 5.5 42-5.5M26 46.5q-2 1.5 0 3" />
    <path d="M58 39.8v4.1M64 39.4v4.1M70 39v4.1M58 56.2v-4.1M64 56.6v-4.1M70 57v-4.1" />
  {:else if kind === 'fuses'}
    <rect x="22" y="22" width="14" height="18" rx="2" />
    <rect x="94" y="22" width="14" height="18" rx="2" />
    <path d="M36 24h58M36 38h58" />
    <path class="hot" d="M36 31c20-6 38 6 58 0" />
    <rect x="50" y="56" width="14" height="18" rx="2" />
    <rect x="122" y="56" width="14" height="18" rx="2" />
    <path d="M64 58h58M64 72h58" />
    <path class="hot" d="M64 65c20-6 38 6 58 0" />
  {/if}
</svg>

<style>
  .drawing {
    display: block;
    width: 100%;
    height: auto;
    overflow: visible;
    fill: none;
    stroke: var(--muted);
    stroke-width: 1.5;
    stroke-linecap: round;
    stroke-linejoin: round;
  }
  .drawing :global(*) {
    vector-effect: non-scaling-stroke;
  }
  .hot {
    stroke: var(--text);
  }
  text {
    fill: var(--text);
    stroke: none;
    font-family: var(--mono);
    font-size: 8px;
    text-anchor: middle;
  }
</style>

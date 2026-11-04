<script lang="ts">
  // The board seen from its edge, with one part of each stage standing on it
  // in build order: why the lowest parts go first. Each stage's heading draws
  // the whole row, with its own part picked out, so the builder sees where
  // they are in the climb from flat resistors to tall capacitors and pots.
  import type { PartKind } from '../../lib/parts';

  let { kinds, at }: { kinds: PartKind[]; at: number } = $props();

  const SLOT = 13;
  const BASE = 17;

  // Each kind's side view, standing on the board at x = 0, as rough as the
  // drawings in a field manual: [width, height, shape].
  type Shape = 'lying' | 'box' | 'dome' | 'can' | 'toggle' | 'shaft';
  const SIDE: Record<PartKind, [number, number, Shape]> = {
    board: [0, 0, 'box'],
    resistor: [10, 3, 'lying'],
    diode: [8, 3, 'lying'],
    socket: [10, 2.5, 'box'],
    chip: [10, 5, 'box'],
    crystal: [7, 5, 'can'],
    capacitor: [6, 7, 'box'],
    inductor: [8, 7, 'can'],
    transistor: [5, 8, 'dome'],
    led: [5, 9, 'dome'],
    electrolytic: [7, 12, 'can'],
    relay: [10, 9, 'box'],
    switch: [7, 13, 'toggle'],
    transformer: [10, 10, 'box'],
    pot: [10, 15, 'shaft'],
    hardware: [9, 9, 'box'],
    other: [6, 6, 'box'],
  };

  function path(kind: PartKind, x: number): string {
    const [w, h, shape] = SIDE[kind];
    const left = x + (SLOT - w) / 2;
    const top = BASE - h;
    switch (shape) {
      case 'lying':
        return `M${left + 1.5} ${top}h${w - 3}a1.5 1.5 0 0 1 0 ${h}h${-(w - 3)}a1.5 1.5 0 0 1 0 ${-h}z`;
      case 'dome':
        return `M${left} ${BASE}v${-(h - w / 2)}a${w / 2} ${w / 2} 0 0 1 ${w} 0v${h - w / 2}z`;
      case 'can':
        return `M${left} ${BASE}v${-(h - 1)}q0 -1 1 -1h${w - 2}q1 0 1 1v${h - 1}z`;
      case 'toggle':
        return `M${left} ${BASE}v-6h${w}v6zM${left + w / 2} ${BASE - 6}l2 ${-(h - 6)}`;
      case 'shaft':
        return `M${left} ${BASE}v-7h${w}v7zM${left + w / 2 - 1.5} ${BASE - 7}v${-(h - 7)}h3v${h - 7}`;
      case 'box':
        return `M${left} ${BASE}v${-h}h${w}v${h}z`;
    }
  }

  const shown = $derived(kinds.filter((k) => k !== 'board'));
  const current = $derived(kinds[at] === 'board' ? -1 : shown.indexOf(kinds[at] as PartKind));
</script>

<svg
  class="profile"
  viewBox={`0 0 ${shown.length * SLOT + 2} 19`}
  width={shown.length * SLOT + 2}
  height="19"
  aria-hidden="true"
  focusable="false"
>
  <path class="board" d={`M0 ${BASE + 0.75}h${shown.length * SLOT + 2}`} />
  {#each shown as kind, i (i)}
    <path
      class:done={i < current}
      class:here={i === current}
      class:ahead={i > current}
      d={path(kind, i * SLOT + 1)}
    />
  {/each}
</svg>

<style>
  .profile {
    flex-shrink: 0;
    overflow: visible;
    fill: none;
    stroke: var(--muted);
    stroke-linejoin: round;
    stroke-width: 1;
  }
  .board {
    stroke: var(--line);
    stroke-width: 1.5;
  }
  .done {
    opacity: 0.55;
  }
  .ahead {
    opacity: 0.22;
  }
  .here {
    fill: var(--accent-soft);
    stroke: var(--accent);
  }
</style>

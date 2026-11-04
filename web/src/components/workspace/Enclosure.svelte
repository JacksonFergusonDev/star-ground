<script lang="ts">
  // A project's pedal from above: its paint, a knob for each pot named as the
  // parts list names it, its toggles, an LED that lights once every part is
  // placed, and a footswitch. A picture to know the project by, not a
  // drilling template (see lib/enclosure.ts).
  import type { Face, Paint } from '../../lib/enclosure';
  import { PAINTS, layout } from '../../lib/enclosure';
  import { count } from '../../lib/workspace.svelte';

  let {
    name,
    face,
    paint,
    lit = false,
  }: { name: string; face: Face; paint: Paint; lit?: boolean } = $props();

  // Drawn a little larger than its units, so a knob's label can be read.
  const SCALE = 1.25;
  const at = $derived(layout(face));
  const coat = $derived(PAINTS[paint]);
  // Each knob turned a little differently, as a set pedal's are.
  const TURNS = [-40, 20, -10, 50, -60, 0, 30, -25, 45];
  const turn = (i: number): number => TURNS[i % TURNS.length] ?? 0;
  const title = $derived(
    name.length > at.width / 3.6 ? `${name.slice(0, Math.floor(at.width / 3.6) - 1)}…` : name,
  );
  // A label wider than its knob's share of the row is squeezed to fit.
  const LABEL_EM = 0.62;
  const squeeze = (text: string, room: number): number | undefined =>
    text.length * 3.6 * LABEL_EM > room ? room : undefined;
  const described = $derived(
    [
      `${name}, painted ${coat.name.toLowerCase()}`,
      face.knobs.length
        ? `${count(face.knobs.length, 'knob')}${
            face.knobs.some(Boolean) ? ` (${face.knobs.filter(Boolean).join(', ')})` : ''
          }`
        : 'no knobs',
      face.toggles ? count(face.toggles, 'toggle') : '',
      lit ? 'its LED lit' : '',
    ]
      .filter(Boolean)
      .join(', '),
  );
</script>

<svg
  class="enclosure"
  class:light-ink={coat.ink === 'light'}
  viewBox={`-2 -2 ${at.width + 4} ${at.height + 4}`}
  width={(at.width + 4) * SCALE}
  height={(at.height + 4) * SCALE}
  role="img"
  aria-label={described}
>
  <rect
    class="box"
    x="0"
    y="0"
    width={at.width}
    height={at.height}
    rx="6"
    style:fill={coat.color}
  />
  {#each at.knobs as knob, i (i)}
    <g transform={`translate(${knob.x} ${knob.y})`}>
      <circle class="knob" r={at.knob} />
      <circle class="cap" r={at.knob * 0.62} />
      <path class="pointer" d={`M0 0V${-at.knob * 0.85}`} transform={`rotate(${turn(i)})`} />
      {#if knob.label}
        <text
          class="label"
          y={at.knob + 5.5}
          textLength={squeeze(knob.label, knob.room)}
          lengthAdjust="spacingAndGlyphs">{knob.label}</text
        >
      {/if}
    </g>
  {/each}
  {#each at.toggles as toggle, i (i)}
    <g transform={`translate(${toggle.x} ${toggle.y})`}>
      <path class="lever" d="M0 0 2 -6" />
      <circle class="bushing" r="2.4" />
    </g>
  {/each}
  <text class="name" x={at.width / 2} y={at.led.y - 8}>{title}</text>
  <circle class="led" class:lit cx={at.led.x} cy={at.led.y} r="2.3" />
  <g transform={`translate(${at.footswitch.x} ${at.footswitch.y})`}>
    <circle class="stomp" r="7" />
    <circle class="stomp-top" r="4.3" />
  </g>
</svg>

<style>
  .enclosure {
    flex-shrink: 0;
    overflow: visible;
  }
  /* The enclosure's paint and hardware are the pedal's own colours, which no
     site token covers. */
  .box {
    stroke: rgb(0 0 0 / 35%);
    stroke-width: 1;
  }
  .knob {
    fill: #1b1c1f;
    stroke: rgb(0 0 0 / 50%);
    stroke-width: 0.8;
  }
  .cap {
    fill: #2a2b30;
  }
  .pointer {
    stroke: #eceae4;
    stroke-linecap: round;
    stroke-width: 1.2;
  }
  .label,
  .name {
    fill: rgb(0 0 0 / 72%);
    font-family: var(--mono);
    letter-spacing: 0.2px;
    text-anchor: middle;
    text-transform: uppercase;
  }
  .label {
    font-size: 3.6px;
  }
  .name {
    font-size: 5px;
    font-weight: 600;
  }
  .light-ink .label,
  .light-ink .name {
    fill: rgb(255 255 255 / 82%);
  }
  .bushing {
    fill: #c9ccd1;
    stroke: rgb(0 0 0 / 40%);
    stroke-width: 0.6;
  }
  .lever {
    stroke: #dfe2e6;
    stroke-linecap: round;
    stroke-width: 1.6;
  }
  .led {
    fill: #4a1b1b;
    stroke: rgb(0 0 0 / 45%);
    stroke-width: 0.6;
  }
  .led.lit {
    fill: #ff4b3e;
  }
  .stomp {
    fill: #c9ccd1;
    stroke: rgb(0 0 0 / 40%);
    stroke-width: 0.8;
  }
  .stomp-top {
    fill: #a9adb3;
  }
</style>

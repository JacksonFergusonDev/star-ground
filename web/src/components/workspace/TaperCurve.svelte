<script lang="ts">
  // A pot's taper as a builder knows it: how its output climbs as the knob
  // turns. A (audio) climbs late, B (linear) evenly, C (reverse audio) early.
  import type { Taper } from '../../lib/parts';
  import { taperName } from '../../lib/parts';

  let { taper }: { taper: Taper } = $props();

  const CURVES: Record<Taper, string> = {
    A: 'M2 14C9 14 12 11 14 2',
    B: 'M2 14 14 2',
    C: 'M2 14C4 5 7 2 14 2',
  };
</script>

<span class="taper" title={taperName(taper)}>
  <svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true" focusable="false">
    <path class="axes" d="M1 1v14h14" />
    <path d={CURVES[taper]} />
  </svg>
  <span class="visually-hidden">{taperName(taper)}</span>
</span>

<style>
  .taper {
    display: inline-flex;
    flex-shrink: 0;
    color: var(--muted);
    vertical-align: middle;
  }
  svg {
    fill: none;
    stroke: currentcolor;
    stroke-linecap: round;
    stroke-width: 1.4;
  }
  .axes {
    opacity: 0.5;
    stroke-width: 1;
  }
  .visually-hidden {
    position: absolute;
    width: 1px;
    height: 1px;
    overflow: hidden;
    clip-path: inset(50%);
    white-space: nowrap;
  }
</style>

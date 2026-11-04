<script lang="ts">
  // The hero's Big Muff board, still and dimmed: an empty page's picture, so
  // the app's empty states carry the home page's circuit. It draws the same
  // board with the same code as the hero's first frame (lib/circuit-field),
  // once, and again when its width changes. Nothing here moves.
  import { resolveColor } from 'house-style/js/field.js';
  import board from 'virtual:circuit-board';
  import { onMount } from 'svelte';
  import { drawBoard } from '../../lib/circuit-field/board';
  import { vec } from '../../lib/circuit-field/geometry';

  let canvas = $state<HTMLCanvasElement>();

  function draw(): void {
    if (!canvas) return;
    const width = canvas.clientWidth;
    const height = canvas.clientHeight;
    if (!width || !height) return;
    const scale = Math.min(window.devicePixelRatio || 1, 2);
    canvas.width = Math.round(width * scale);
    canvas.height = Math.round(height * scale);
    const ctx = canvas.getContext('2d');
    if (!ctx) return;
    // The board fills the width and is cut top and bottom, centred.
    const unit = (width / board.width) * scale;
    const origin = vec(0, (canvas.height - board.height * unit) / 2);
    const accent = resolveColor('var(--accent)');
    const text = resolveColor('var(--text)');
    const rgba = (c: number[], a: number) => `rgba(${c[0]}, ${c[1]}, ${c[2]}, ${a})`;
    const font =
      getComputedStyle(document.documentElement).getPropertyValue('--mono').trim() || 'monospace';
    drawBoard(ctx, board, {
      unit,
      origin,
      font,
      mask: rgba(accent, 0.045),
      edge: rgba(text, 0.3),
      copper: rgba(accent, 0.42),
      pad: rgba(text, 0.5),
      silk: rgba(text, 0.42),
    });
  }

  onMount(() => {
    let width = 0;
    const observer = new ResizeObserver(() => {
      if (!canvas || canvas.clientWidth === width) return;
      width = canvas.clientWidth;
      draw();
    });
    if (canvas) observer.observe(canvas);
    return () => observer.disconnect();
  });
</script>

<canvas class="still" bind:this={canvas} aria-hidden="true"></canvas>

<style>
  .still {
    display: block;
    width: 100%;
    height: 100%;
    opacity: 0.5;
    mask-image: radial-gradient(ellipse at center, #000 35%, transparent 80%);
  }
</style>

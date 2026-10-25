// Draws the board's still layers once, with Canvas 2D: the board itself,
// copper under solder mask, bare pads, drill holes, wire jumpers, and
// silkscreen. The animation then only has to draw current moving through it.

import type { Silk } from './footprints';
import type { Vec } from './geometry';
import { LABEL_SIZE, type Board } from './layout';

export interface BoardStyle {
  /** Device pixels per board unit, and where the board's origin sits. */
  unit: number;
  origin: Vec;
  /** The solder mask, a shade lifted off the page. */
  mask: string;
  edge: string;
  copper: string;
  pad: string;
  silk: string;
  font: string;
}

const CORNER = 1.2;
const MOUNTING_HOLE = 1.6;

export function drawBoard(ctx: CanvasRenderingContext2D, board: Board, style: BoardStyle): void {
  const u = style.unit;
  ctx.setTransform(1, 0, 0, 1, 0, 0);
  ctx.clearRect(0, 0, ctx.canvas.width, ctx.canvas.height);
  ctx.setTransform(u, 0, 0, u, style.origin.x, style.origin.y);
  ctx.lineCap = 'round';
  ctx.lineJoin = 'round';
  const circle = (p: Vec, r: number) => {
    ctx.beginPath();
    ctx.arc(p.x, p.y, r, 0, Math.PI * 2);
  };

  // The board: solder mask over FR-4, with rounded corners.
  ctx.beginPath();
  ctx.roundRect(0, 0, board.width, board.height, CORNER);
  ctx.fillStyle = style.mask;
  ctx.fill();
  ctx.strokeStyle = style.edge;
  ctx.lineWidth = 0.12;
  ctx.stroke();

  // Copper under the mask.
  ctx.strokeStyle = style.copper;
  for (const trace of board.traces) {
    ctx.lineWidth = trace.width;
    ctx.beginPath();
    trace.points.forEach((p, i) => (i ? ctx.lineTo(p.x, p.y) : ctx.moveTo(p.x, p.y)));
    ctx.stroke();
  }

  // Bare, tinned pads.
  ctx.fillStyle = style.pad;
  const holes: { at: Vec; drill: number }[] = [];
  for (const at of board.jumpers.flatMap((j) => [j.a, j.b])) {
    circle(at, 0.4);
    ctx.fill();
    holes.push({ at, drill: 0.17 });
  }
  for (const part of board.parts) {
    for (const pad of part.footprint.pads) {
      if (pad.square) {
        const r = pad.radius * 0.92;
        ctx.fillRect(pad.at.x - r, pad.at.y - r, 2 * r, 2 * r);
      } else {
        circle(pad.at, pad.radius);
        ctx.fill();
      }
      holes.push(pad);
    }
  }

  // Drill holes go through everything, mounting holes included.
  ctx.globalCompositeOperation = 'destination-out';
  ctx.fillStyle = '#000';
  const m = MOUNTING_HOLE;
  for (const [x, y] of [
    [m, m],
    [board.width - m, m],
    [m, board.height - m],
    [board.width - m, board.height - m],
  ] as const) {
    circle({ x, y }, 0.6);
    ctx.fill();
  }
  for (const hole of holes) {
    circle(hole.at, hole.drill);
    ctx.fill();
  }
  ctx.globalCompositeOperation = 'source-over';

  // Silkscreen: wire jumpers, outlines, and designators.
  ctx.strokeStyle = style.silk;
  ctx.fillStyle = style.silk;
  ctx.lineWidth = 0.1;
  for (const { a, b } of board.jumpers) {
    ctx.beginPath();
    ctx.moveTo(a.x, a.y);
    ctx.lineTo(b.x, b.y);
    ctx.stroke();
  }
  for (const part of board.parts) for (const silk of part.footprint.silk) drawSilk(ctx, silk);

  // Text at device resolution, so small labels stay crisp.
  ctx.setTransform(1, 0, 0, 1, 0, 0);
  ctx.font = `500 ${LABEL_SIZE * u}px ${style.font}`;
  ctx.textAlign = 'center';
  ctx.textBaseline = 'middle';
  for (const part of board.parts) {
    ctx.fillText(
      part.spec.ref,
      style.origin.x + part.labelAt.x * u,
      style.origin.y + part.labelAt.y * u,
    );
  }
}

function drawSilk(ctx: CanvasRenderingContext2D, silk: Silk): void {
  ctx.beginPath();
  if (silk.kind === 'arc') {
    ctx.arc(silk.center.x, silk.center.y, silk.radius, silk.start, silk.end);
    ctx.stroke();
    return;
  }
  silk.points.forEach((p, i) => (i ? ctx.lineTo(p.x, p.y) : ctx.moveTo(p.x, p.y)));
  if (silk.kind === 'fill') {
    ctx.closePath();
    ctx.fill();
    return;
  }
  if (silk.closed) ctx.closePath();
  ctx.stroke();
}

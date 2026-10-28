// Routes a single-sided board on a 0.05 inch grid, one net at a time, the way
// a careful hand layout would look: straight runs and 45 degree bends, never a
// sharp corner, clear of other nets' copper. When a net can't get through, it
// hops a wire jumper over the copper in its way, as single-sided boards do.
//
// Each net grows as a tree from its root pin, so every copper cell knows its
// parent and its distance back to the root along the copper. The animation
// reads current flow straight off those trees.

import { vec, type Vec } from './geometry';

const GRID = 0.5;
const CLEARANCE = 0.3;
const JUMPER_PAD = 0.45;
/** What a jumper costs, in units of trace it saves: worth it only for a real detour. */
const JUMPER_COST = 12;
/** Extra cost for 45 and 90 degree turns; sharper turns aren't allowed. */
const TURN_COST = [0, 0.35, 2.5];
const STEPS: readonly [number, number][] = [
  [1, 0],
  [1, 1],
  [0, 1],
  [-1, 1],
  [-1, 0],
  [-1, -1],
  [0, -1],
  [1, -1],
];

interface RouterPin {
  net: string;
  at: Vec;
  radius: number;
  square: boolean;
}

export interface NetRequest {
  net: string;
  width: number;
  /** The root pin is the first; the rest join the tree nearest-first. */
  pins: RouterPin[];
}

export interface RoutedNet {
  net: string;
  width: number;
  /** Each path runs from a pin's centre cell to the tree it joined. */
  paths: number[][];
  /** Cells joined to their parent by a wire jumper rather than copper. */
  jumped: Set<number>;
  parent: Map<number, number>;
  /** Distance along the copper from each cell to the root pin. */
  distance: Map<number, number>;
  root: number;
  failed: RouterPin[];
}

export interface Grid {
  cols: number;
  rows: number;
  cell(p: Vec): number;
  point(cell: number): Vec;
}

export function makeGrid(width: number, height: number): Grid {
  const cols = Math.round(width / GRID) + 1;
  const rows = Math.round(height / GRID) + 1;
  return {
    cols,
    rows,
    cell: (p) =>
      Math.min(rows - 1, Math.max(0, Math.round(p.y / GRID))) * cols +
      Math.min(cols - 1, Math.max(0, Math.round(p.x / GRID))),
    point: (cell) => vec((cell % cols) * GRID, Math.floor(cell / cols) * GRID),
  };
}

/** Routes `requests` in order on a board `width` by `height` units. */
export function route(
  grid: Grid,
  width: number,
  height: number,
  requests: NetRequest[],
): RoutedNet[] {
  const { cols, rows } = grid;
  const size = cols * rows;
  const owner = new Int16Array(size).fill(-1);
  // Half the copper's width at each owned cell; pads are areas, so 0.
  const half = new Float32Array(size);
  const isPad = new Uint8Array(size);

  const markPad = (at: Vec, radius: number, square: boolean, net: number) => {
    const reach = Math.ceil(radius / GRID) + 1;
    const centre = grid.cell(at);
    const cx = centre % cols;
    const cy = Math.floor(centre / cols);
    for (let y = cy - reach; y <= cy + reach; y++) {
      for (let x = cx - reach; x <= cx + reach; x++) {
        if (x < 0 || y < 0 || x >= cols || y >= rows) continue;
        const dx = Math.abs(x * GRID - at.x);
        const dy = Math.abs(y * GRID - at.y);
        const inside = square ? dx <= radius && dy <= radius : Math.hypot(dx, dy) <= radius;
        if (!inside && y * cols + x !== centre) continue;
        owner[y * cols + x] = net;
        half[y * cols + x] = 0;
        isPad[y * cols + x] = 1;
      }
    }
  };

  requests.forEach((request, net) => {
    for (const pin of request.pins) markPad(pin.at, pin.radius, pin.square, net);
  });

  // 0 blocked, 1 passable but close to other copper, 2 open.
  const room = (cell: number, net: number, own: number): number => {
    if (owner[cell] >= 0 && owner[cell] !== net) return 0;
    const x = cell % cols;
    const y = Math.floor(cell / cols);
    if (x * GRID < 0.8 || y * GRID < 0.8 || x * GRID > width - 0.8 || y * GRID > height - 0.8) {
      return 0;
    }
    let result = 2;
    for (let dy = -4; dy <= 4; dy++) {
      for (let dx = -4; dx <= 4; dx++) {
        const nx = x + dx;
        const ny = y + dy;
        if (nx < 0 || ny < 0 || nx >= cols || ny >= rows) continue;
        const other = ny * cols + nx;
        const o = owner[other] as number;
        if (o < 0 || o === net) continue;
        const d = Math.hypot(dx, dy) * GRID;
        const need = own + CLEARANCE + (isPad[other] ? 0.22 : (half[other] as number));
        if (d < need) return 0;
        if (d < need + 0.5) result = 1;
      }
    }
    return result;
  };

  const routed: RoutedNet[] = [];
  requests.forEach((request, net) => {
    const own = request.width / 2;
    const [rootPin, ...rest] = request.pins;
    if (!rootPin) return;
    const root = grid.cell(rootPin.at);
    const parent = new Map<number, number>([[root, -1]]);
    const distance = new Map<number, number>([[root, 0]]);
    const tree = new Uint8Array(size);
    const result: RoutedNet = {
      net: request.net,
      width: request.width,
      paths: [],
      jumped: new Set(),
      parent,
      distance,
      root,
      failed: [],
    };

    // A pin's pad joins the tree whole, so traces can meet it anywhere.
    const joinPad = (pin: RouterPin) => {
      const centre = grid.cell(pin.at);
      tree[centre] = 1;
      for (let cell = 0; cell < size; cell++) {
        if (owner[cell] !== net || !isPad[cell] || tree[cell]) continue;
        const p = grid.point(cell);
        if (Math.hypot(p.x - pin.at.x, p.y - pin.at.y) > pin.radius * 1.5 + GRID) continue;
        tree[cell] = 1;
        parent.set(cell, centre);
        distance.set(
          cell,
          (distance.get(centre) ?? 0) + Math.hypot(p.x - pin.at.x, p.y - pin.at.y),
        );
      }
    };
    joinPad(rootPin);

    const pending = [...rest];
    const joined = [rootPin];
    while (pending.length) {
      // Nearest pin to the tree so far.
      pending.sort(
        (a, b) =>
          Math.min(...joined.map((j) => Math.hypot(a.at.x - j.at.x, a.at.y - j.at.y))) -
          Math.min(...joined.map((j) => Math.hypot(b.at.x - j.at.x, b.at.y - j.at.y))),
      );
      const pin = pending.shift() as RouterPin;
      const start = grid.cell(pin.at);
      if (tree[start]) {
        joined.push(pin);
        continue;
      }
      const path = search(start, net, own, tree, true) ?? undefined;
      if (!path) {
        result.failed.push(pin);
        continue;
      }
      const cells = path.map((step) => step.cell);
      for (const step of path) {
        if (owner[step.cell] < 0) {
          owner[step.cell] = net;
          half[step.cell] = own;
        }
      }
      for (let i = 1; i < path.length; i++) {
        const step = path[i] as { cell: number; jump: boolean };
        if (!step.jump) continue;
        const prev = path[i - 1] as { cell: number; jump: boolean };
        markPad(grid.point(prev.cell), JUMPER_PAD, false, net);
        markPad(grid.point(step.cell), JUMPER_PAD, false, net);
      }
      for (let i = cells.length - 2; i >= 0; i--) {
        const cell = cells[i] as number;
        const next = cells[i + 1] as number;
        if (parent.has(cell) && tree[cell]) continue;
        parent.set(cell, next);
        const a = grid.point(cell);
        const b = grid.point(next);
        distance.set(cell, (distance.get(next) ?? 0) + Math.hypot(a.x - b.x, a.y - b.y));
        if ((path[i + 1] as { jump: boolean }).jump) result.jumped.add(cell);
        tree[cell] = 1;
      }
      result.paths.push(cells);
      joinPad(pin);
      joined.push(pin);
    }
    routed.push(result);
  });
  return routed;

  // Cheapest route from `start` to any cell of `tree`, optionally jumping.
  function search(
    start: number,
    net: number,
    own: number,
    tree: Uint8Array,
    jumps: boolean,
  ): { cell: number; jump: boolean }[] | undefined {
    const states = size * 9;
    const cost = new Float64Array(states).fill(Infinity);
    const from = new Int32Array(states).fill(-1);
    const viaJump = new Uint8Array(states);
    const roomCache = new Int8Array(size).fill(-1);
    const jumperCache = new Int8Array(size).fill(-1);
    const roomAt = (cell: number) => {
      if (roomCache[cell] === -1) roomCache[cell] = room(cell, net, own);
      return roomCache[cell] as number;
    };
    const jumperAt = (cell: number) => {
      if (jumperCache[cell] === -1) jumperCache[cell] = room(cell, net, JUMPER_PAD) > 0 ? 1 : 0;
      return jumperCache[cell] === 1;
    };
    const heap = new Heap();
    const first = start * 9 + 8;
    cost[first] = 0;
    heap.push(0, first);
    while (heap.size) {
      const [g, state] = heap.pop();
      if (g > (cost[state] as number)) continue;
      const cell = Math.floor(state / 9);
      const dir = state % 9;
      if (tree[cell] && cell !== start) {
        const out: { cell: number; jump: boolean }[] = [];
        for (let s = state; s >= 0; s = from[s] as number) {
          out.push({ cell: Math.floor(s / 9), jump: viaJump[s] === 1 });
        }
        return out.reverse();
      }
      const x = cell % cols;
      const y = Math.floor(cell / cols);
      for (let d = 0; d < 8; d++) {
        const turn = dir === 8 ? 0 : Math.min(Math.abs(d - dir), 8 - Math.abs(d - dir));
        if (turn > 2) continue;
        const [sx, sy] = STEPS[d] as [number, number];
        const nx = x + sx;
        const ny = y + sy;
        if (nx < 0 || ny < 0 || nx >= cols || ny >= rows) continue;
        const next = ny * cols + nx;
        const open = owner[next] === net ? 2 : roomAt(next);
        if (open === 0) continue;
        const step =
          (d % 2 ? Math.SQRT2 : 1) * GRID + (TURN_COST[turn] as number) + (open === 1 ? 0.2 : 0);
        const ns = next * 9 + d;
        if (g + step < (cost[ns] as number)) {
          cost[ns] = g + step;
          from[ns] = state;
          viaJump[ns] = 0;
          heap.push(g + step, ns);
        }
      }
      // A jumper: straight across, between two free pad sites.
      if (!jumps || !jumperAt(cell)) continue;
      for (const d of [0, 2, 4, 6]) {
        const [sx, sy] = STEPS[d] as [number, number];
        for (let length = 4; length <= 16; length++) {
          const nx = x + sx * length;
          const ny = y + sy * length;
          if (nx < 0 || ny < 0 || nx >= cols || ny >= rows) break;
          const next = ny * cols + nx;
          if (!jumperAt(next)) continue;
          const ns = next * 9 + 8;
          const step = JUMPER_COST + length * GRID;
          if (g + step < (cost[ns] as number)) {
            cost[ns] = g + step;
            from[ns] = state;
            viaJump[ns] = 1;
            heap.push(g + step, ns);
          }
        }
      }
    }
    return undefined;
  }
}

class Heap {
  private keys: number[] = [];
  private values: number[] = [];

  get size(): number {
    return this.keys.length;
  }

  push(key: number, value: number): void {
    const keys = this.keys;
    const values = this.values;
    let i = keys.length;
    keys.push(key);
    values.push(value);
    while (i > 0) {
      const up = (i - 1) >> 1;
      if ((keys[up] as number) <= key) break;
      keys[i] = keys[up] as number;
      values[i] = values[up] as number;
      i = up;
    }
    keys[i] = key;
    values[i] = value;
  }

  pop(): [number, number] {
    const keys = this.keys;
    const values = this.values;
    const top: [number, number] = [keys[0] as number, values[0] as number];
    const lastKey = keys.pop() as number;
    const lastValue = values.pop() as number;
    if (keys.length) {
      let i = 0;
      for (;;) {
        const l = 2 * i + 1;
        if (l >= keys.length) break;
        const r = l + 1;
        const c = r < keys.length && (keys[r] as number) < (keys[l] as number) ? r : l;
        if ((keys[c] as number) >= lastKey) break;
        keys[i] = keys[c] as number;
        values[i] = values[c] as number;
        i = c;
      }
      keys[i] = lastKey;
      values[i] = lastValue;
    }
    return top;
  }
}

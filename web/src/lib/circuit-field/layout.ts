// Builds the Big Muff π board (circuit.ts): places its parts, routes its
// copper (router.ts), and describes current paths for the animation.
//
// - Ground returns: the ground net is a tree rooted at the star, so each of its
//   segments knows its distance from the star and which ground pins feed it.
//   Pins are numbered so every segment's feeders form one contiguous range.
// - The signal: the guitar's path from the input pad, through each part in
//   turn, to the output pad, measured as distance still to travel.

import {
  BOARD,
  CLIPPING_STAGES,
  GROUND,
  PARTS,
  SIGNAL_PATH,
  SUPPLY,
  TONE_BRANCH,
  type PartSpec,
  type Side,
  type SignalStep,
} from './circuit';
import { place, type Pad, type PlacedFootprint } from './footprints';
import { distance, vec, type Vec } from './geometry';
import { makeGrid, route, type Grid, type NetRequest, type RoutedNet } from './router';

export const LABEL_SIZE = 0.8;
const WIDTH = { power: 0.6, signal: 0.4, body: 0.22 } as const;

interface Part {
  spec: PartSpec;
  footprint: PlacedFootprint;
  labelAt: Vec;
}

interface Trace {
  points: Vec[];
  width: number;
}

interface Jumper {
  a: Vec;
  b: Vec;
}

/** A straight run that current flows along, for the animation. */
interface FlowSegment {
  a: Vec;
  b: Vec;
  /** Distance still to travel, at `a` and at `b`. */
  da: number;
  db: number;
  /** The range of sources whose current flows here. */
  first: number;
  last: number;
  width: number;
}

interface FlowPad extends Pad {
  distance: number;
  first: number;
  last: number;
}

interface FlowGeometry {
  segments: FlowSegment[];
  pads: FlowPad[];
}

interface PulsePath {
  source: number;
  length: number;
}

interface FeedbackFlow {
  /** Distance along the main signal path to this stage's collector. */
  offset: number;
  forward: PulsePath;
  reverse: PulsePath;
}

export interface Board extends FlowGeometry {
  width: number;
  height: number;
  star: Vec;
  parts: Part[];
  traces: Trace[];
  jumpers: Jumper[];
  /** Each ground pin's distance to the star, by source id. */
  ground: number[];
  /** The signal's source id and the length of its path. */
  signal: PulsePath;
  /** Alternating clipping loops, triggered when a note reaches their stage. */
  feedback: FeedbackFlow[];
  /** Supply flow from 9V through its connected resistors. */
  supply: PulsePath;
  /** Pins the router couldn't reach; empty for a complete board. */
  failed: string[];
}

export function buildBoard(): Board {
  const parts = PARTS.map((spec): Part => {
    const footprint = place(spec.kind, spec.at, spec.turns);
    const side = spec.label ?? (spec.turns % 2 ? 'e' : 'n');
    return { spec, footprint, labelAt: labelAt(spec.ref, footprint, side) };
  });
  const star = (parts.find((p) => p.spec.kind === 'star') as Part).spec.at;

  // One request per net, rooted at the star for ground and the 9V pad for supply.
  const byNet = new Map<string, NetRequest>();
  for (const part of parts) {
    part.footprint.pads.forEach((pad, i) => {
      const net = part.spec.pins[i] as string;
      const request = byNet.get(net) ?? {
        net,
        width: net === GROUND || net === SUPPLY ? WIDTH.power : WIDTH.signal,
        pins: [],
      };
      const pin = { net, at: pad.at, radius: pad.radius, square: pad.square };
      const isRoot =
        (net === GROUND && part.spec.kind === 'star') ||
        (net === SUPPLY && part.spec.kind === 'terminal');
      if (isRoot) request.pins.unshift(pin);
      else request.pins.push(pin);
      byNet.set(net, request);
    });
  }
  const span = (r: NetRequest) => {
    const xs = r.pins.map((p) => p.at.x);
    const ys = r.pins.map((p) => p.at.y);
    return Math.max(...xs) - Math.min(...xs) + Math.max(...ys) - Math.min(...ys);
  };
  // Short local nets first, as a hand layout starts; then the ground returns
  // to the star; the supply last, since it can best afford a jumper.
  const requests = [...byNet.values()].sort((a, b) => {
    const rank = (r: NetRequest) => (r.net === GROUND ? 1 : r.net === SUPPLY ? 2 : 0);
    return rank(a) - rank(b) || span(a) - span(b);
  });

  const grid = makeGrid(
    BOARD.width,
    BOARD.height,
    parts.flatMap((p) => p.footprint.pads.map((pad) => pad.at)),
  );
  const routed = route(grid, BOARD.width, BOARD.height, requests);
  const nets = new Map(routed.map((r) => [r.net, r]));

  const traces: Trace[] = [];
  const jumpers: Jumper[] = [];
  for (const net of routed) {
    for (const path of net.paths) {
      let run: Vec[] = [grid.point(path[0] as number)];
      for (let i = 1; i < path.length; i++) {
        const cell = path[i] as number;
        const prev = path[i - 1] as number;
        if (net.jumped.has(prev) && net.parent.get(prev) === cell) {
          if (run.length > 1) traces.push({ points: simplify(run), width: net.width });
          jumpers.push({ a: grid.point(prev), b: grid.point(cell) });
          run = [grid.point(cell)];
        } else {
          run.push(grid.point(cell));
        }
      }
      if (run.length > 1) traces.push({ points: simplify(run), width: net.width });
    }
  }

  const segments: FlowSegment[] = [];
  const pads: FlowPad[] = [];
  const ground = groundFlow(grid, nets.get(GROUND) as RoutedNet, parts, segments, pads);
  const flow = { segments, pads };
  const { signal, feedback } = signalFlow(grid, nets, parts, ground.length, flow);
  const supply = supplyFlow(
    grid,
    nets.get(SUPPLY) as RoutedNet,
    parts,
    signal.source + 1 + feedback.length * 2,
    flow,
  );

  return {
    width: BOARD.width,
    height: BOARD.height,
    star,
    parts,
    traces,
    jumpers,
    segments,
    pads,
    ground,
    signal,
    feedback,
    supply,
    failed: routed.flatMap((r) => r.failed.map((p) => `${r.net} at ${p.at.x},${p.at.y}`)),
  };
}

function labelAt(text: string, footprint: PlacedFootprint, side: Side): Vec {
  const b = footprint.bounds;
  const w = text.length * LABEL_SIZE * 0.62;
  const gap = 0.3;
  const cx = (b.x0 + b.x1) / 2;
  const cy = (b.y0 + b.y1) / 2;
  switch (side) {
    case 'n':
      return vec(cx, b.y0 - gap - LABEL_SIZE / 2);
    case 's':
      return vec(cx, b.y1 + gap + LABEL_SIZE / 2);
    case 'e':
      return vec(b.x1 + gap + w / 2, cy);
    case 'w':
      return vec(b.x0 - gap - w / 2, cy);
  }
}

// Drops points that lie on the straight line between their neighbours.
function simplify(points: Vec[]): Vec[] {
  const out: Vec[] = [];
  for (const p of points) {
    const a = out[out.length - 2];
    const b = out[out.length - 1];
    if (a && b && Math.abs((b.x - a.x) * (p.y - b.y) - (b.y - a.y) * (p.x - b.x)) < 1e-6) {
      out[out.length - 1] = p;
    } else {
      out.push(p);
    }
  }
  return out;
}

function direction(grid: Grid, a: number, b: number): string {
  const p = grid.point(a);
  const q = grid.point(b);
  return `${Math.sign(q.x - p.x)},${Math.sign(q.y - p.y)}`;
}

// Numbers the ground pins depth-first from the star, so each cell's feeders
// are a contiguous range, then turns the ground net's paths into segments.
function groundFlow(
  grid: Grid,
  net: RoutedNet,
  parts: Part[],
  segments: FlowSegment[],
  pads: FlowPad[],
): number[] {
  const children = new Map<number, number[]>();
  for (const [cell, parent] of net.parent) {
    if (parent < 0) continue;
    const list = children.get(parent) ?? [];
    list.push(cell);
    children.set(parent, list);
  }
  const pins = new Map<number, Pad>();
  for (const part of parts) {
    part.footprint.pads.forEach((pad, i) => {
      if (part.spec.pins[i] === GROUND && part.spec.kind !== 'star') {
        pins.set(grid.cell(pad.at), pad);
      }
    });
  }

  const ids = new Map<number, number>();
  const range = new Map<number, [number, number]>();
  const distances: number[] = [];
  const visit = (cell: number): [number, number] => {
    let first = Infinity;
    let last = -Infinity;
    if (pins.has(cell)) {
      const id = distances.length;
      ids.set(cell, id);
      distances.push(net.distance.get(cell) ?? 0);
      first = last = id;
    }
    for (const child of (children.get(cell) ?? []).sort((a, b) => a - b)) {
      const [f, l] = visit(child);
      first = Math.min(first, f);
      last = Math.max(last, l);
    }
    range.set(cell, [first, last]);
    return [first, last];
  };
  visit(net.root);

  // Split each path wherever it bends, jumps, or gains feeders.
  for (const path of net.paths) {
    let start = 0;
    for (let i = 1; i < path.length; i++) {
      const prev = path[i - 1] as number;
      const cell = path[i] as number;
      const after = path[i + 1];
      const here = range.get(prev);
      const there = range.get(cell);
      const end =
        after === undefined ||
        direction(grid, prev, cell) !== direction(grid, cell, after) ||
        net.jumped.has(cell) ||
        net.jumped.has(prev) ||
        here?.[0] !== there?.[0] ||
        here?.[1] !== there?.[1];
      if (!end) continue;
      const a = path[start] as number;
      const [first, last] = range.get(a) ?? [Infinity, -Infinity];
      if (first <= last) {
        segments.push({
          a: grid.point(a),
          b: grid.point(cell),
          da: net.distance.get(a) ?? 0,
          db: net.distance.get(cell) ?? 0,
          first,
          last,
          width: net.width,
        });
      }
      start = i;
    }
  }
  for (const [cell, pad] of pins) {
    const id = ids.get(cell);
    if (id === undefined) continue;
    pads.push({ ...pad, distance: distances[id] as number, first: id, last: id });
  }
  return distances;
}

// The copper path between two cells of a net's tree, through their nearest
// common ancestor.
function treePath(net: RoutedNet, a: number, b: number): number[] | undefined {
  if (!net.parent.has(a) || !net.parent.has(b)) return undefined;
  const up = (cell: number) => {
    const out = [cell];
    for (let c = net.parent.get(cell) ?? -1; c >= 0; c = net.parent.get(c) ?? -1) out.push(c);
    return out;
  };
  const fromA = up(a);
  const fromB = up(b);
  const onA = new Set(fromA);
  const meet = fromB.findIndex((c) => onA.has(c));
  if (meet < 0) return undefined;
  const common = fromB[meet] as number;
  return [...fromA.slice(0, fromA.indexOf(common) + 1), ...fromB.slice(0, meet).reverse()];
}

interface Route {
  points: Vec[];
  /** Whether each step runs through a part rather than along copper. */
  inside: boolean[];
}

// The signal's route as points: copper between parts, then straight through
// each part from the pad it enters to the pad it leaves.
function signalRoute(
  grid: Grid,
  nets: Map<string, RoutedNet>,
  parts: Part[],
  steps: readonly SignalStep[],
  start: Vec,
  end: Vec,
): Route {
  const pad = (ref: string, net: string): Vec => {
    const part = parts.find((p) => p.spec.ref === ref) as Part;
    return (part.footprint.pads[part.spec.pins.indexOf(net)] as Pad).at;
  };
  const points: Vec[] = [start];
  const inside: boolean[] = [];
  let here = start;
  for (const [ref, from, to] of steps) {
    const enter = pad(ref, from);
    const net = nets.get(from);
    const cells = net && treePath(net, grid.cell(here), grid.cell(enter));
    for (const cell of cells?.slice(1, -1) ?? []) {
      points.push(grid.point(cell));
      inside.push(false);
    }
    points.push(enter);
    inside.push(false);
    here = pad(ref, to);
    points.push(here);
    inside.push(true);
  }
  const lastNet = steps[steps.length - 1]?.[2] as string;
  const net = nets.get(lastNet);
  const tail = net && treePath(net, grid.cell(here), grid.cell(end));
  for (const cell of tail?.slice(1, -1) ?? []) {
    points.push(grid.point(cell));
    inside.push(false);
  }
  points.push(end);
  inside.push(false);
  return { points, inside };
}

function lengthsOf(points: Vec[]): number[] {
  const out = [0];
  for (let i = 1; i < points.length; i++) {
    out.push((out[i - 1] as number) + distance(points[i - 1] as Vec, points[i] as Vec));
  }
  return out;
}

interface FlowRoute extends Route {
  source: number;
  remaining: number[];
  width: number;
}

// Uses the same geometry for all pulse types, including the actual pad centres.
function emitFlow(r: FlowRoute, parts: Part[], flow: FlowGeometry): void {
  for (let i = 1; i < r.points.length; i++) {
    const a = r.points[i - 1] as Vec;
    const b = r.points[i] as Vec;
    if (distance(a, b) < 1e-6) continue;
    flow.segments.push({
      a,
      b,
      da: r.remaining[i - 1] as number,
      db: r.remaining[i] as number,
      first: r.source,
      last: r.source,
      width: r.inside[i - 1] ? WIDTH.body : r.width,
    });
  }
  r.points.forEach((at, i) => {
    for (const part of parts) {
      const pad = part.footprint.pads.find((q) => distance(q.at, at) < 1e-6);
      if (pad)
        flow.pads.push({
          ...pad,
          distance: r.remaining[i] as number,
          first: r.source,
          last: r.source,
        });
    }
  });
}

function signalFlow(
  grid: Grid,
  nets: Map<string, RoutedNet>,
  parts: Part[],
  source: number,
  flow: FlowGeometry,
): { signal: PulsePath; feedback: FeedbackFlow[] } {
  const find = (ref: string) => parts.find((p) => p.spec.ref === ref) as Part;
  const padOn = (ref: string, net: string) => {
    const part = find(ref);
    return (part.footprint.pads[part.spec.pins.indexOf(net)] as Pad).at;
  };
  const main = signalRoute(grid, nets, parts, SIGNAL_PATH, find('IN').spec.at, find('OUT').spec.at);
  const lengths = lengthsOf(main.points);
  const length = lengths[lengths.length - 1] as number;
  emitFlow(
    { ...main, source, remaining: lengths.map((d) => length - d), width: WIDTH.signal },
    parts,
    flow,
  );

  // The tone stack's high-pass branch, timed to reach the tone pot's wiper
  // together with the main path.
  const fromC3 = padOn('Q3', 'Q3C');
  const wiper = padOn('TONE', 'TONE_WIPER');
  const startAt = main.points.findIndex((p) => distance(p, fromC3) < 1e-6);
  const endAt = main.points.findIndex((p) => distance(p, wiper) < 1e-6);
  if (startAt >= 0 && endAt >= 0) {
    const branch = signalRoute(grid, nets, parts, TONE_BRANCH, fromC3, wiper);
    const d0 = length - (lengths[startAt] as number);
    const d1 = length - (lengths[endAt] as number);
    const along = lengthsOf(branch.points);
    const total = along[along.length - 1] || 1;
    emitFlow(
      {
        ...branch,
        source,
        width: WIDTH.signal,
        remaining: along.map((d) => d0 + ((d1 - d0) * d) / total),
      },
      parts,
      flow,
    );
  }

  // Each note excites one of the two conducting directions. The routes end
  // back on the transistor, closing the loop along the existing copper.
  const feedback = CLIPPING_STAGES.map((stage, i): FeedbackFlow => {
    const collector = padOn(stage.transistor, stage.forward[0]?.[1] as string);
    const at = main.points.findIndex((p) => distance(p, collector) < 1e-6);
    const paths = [stage.forward, stage.reverse].map((steps, j): PulsePath => {
      const from = steps[0]?.[1] as string;
      const to = steps[steps.length - 1]?.[2] as string;
      const route = signalRoute(
        grid,
        nets,
        parts,
        steps,
        padOn(stage.transistor, from),
        padOn(stage.transistor, to),
      );
      const along = lengthsOf(route.points);
      const length = along[along.length - 1] as number;
      const id = source + 1 + i * 2 + j;
      emitFlow(
        { ...route, source: id, width: WIDTH.signal, remaining: along.map((d) => length - d) },
        parts,
        flow,
      );
      return { source: id, length };
    });
    return {
      offset: lengths[at] as number,
      forward: paths[0] as PulsePath,
      reverse: paths[1] as PulsePath,
    };
  });
  return { signal: { source, length }, feedback };
}

// One pulse leaves 9V and fans out along the supply tree, then crosses each
// supply resistor. Distances decrease away from 9V, so shared copper is drawn
// once and all branches start together.
function supplyFlow(
  grid: Grid,
  net: RoutedNet,
  parts: Part[],
  source: number,
  flow: FlowGeometry,
): PulsePath {
  const resistors = parts.filter((p) => p.spec.kind === 'resistor' && p.spec.pins.includes(SUPPLY));
  const length = Math.max(
    ...resistors.map((part) => {
      const pin = part.spec.pins.indexOf(SUPPLY);
      const a = part.footprint.pads[pin] as Pad;
      const b = part.footprint.pads[1 - pin] as Pad;
      return (net.distance.get(grid.cell(a.at)) as number) + distance(a.at, b.at);
    }),
  );
  for (const path of net.paths) {
    emitFlow(
      {
        points: path.map((cell) => grid.point(cell)),
        inside: path.slice(1).map(() => false),
        source,
        width: net.width,
        remaining: path.map((cell) => length - (net.distance.get(cell) as number)),
      },
      parts,
      flow,
    );
  }
  for (const part of resistors) {
    const pin = part.spec.pins.indexOf(SUPPLY);
    const a = part.footprint.pads[pin] as Pad;
    const b = part.footprint.pads[1 - pin] as Pad;
    const d = length - (net.distance.get(grid.cell(a.at)) as number);
    emitFlow(
      {
        points: [a.at, b.at],
        inside: [true],
        source,
        width: net.width,
        remaining: [d, d - distance(a.at, b.at)],
      },
      parts,
      flow,
    );
  }
  return { source, length };
}

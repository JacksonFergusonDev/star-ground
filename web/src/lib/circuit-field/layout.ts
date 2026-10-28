// Builds the Big Muff π board (circuit.ts): places its parts, routes its
// copper (router.ts), and describes two kinds of current for the animation.
//
// - Ground returns: the ground net is a tree rooted at the star, so each of its
//   segments knows its distance from the star and which ground pins feed it.
//   Pins are numbered so every segment's feeders form one contiguous range.
// - The signal: the guitar's path from the input pad, through each part in
//   turn, to the output pad, measured as distance still to travel.

import {
  BOARD,
  GROUND,
  PARTS,
  SIGNAL_PATH,
  SUPPLY,
  TONE_BRANCH,
  type PartSpec,
  type Side,
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

export interface Board {
  width: number;
  height: number;
  star: Vec;
  parts: Part[];
  traces: Trace[];
  jumpers: Jumper[];
  segments: FlowSegment[];
  pads: FlowPad[];
  /** Each ground pin's distance to the star, by source id. */
  ground: number[];
  /** The signal's source id and the length of its path. */
  signal: { source: number; length: number };
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

  const grid = makeGrid(BOARD.width, BOARD.height);
  const routed = route(grid, BOARD.width, BOARD.height, requests);
  const nets = new Map(routed.map((r) => [r.net, r]));
  const padAt = new Map<number, Vec>();
  for (const part of parts) {
    for (const pad of part.footprint.pads) padAt.set(grid.cell(pad.at), pad.at);
  }

  const traces: Trace[] = [];
  const jumpers: Jumper[] = [];
  for (const net of routed) {
    for (const path of net.paths) {
      let run: Vec[] = [padAt.get(path[0] as number) ?? grid.point(path[0] as number)];
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
  const ground = groundFlow(grid, nets.get(GROUND) as RoutedNet, parts, padAt, segments, pads);
  const signal = signalFlow(grid, nets, parts, ground.length, segments, pads);

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
  padAt: Map<number, Vec>,
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
          a: start === 0 ? (padAt.get(a) ?? grid.point(a)) : grid.point(a),
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
  steps: readonly [string, string, string][],
  start: Vec,
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
  return { points, inside };
}

function lengthsOf(points: Vec[]): number[] {
  const out = [0];
  for (let i = 1; i < points.length; i++) {
    out.push((out[i - 1] as number) + distance(points[i - 1] as Vec, points[i] as Vec));
  }
  return out;
}

function signalFlow(
  grid: Grid,
  nets: Map<string, RoutedNet>,
  parts: Part[],
  source: number,
  segments: FlowSegment[],
  pads: FlowPad[],
): { source: number; length: number } {
  const find = (ref: string) => parts.find((p) => p.spec.ref === ref) as Part;
  const padOn = (ref: string, net: string) => {
    const part = find(ref);
    return (part.footprint.pads[part.spec.pins.indexOf(net)] as Pad).at;
  };
  const main = signalRoute(grid, nets, parts, SIGNAL_PATH, find('IN').spec.at);
  // Finish along the copper to the output pad.
  const out = find('OUT').spec.at;
  const outNet = nets.get('OUT');
  const last = main.points[main.points.length - 1] as Vec;
  const tail = outNet && treePath(outNet, grid.cell(last), grid.cell(out));
  for (const cell of tail?.slice(1, -1) ?? []) {
    main.points.push(grid.point(cell));
    main.inside.push(false);
  }
  main.points.push(out);
  main.inside.push(false);

  const lengths = lengthsOf(main.points);
  const length = lengths[lengths.length - 1] as number;
  const emit = (r: Route, remaining: (i: number) => number) => {
    for (let i = 1; i < r.points.length; i++) {
      const a = r.points[i - 1] as Vec;
      const b = r.points[i] as Vec;
      if (distance(a, b) < 1e-6) continue;
      segments.push({
        a,
        b,
        da: remaining(i - 1),
        db: remaining(i),
        first: source,
        last: source,
        width: r.inside[i - 1] ? WIDTH.body : WIDTH.signal,
      });
    }
    r.points.forEach((at, i) => {
      for (const part of parts) {
        const pad = part.footprint.pads.find((q) => distance(q.at, at) < 1e-6);
        if (pad) pads.push({ ...pad, distance: remaining(i), first: source, last: source });
      }
    });
  };
  emit(main, (i) => length - (lengths[i] as number));

  // The tone stack's high-pass branch, timed to reach the tone pot's wiper
  // together with the main path.
  const fromC3 = padOn('Q3', 'Q3C');
  const wiper = padOn('TONE', 'TONE_WIPER');
  const startAt = main.points.findIndex((p) => distance(p, fromC3) < 1e-6);
  const endAt = main.points.findIndex((p) => distance(p, wiper) < 1e-6);
  if (startAt >= 0 && endAt >= 0) {
    const branch = signalRoute(grid, nets, parts, TONE_BRANCH, fromC3);
    const d0 = length - (lengths[startAt] as number);
    const d1 = length - (lengths[endAt] as number);
    const along = lengthsOf(branch.points);
    const total = along[along.length - 1] || 1;
    emit(branch, (i) => d0 + ((d1 - d0) * (along[i] as number)) / total);
  }
  return { source, length };
}

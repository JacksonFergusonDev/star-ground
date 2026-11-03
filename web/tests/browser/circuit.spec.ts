// The hero board's circuit (src/lib/circuit-field). These checks run in Node, not
// a browser, so they run once, under the chromium project.

import { readFileSync } from 'node:fs';
import { expect, test } from '@playwright/test';
import {
  CLIPPING_STAGES,
  GROUND,
  PARTS,
  SIGNAL_PATH,
  SUPPLY,
  TONE_BRANCH,
} from '../../src/lib/circuit-field/circuit';
import { buildBoard } from '../../src/lib/circuit-field/layout';
import { distance, type Vec } from '../../src/lib/circuit-field/geometry';

test.skip(
  ({ browserName, isMobile }) => browserName !== 'chromium' || isMobile,
  'Runs in Node, so once is enough.',
);

const netlist = readFileSync(
  new URL('../../src/lib/circuit-field/big-muff.cir', import.meta.url),
  'utf8',
);

// The parts between the netlist's board markers, keyed by designator.
function boardSection(): Map<string, { nodes: string[]; value: string }> {
  const start = netlist.indexOf('* --- board ---');
  const end = netlist.indexOf('* --- end board ---');
  const parts = new Map<string, { nodes: string[]; value: string }>();
  for (const line of netlist.slice(start, end).split('\n')) {
    if (!line.trim() || line.startsWith('*')) continue;
    const words = line.trim().split(/\s+/);
    const name = words[0] as string;
    if (name.startsWith('X')) {
      // Subcircuit: X<ref> nodes... POT R=<value>
      const value = (words[words.length - 1] as string).replace('R=', '');
      parts.set(name.slice(1), { nodes: words.slice(1, -2), value });
    } else {
      parts.set(name, { nodes: words.slice(1, -1), value: words[words.length - 1] as string });
    }
  }
  return parts;
}

test('the SPICE netlist matches the board part for part', () => {
  const spice = boardSection();
  const onBoard = PARTS.filter((p) => p.kind !== 'terminal' && p.kind !== 'star');
  expect([...spice.keys()].sort()).toEqual(onBoard.map((p) => p.ref).sort());
  for (const part of onBoard) {
    const nets = part.pins.map((n) => (n === GROUND ? '0' : n));
    // SPICE lists diodes anode first and transistors collector first; the
    // footprints list them cathode first and emitter first.
    const expected =
      part.kind === 'diode' || part.kind === 'transistor'
        ? [...nets].reverse()
        : part.kind === 'resistor' || part.kind === 'film-cap'
          ? [...nets].sort()
          : nets;
    const found = spice.get(part.ref);
    const nodes =
      part.kind === 'resistor' || part.kind === 'film-cap'
        ? [...(found?.nodes ?? [])].sort()
        : found?.nodes;
    expect({ ref: part.ref, nodes, value: found?.value }).toEqual({
      ref: part.ref,
      nodes: expected,
      value: part.value,
    });
  }
});

test('the signal path runs unbroken from the input to the output', () => {
  const pins = new Map(PARTS.map((p) => [p.ref, p.pins]));
  for (const steps of [SIGNAL_PATH, TONE_BRANCH]) {
    for (const [ref, from, to] of steps) {
      expect(pins.get(ref), ref).toEqual(expect.arrayContaining([from, to]));
    }
    for (let i = 1; i < steps.length; i++) {
      expect(steps[i]?.[1], `step ${i}`).toBe(steps[i - 1]?.[2]);
    }
  }
  expect(SIGNAL_PATH[0]?.[1]).toBe('IN');
  expect(SIGNAL_PATH[SIGNAL_PATH.length - 1]?.[2]).toBe('OUT');
});

test('the board routes completely, with few jumpers', () => {
  const board = buildBoard();
  expect(board.failed).toEqual([]);
  // A hand layout of this board needs a handful; more means the placement or
  // the routing order got worse (README.md, "Routing").
  expect(board.jumpers.length).toBeLessThanOrEqual(4);
  // Every ground pin returns to the star along the copper.
  expect(board.ground.length).toBe(
    PARTS.flatMap((p) => p.pins).filter((n) => n === GROUND).length - 1,
  );
  expect(Math.min(...board.ground)).toBeGreaterThan(0);
});

test('copper reaches every pad at its exact centre', () => {
  const board = buildBoard();
  const lines = board.traces.flatMap((trace) =>
    trace.points.slice(1).map((b, i) => ({ a: trace.points[i] as Vec, b })),
  );
  const onLine = (p: Vec, { a, b }: { a: Vec; b: Vec }) =>
    Math.abs(distance(a, p) + distance(p, b) - distance(a, b)) < 1e-6;
  for (const part of board.parts) {
    for (const pad of part.footprint.pads) {
      expect(
        lines.some((line) => onLine(pad.at, line)),
        part.spec.ref,
      ).toBe(true);
    }
  }
});

test('every animated ground return runs continuously from its pad to the star', () => {
  const board = buildBoard();
  board.ground.forEach((length, source) => {
    const pad = board.pads.find((p) => p.first === source && p.last === source);
    expect(pad, `ground source ${source}`).toBeDefined();
    const segments = board.segments
      .filter((s) => s.first <= source && s.last >= source)
      .sort((a, b) => b.da - a.da);
    let at = pad?.at as Vec;
    let remaining = length;
    for (const segment of segments) {
      expect(distance(at, segment.a), `ground source ${source}`).toBeLessThan(1e-6);
      expect(segment.da).toBeCloseTo(remaining, 6);
      expect(segment.da - segment.db).toBeCloseTo(distance(segment.a, segment.b), 6);
      at = segment.b;
      remaining = segment.db;
    }
    expect(at).toEqual(board.star);
    expect(remaining).toBe(0);
  });
});

test('terminal traces approach the centre without a bend at the pad corner', () => {
  const board = buildBoard();
  const vertices = board.traces.flatMap((trace) => trace.points);
  for (const part of board.parts.filter((p) => p.spec.kind === 'terminal')) {
    for (const pad of part.footprint.pads) {
      const near = vertices.filter(
        (p) => Math.abs(p.x - pad.at.x) <= pad.radius && Math.abs(p.y - pad.at.y) <= pad.radius,
      );
      expect(near.length, part.spec.ref).toBeGreaterThan(0);
      for (const p of near) expect(distance(p, pad.at), part.spec.ref).toBeLessThan(1e-6);
    }
  }
});

test('clipping pulses follow connected feedback loops and forward-biased diodes', () => {
  const board = buildBoard();
  for (const [i, stage] of CLIPPING_STAGES.entries()) {
    const transistor = board.parts.find((p) => p.spec.ref === stage.transistor)!;
    const feedback = board.feedback[i]!;
    expect(feedback.offset).toBeGreaterThan(0);
    expect(feedback.offset).toBeLessThan(board.signal.length);
    for (const [steps, path] of [
      [stage.forward, feedback.forward],
      [stage.reverse, feedback.reverse],
    ] as const) {
      for (const [j, [ref, from, to]] of steps.entries()) {
        const part = PARTS.find((p) => p.ref === ref)!;
        expect(part.pins).toEqual(expect.arrayContaining([from, to]));
        if (part.kind === 'diode') expect([from, to]).toEqual([...part.pins].reverse());
        if (j > 0) expect(from).toBe(steps[j - 1]?.[2]);
      }
      const start = transistor.footprint.pads[transistor.spec.pins.indexOf(steps[0]![1])]!.at;
      const end =
        transistor.footprint.pads[transistor.spec.pins.indexOf(steps[steps.length - 1]![2])]!.at;
      const segments = board.segments
        .filter((s) => s.first === path.source)
        .sort((a, b) => b.da - a.da);
      let at = start;
      let remaining = path.length;
      for (const segment of segments) {
        expect(segment.a).toEqual(at);
        expect(segment.da).toBeCloseTo(remaining, 6);
        expect(segment.da - segment.db).toBeCloseTo(distance(segment.a, segment.b), 6);
        at = segment.b;
        remaining = segment.db;
      }
      expect(at).toEqual(end);
      expect(remaining).toBe(0);
    }
    const collector = transistor.footprint.pads[2]!.at;
    const main = board.pads.find(
      (p) => p.first === board.signal.source && distance(p.at, collector) < 1e-6,
    )!;
    expect(board.signal.length - main.distance).toBeCloseTo(feedback.offset, 6);
  }
});

test('supply pulses leave 9V and cross every supply resistor', () => {
  const board = buildBoard();
  const source = board.supply.source;
  const supply = board.segments.filter((s) => s.first === source);
  const start = board.parts.find((p) => p.spec.ref === '9V')!.spec.at;
  const startPad = board.pads.find((p) => p.first === source && distance(p.at, start) < 1e-6)!;
  expect(startPad.distance).toBeCloseTo(board.supply.length, 6);
  for (const segment of supply) {
    expect(Math.abs(segment.da - segment.db)).toBeCloseTo(distance(segment.a, segment.b), 6);
    expect(Math.min(segment.da, segment.db)).toBeGreaterThanOrEqual(0);
  }
  for (const part of board.parts.filter(
    (p) => p.spec.pins.includes(SUPPLY) && p.spec.kind === 'resistor',
  )) {
    const pin = part.spec.pins.indexOf(SUPPLY);
    const a = part.footprint.pads[pin]!.at;
    const b = part.footprint.pads[1 - pin]!.at;
    const body = supply.find((s) => distance(s.a, a) < 1e-6 && distance(s.b, b) < 1e-6)!;
    expect(body, part.spec.ref).toBeDefined();
    expect(body.da).toBeGreaterThan(body.db);
  }
  const ids = [
    board.signal.source,
    source,
    ...board.feedback.flatMap((f) => [f.forward.source, f.reverse.source]),
  ];
  expect(new Set(ids).size).toBe(ids.length);
  expect(Math.min(...ids)).toBe(board.ground.length);
});

test('new pulses stay on copper, jumpers, or between a part’s own pads', () => {
  const board = buildBoard();
  const lines = [
    ...board.traces.flatMap((trace) =>
      trace.points.slice(1).map((b, i) => ({ a: trace.points[i]!, b })),
    ),
    ...board.jumpers,
  ];
  const onLine = (p: Vec, { a, b }: { a: Vec; b: Vec }) =>
    Math.abs(distance(a, p) + distance(p, b) - distance(a, b)) < 1e-6;
  for (const s of board.segments.filter((s) => s.first > board.signal.source)) {
    const throughPart = board.parts.some(
      ({ footprint: { pads } }) =>
        pads.some((p) => distance(p.at, s.a) < 1e-6) &&
        pads.some((p) => distance(p.at, s.b) < 1e-6),
    );
    if (throughPart) continue;
    for (const t of [0, 0.25, 0.5, 0.75, 1]) {
      const p = { x: s.a.x + (s.b.x - s.a.x) * t, y: s.a.y + (s.b.y - s.a.y) * t };
      expect(
        lines.some((line) => onLine(p, line)),
        JSON.stringify(p),
      ).toBe(true);
    }
  }
});

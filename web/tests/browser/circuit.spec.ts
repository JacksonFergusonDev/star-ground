// The hero board's circuit (src/lib/circuit-field). These checks run in Node, not
// a browser, so they run once, under the chromium project.

import { readFileSync } from 'node:fs';
import { expect, test } from '@playwright/test';
import { GROUND, PARTS, SIGNAL_PATH, TONE_BRANCH } from '../../src/lib/circuit-field/circuit';
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

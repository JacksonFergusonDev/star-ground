// The hero board's circuit (src/lib/star-field). These checks run in Node, not
// a browser, so they run once, under the chromium project.

import { readFileSync } from 'node:fs';
import { expect, test } from '@playwright/test';
import { GROUND, PARTS, SIGNAL_PATH, TONE_BRANCH } from '../../src/lib/star-field/circuit';
import { buildBoard } from '../../src/lib/star-field/layout';

test.skip(
  ({ browserName, isMobile }) => browserName !== 'chromium' || isMobile,
  'Runs in Node, so once is enough.',
);

const netlist = readFileSync(
  new URL('../../src/lib/star-field/big-muff.cir', import.meta.url),
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

// The Big Muff π fuzz: an input booster, two clipping stages, a passive tone
// stack, and an output booster. The netlist and values are the early version
// drawn in J.L., "A Case Study: Re-Engineering the Big Muff π" (2014,
// guitarscience.net), renumbered here in signal order as a board's silkscreen
// would be. The layout is Star Ground's own: a single-sided board on a 0.1 inch
// grid, with every ground return wired to one star ground.

import type { PartKind } from './footprints';
import { vec, type Vec } from './geometry';

/** Board size in units (0.1 inch). */
export const BOARD = { width: 64, height: 40 } as const;

export type Side = 'n' | 's' | 'e' | 'w';

export interface PartSpec {
  ref: string;
  kind: PartKind;
  value: string;
  at: Vec;
  /** Quarter turns clockwise; a resistor turned once stands with pin 1 up. */
  turns: number;
  /** The net on each pad, in pin order. */
  pins: string[];
  /** Where the designator sits; defaults to east of upright parts, north of others. */
  label?: Side;
}

export const GROUND = 'GND';
export const SUPPLY = 'VCC';

const parts: PartSpec[] = [];
const add = (
  ref: string,
  kind: PartKind,
  value: string,
  x: number,
  y: number,
  turns: number,
  pins: string[],
  label?: Side,
) => parts.push({ ref, kind, value, at: vec(x, y), turns, pins, label });

// Off-board wiring: the jacks and battery land on these pads; the jacks'
// sleeves land on the star itself.
add('IN', 'terminal', '', 1.5, 26, 0, ['IN'], 's');
add('9V', 'terminal', '', 1.5, 4, 0, [SUPPLY], 's');
add('OUT', 'terminal', '', 62, 36.5, 0, ['OUT'], 'n');
add('STAR GND', 'star', '', 29.5, 28.5, 0, [GROUND], 's');

// The three transistor stages share one shape: collector-to-base feedback
// (RB1 and CF), base bias to ground (RB2), and a small emitter resistor (RE).
interface Stage {
  n: number;
  ox: number;
  refs: { rb1: string; cf: string; rb2: string; rc: string; re: string; q: string };
}

function stage({ n, ox, refs }: Stage) {
  const b = `Q${n}B`;
  const c = `Q${n}C`;
  const e = `Q${n}E`;
  add(refs.q, 'transistor', 'BC549C', ox, 18, 0, [e, b, c], 's');
  add(refs.rc, 'resistor', '47k', ox + 1, 10, 1, [SUPPLY, c]);
  add(refs.rb1, 'resistor', '470k', ox - 3, 13, 0, [b, c]);
  add(refs.cf, 'film-cap', '470p', ox - 3, 15, 0, [b, c], 's');
  add(refs.rb2, 'resistor', '100k', ox - 3, 23, 1, [b, GROUND], 'w');
  add(refs.re, 'resistor', '100', ox - 1, 23, 1, [e, GROUND]);
}

// Input booster.
add('R1', 'resistor', '39k', 3, 21, 1, ['Q1IN', 'IN'], 'e');
add('C1', 'electrolytic', '1u', 5, 18, 2, ['Q1B', 'Q1IN'], 'n');
stage({
  n: 1,
  ox: 10,
  refs: { rb1: 'R2', cf: 'C2', rb2: 'R3', rc: 'R4', re: 'R5', q: 'Q1' },
});
add('C3', 'electrolytic', '1u', 13, 14, 1, ['Q1C', 'SUS_TOP'], 'e');
add('SUSTAIN', 'pot', '100k', 16, 32, 2, [GROUND, 'SUS_WIPER', 'SUS_TOP'], 'n');
add('C4', 'electrolytic', '1u', 17, 26, 1, ['Q2IN', 'SUS_WIPER'], 'e');

// First clipping stage.
add('R6', 'resistor', '10k', 18, 18, 0, ['Q2IN', 'Q2B'], 's');
stage({
  n: 2,
  ox: 24,
  refs: { rb1: 'R7', cf: 'C5', rb2: 'R8', rc: 'R9', re: 'R10', q: 'Q2' },
});
add('C6', 'film-cap', '1u', 17, 9, 1, ['CLIP2', 'Q2B'], 'w');
add('D1', 'diode', '1N914', 21, 6, 0, ['CLIP2', 'Q2C'], 'n');
add('D2', 'diode', '1N914', 21, 4, 2, ['Q2C', 'CLIP2'], 'n');
add('C7', 'electrolytic', '1u', 27, 14, 1, ['Q2C', 'Q3IN'], 'e');

// Second clipping stage.
add('R11', 'resistor', '10k', 32, 18, 0, ['Q3IN', 'Q3B'], 's');
stage({
  n: 3,
  ox: 38,
  refs: { rb1: 'R12', cf: 'C8', rb2: 'R13', rc: 'R14', re: 'R15', q: 'Q3' },
});
add('C9', 'film-cap', '1u', 31, 9, 1, ['CLIP3', 'Q3B'], 'w');
add('D3', 'diode', '1N914', 35, 6, 0, ['CLIP3', 'Q3C'], 'n');
add('D4', 'diode', '1N914', 35, 4, 2, ['Q3C', 'CLIP3'], 'n');

// Tone stack: a low-pass (R16, C10) and a high-pass (C11, R17), blended by the
// tone pot.
add('R16', 'resistor', '27k', 43, 12, 0, ['Q3C', 'TONE_LP'], 'n');
add('C10', 'film-cap', '10n', 46, 16, 1, ['TONE_LP', GROUND], 'e');
add('C11', 'film-cap', '4n7', 43, 9, 0, ['Q3C', 'TONE_HP'], 'n');
add('R17', 'resistor', '27k', 48, 12, 1, ['TONE_HP', GROUND], 'e');
add('TONE', 'pot', '100k', 43, 32, 2, ['TONE_LP', 'TONE_WIPER', 'TONE_HP'], 'n');

// Output booster.
add('C12', 'electrolytic', '1u', 50, 22, 1, ['Q4B', 'TONE_WIPER'], 'w');
add('Q4', 'transistor', 'BC549C', 54, 18, 0, ['Q4E', 'Q4B', 'Q4C'], 's');
add('R18', 'resistor', '470k', 52, 10, 1, [SUPPLY, 'Q4B'], 'w');
add('R19', 'resistor', '100k', 52, 23, 1, ['Q4B', GROUND], 'e');
add('R20', 'resistor', '10k', 56, 10, 1, [SUPPLY, 'Q4C']);
add('R21', 'resistor', '2.2k', 56, 23, 1, ['Q4E', GROUND]);
add('C13', 'electrolytic', '1u', 59, 14, 1, ['Q4C', 'VOL_TOP'], 'e');
add('VOLUME', 'pot', '500k', 55, 32, 2, ['VOL_TOP', 'OUT', GROUND], 'n');

export const PARTS: readonly PartSpec[] = parts;

/**
 * The guitar's path through the circuit, from the input jack to the output:
 * each step enters a part on one net and leaves it on another.
 */
export const SIGNAL_PATH: readonly [ref: string, from: string, to: string][] = [
  ['R1', 'IN', 'Q1IN'],
  ['C1', 'Q1IN', 'Q1B'],
  ['Q1', 'Q1B', 'Q1C'],
  ['C3', 'Q1C', 'SUS_TOP'],
  ['SUSTAIN', 'SUS_TOP', 'SUS_WIPER'],
  ['C4', 'SUS_WIPER', 'Q2IN'],
  ['R6', 'Q2IN', 'Q2B'],
  ['Q2', 'Q2B', 'Q2C'],
  ['C7', 'Q2C', 'Q3IN'],
  ['R11', 'Q3IN', 'Q3B'],
  ['Q3', 'Q3B', 'Q3C'],
  ['R16', 'Q3C', 'TONE_LP'],
  ['TONE', 'TONE_LP', 'TONE_WIPER'],
  ['C12', 'TONE_WIPER', 'Q4B'],
  ['Q4', 'Q4B', 'Q4C'],
  ['C13', 'Q4C', 'VOL_TOP'],
  ['VOLUME', 'VOL_TOP', 'OUT'],
];

/** The tone stack's high-pass branch, which rejoins the path at the tone pot. */
export const TONE_BRANCH: readonly [ref: string, from: string, to: string][] = [
  ['C11', 'Q3C', 'TONE_HP'],
  ['TONE', 'TONE_HP', 'TONE_WIPER'],
];

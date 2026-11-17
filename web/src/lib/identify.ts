// Reading a part in the hand: a resistor's colour bands, and the code printed
// on a capacitor. Each gives the value as a parts list writes it ('4.7k',
// '100n'), so a part identified here can go straight into the stock.

import type { Band } from './parts';
import { DIGITS } from './parts';

/** A colour's name as a builder says it. */
export const COLOUR_NAMES: Record<Band, string> = {
  black: 'Black',
  brown: 'Brown',
  red: 'Red',
  orange: 'Orange',
  yellow: 'Yellow',
  green: 'Green',
  blue: 'Blue',
  violet: 'Violet',
  grey: 'Grey',
  white: 'White',
  gold: 'Gold',
  silver: 'Silver',
};

/** The power of ten each multiplier band stands for. */
export const MULTIPLIERS: Partial<Record<Band, number>> = {
  ...Object.fromEntries(DIGITS.map((band, i) => [band, i])),
  gold: -1,
  silver: -2,
};

/** The tolerance each tolerance band stands for, in percent. */
export const TOLERANCES: Partial<Record<Band, number>> = {
  brown: 1,
  red: 2,
  green: 0.5,
  blue: 0.25,
  violet: 0.1,
  grey: 0.05,
  gold: 5,
  silver: 10,
};

/** Every colour, in the order of the colour code. */
export const COLOURS: Band[] = [...DIGITS, 'gold', 'silver'];

const PREFIXES: [number, string][] = [
  [1e6, 'M'],
  [1e3, 'k'],
  [1, ''],
];

/** A number without a float's trailing noise: 4.7000000000000002 is '4.7'. */
const plain = (n: number): string => String(Number(n.toPrecision(6)));

/** A resistance as a parts list writes it: 4700 is '4.7k', 220 is '220', 0.47 is '0.47'. */
function ohms(value: number): string {
  for (const [size, prefix] of PREFIXES)
    if (value >= size || size === 1) return `${plain(value / size)}${prefix}`;
  return plain(value);
}

/** A resistor's bands, read left to right: its digits, multiplier and tolerance. */
export interface Bands {
  /** Two digits on a four-band resistor, three on a five-band one. */
  digits: Band[];
  multiplier: Band;
  /** None on a 20% resistor, which has no tolerance band. */
  tolerance: Band | null;
}

/** A resistor's value from its bands, in ohms and as a parts list writes it ('1.5k'). */
export function readBands(bands: Bands): { ohms: number; value: string; tolerance: number } {
  const number = Number(bands.digits.map((b) => DIGITS.indexOf(b)).join(''));
  const resistance = Number((number * 10 ** (MULTIPLIERS[bands.multiplier] ?? 0)).toPrecision(6));
  const tolerance = bands.tolerance === null ? 20 : (TOLERANCES[bands.tolerance] ?? 20);
  return { ohms: resistance, value: ohms(resistance), tolerance };
}

/** A capacitance as a parts list writes it: 1e-7 is '100n', 4.7e-9 is '4.7n'. */
function farads(value: number): string {
  for (const [size, prefix] of [
    [1e-6, 'u'],
    [1e-9, 'n'],
    [1e-12, 'p'],
  ] as const)
    if (value >= size * 0.999) return `${plain(value / size)}${prefix}`;
  return `${plain(value / 1e-12)}p`;
}

/** The tolerance letters printed after a capacitor's code. */
export const CAP_TOLERANCES: Record<string, string> = {
  B: '±0.1 pF',
  C: '±0.25 pF',
  D: '±0.5 pF',
  F: '±1%',
  G: '±2%',
  J: '±5%',
  K: '±10%',
  M: '±20%',
  Z: '+80% / −20%',
};

/** The voltage codes printed before the value on some film capacitors ('2A104J'). */
export const VOLTAGE_CODES: Record<string, number> = {
  '0J': 6.3,
  '1A': 10,
  '1C': 16,
  '1E': 25,
  '1V': 35,
  '1H': 50,
  '1J': 63,
  '2A': 100,
  '2D': 200,
  '2E': 250,
  '2G': 400,
  '2J': 630,
};

/** What a capacitor's printing says. */
export interface CapReading {
  farads: number;
  value: string;
  tolerance: string | null;
  volts: number | null;
  /** How the printing was read, in words. */
  how: string;
}

const PREFIX_POWER: Record<string, number> = { p: -12, n: -9, u: -6, µ: -6 };

/**
 * Reads what's printed on a capacitor: a three-digit code ('104', '473J'), a
 * value with its prefix ('4n7', '.1', 'u1', '100nF', '0.1µF'), and around it
 * a voltage code ('2A') or rating ('100V') and a tolerance letter. Null when
 * the printing doesn't read as a capacitance.
 */
export function readCapCode(text: string): CapReading | null {
  let rest = text.trim().replace(/\s+/g, ' ');
  if (!rest) return null;
  let volts: number | null = null;
  const code = /^([0-2][ACDEGHJV])\s?(?=\d)/i.exec(rest);
  if (code) {
    volts = VOLTAGE_CODES[(code[1] ?? '').toUpperCase()] ?? null;
    rest = rest.slice(code[0].length);
  }
  const rating = /\s?(\d+(?:\.\d+)?)\s?V(?:DC)?$/i.exec(rest);
  if (rating) {
    volts = Number(rating[1]);
    rest = rest.slice(0, rating.index);
  }
  let tolerance: string | null = null;
  const letter = /([BCDFGJKMZ])$/i.exec(rest);
  // A trailing letter that's a prefix ('47n') isn't a tolerance.
  if (letter && /\d\s?[BCDFGJKMZ]$/i.test(rest) && !/[pnuµ]$/.test(rest)) {
    tolerance = CAP_TOLERANCES[(letter[1] ?? '').toUpperCase()] ?? null;
    rest = rest.slice(0, -1).trim();
  }

  let value: number;
  let how: string;
  const digits = /^(\d)(\d)(\d)$/.exec(rest);
  const prefixed = /^(\d*)[.,]?(\d*)\s?([pnuµ])(\d*)\s?F?$/i.exec(rest);
  if (digits) {
    const [, a = '', b = '', zeros = ''] = digits;
    const z = Number(zeros);
    // 8 and 9 divide instead: 0.01 and 0.1 times the first two digits.
    const multiplier = z === 8 ? 0.01 : z === 9 ? 0.1 : 10 ** z;
    if (z === 6 || z === 7) return null;
    value = Number(`${a}${b}`) * multiplier * 1e-12;
    how =
      z >= 8
        ? `${a}${b} times ${multiplier} picofarads`
        : `${a}${b} followed by ${z} zero${z === 1 ? '' : 's'}, in picofarads`;
  } else if (prefixed) {
    const [, whole = '', fraction = '', prefix = '', after = ''] = prefixed;
    if (!whole && !fraction && !after) return null;
    const lower = prefix === 'µ' ? 'u' : prefix.toLowerCase();
    const number = Number(`${whole || '0'}.${fraction || after || '0'}`);
    value = number * 10 ** (PREFIX_POWER[lower] ?? 0);
    how = after
      ? `the letter ${lower} marks the decimal point`
      : `${plain(number)} ${lower === 'u' ? 'micro' : lower === 'n' ? 'nano' : 'pico'}farads`;
  } else if (/^\d*[.,]\d+$/.test(rest)) {
    // A bare decimal ('.1', '0.047') is in microfarads, as film capacitors print it.
    value = Number(rest.replace(',', '.')) * 1e-6;
    how = 'a decimal with no unit is in microfarads';
  } else if (/^\d{1,2}$/.test(rest)) {
    value = Number(rest) * 1e-12;
    how = 'one or two digits alone are picofarads';
  } else return null;
  if (!(value > 0)) return null;
  return { farads: value, value: farads(value), tolerance, volts, how };
}

/** A capacitance in all three units a builder meets: '100 nF = 0.1 µF = 100,000 pF'. */
export function capUnits(value: number): string {
  const pf = Number((value / 1e-12).toPrecision(6));
  const nf = Number((value / 1e-9).toPrecision(6));
  const uf = Number((value / 1e-6).toPrecision(6));
  const grouped = new Intl.NumberFormat('en-US', { maximumFractionDigits: 6 });
  return `${grouped.format(pf)} pF = ${grouped.format(nf)} nF = ${grouped.format(uf)} µF`;
}

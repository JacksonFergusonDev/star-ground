// What a part's value says to a builder at the bench: its value written the
// way they prefer (4.7k or 4k7), a pot's taper, a resistor's colour bands, and
// the drawing for its kind.
// The API sends values in decimal form ('4.7k', '100n', 'B100K'); everything
// here reads that text and never changes what was read.

import type { BuildStage, ComponentCategory } from './api';

/** How a value is written: '4.7k', or the European '4k7' (IEC 60062's RKM code). */
export type Notation = 'decimal' | 'rkm';

/** How many bands a resistor shows: 4 (two digits, 5%) or 5 (three digits, 1%). */
export type BandCount = 4 | 5;

// Categories whose values are numbers with a unit prefix.
const NUMERIC = new Set<string>(['Resistors', 'Capacitors', 'Inductors', 'Potentiometers']);

// A number with an optional SI prefix, written either way: '4.7k', '4k7',
// '390R', '100n', '4u7'. Lowercase 'm' is left out: in parts lists it means
// mega as often as milli.
const VALUE = /^(\d+)(?:[.,](\d+))?\s*([pnuµkKMRr]?)(\d*)$/;

const POWER: Record<string, number> = {
  p: -12,
  n: -9,
  u: -6,
  µ: -6,
  '': 0,
  R: 0,
  r: 0,
  k: 3,
  K: 3,
  M: 6,
};

/** A value read as its digits and prefix: '4.7k' is whole '4', fraction '7', prefix 'k'. */
interface Reading {
  whole: string;
  fraction: string;
  prefix: string;
}

function read(text: string): Reading | null {
  const match = VALUE.exec(text.trim());
  if (!match) return null;
  const [, whole = '', decimals = '', prefix = '', after = ''] = match;
  // A value written both ways at once ('4.7k7') isn't a value.
  if (decimals && after) return null;
  // A digit after no prefix ('47 5') isn't either.
  if (after && !prefix) return null;
  return { whole, fraction: decimals || after, prefix: prefix === 'µ' ? 'u' : prefix };
}

/** A pot's taper and the rest of its value: 'B100K' is B and '100K'. */
export interface Pot {
  taper: Taper | null;
  value: string;
  /** What follows the value, such as 'DUAL'. */
  rest: string;
}

/** A pot's taper: A audio (log), B linear, C reverse audio. */
export type Taper = 'A' | 'B' | 'C';

/** Reads a pot's value, with its taper before or after the number ('B100K', '100kB'). */
export function readPot(text: string): Pot {
  const match = /^([ABC])?\s*(\d+(?:[.,]\d+)?\s*[kKMRr]?\d*)\s*([ABC])?\b\s*(.*)$/.exec(
    text.trim(),
  );
  if (!match) return { taper: null, value: text, rest: '' };
  const [, before, value = '', after, rest = ''] = match;
  return { taper: (before ?? after ?? null) as Taper | null, value, rest };
}

const TAPERS: Record<Taper, string> = {
  A: 'Audio taper (logarithmic)',
  B: 'Linear taper',
  C: 'Reverse audio taper',
};

/** A taper in words, for its drawing's label. */
export const taperName = (taper: Taper): string => TAPERS[taper];

/** Writes a numeric value in the given notation; anything else comes back as given. */
function write(text: string, notation: Notation, unitless: 'R' | ''): string {
  const value = read(text);
  if (!value) return text;
  if (notation === 'decimal') {
    const prefix = value.prefix === 'R' || value.prefix === 'r' ? '' : value.prefix;
    const k = prefix === 'K' ? 'k' : prefix;
    return `${value.whole}${value.fraction ? `.${value.fraction}` : ''}${k}`;
  }
  const letter = value.prefix === 'K' ? 'k' : value.prefix === 'r' ? 'R' : value.prefix;
  return `${value.whole}${letter || unitless}${value.fraction}`;
}

/**
 * A part's value as the builder prefers to read it. Only resistors,
 * capacitors, inductors and pots change; a transistor's '2N5088' doesn't.
 */
export function formatValue(value: string, category: string, notation: Notation): string {
  if (notation === 'decimal' || !NUMERIC.has(category)) return value;
  if (category === 'Potentiometers') {
    const pot = readPot(value);
    if (!pot.taper) return write(value, notation, 'R');
    const number = write(pot.value, notation, 'R');
    return [`${pot.taper}${number}`, pot.rest].filter(Boolean).join(' ');
  }
  return write(value, notation, category === 'Resistors' ? 'R' : '');
}

/** A resistor band's colour, by the digit it stands for (-2 silver to 9 white). */
type Band =
  | 'black'
  | 'brown'
  | 'red'
  | 'orange'
  | 'yellow'
  | 'green'
  | 'blue'
  | 'violet'
  | 'grey'
  | 'white'
  | 'gold'
  | 'silver';

const DIGITS: Band[] = [
  'black',
  'brown',
  'red',
  'orange',
  'yellow',
  'green',
  'blue',
  'violet',
  'grey',
  'white',
];

const MULTIPLIER = (power: number): Band | null =>
  power === -2 ? 'silver' : power === -1 ? 'gold' : (DIGITS[power] ?? null);

/**
 * A resistor's colour bands: its digits, the multiplier, then the tolerance
 * (gold for 5% on four bands, brown for 1% on five). Null when the value
 * isn't a resistance, or needs more digits than the bands hold.
 */
export function bands(value: string, count: BandCount): Band[] | null {
  const reading = read(value);
  if (!reading) return null;
  const power = POWER[reading.prefix];
  if (power === undefined || power < 0) return null;
  let digits = `${reading.whole}${reading.fraction}`.replace(/^0+/, '');
  let exponent = power - reading.fraction.length;
  if (!digits) return null;
  while (digits.length > 1 && digits.endsWith('0')) {
    digits = digits.slice(0, -1);
    exponent += 1;
  }
  const significant = count - 2;
  if (digits.length > significant) return null;
  while (digits.length < significant) {
    digits += '0';
    exponent -= 1;
  }
  const multiplier = MULTIPLIER(exponent);
  if (!multiplier) return null;
  return [
    ...[...digits].map((d) => DIGITS[Number(d)] as Band),
    multiplier,
    count === 4 ? 'gold' : 'brown',
  ];
}

/** The drawings a part can carry, one per kind of part. */
export type PartKind =
  | 'resistor'
  | 'capacitor'
  | 'electrolytic'
  | 'diode'
  | 'led'
  | 'transistor'
  | 'chip'
  | 'socket'
  | 'pot'
  | 'switch'
  | 'crystal'
  | 'inductor'
  | 'transformer'
  | 'relay'
  | 'board'
  | 'hardware'
  | 'other';

const BY_CATEGORY: Record<ComponentCategory, PartKind> = {
  Resistors: 'resistor',
  Capacitors: 'capacitor',
  Potentiometers: 'pot',
  Switches: 'switch',
  Diodes: 'diode',
  Transistors: 'transistor',
  'Crystals/Oscillators': 'crystal',
  Inductors: 'inductor',
  Transformers: 'transformer',
  Relays: 'relay',
  'Hardware/Misc': 'hardware',
  ICs: 'chip',
  Optoelectronics: 'led',
  PCB: 'board',
};

/** The drawing for a shopping list category ('Resistors'); 'other' for one it doesn't know. */
export const kindOfCategory = (category: string): PartKind =>
  BY_CATEGORY[category as ComponentCategory] ?? 'other';

const BY_STAGE: Record<BuildStage, PartKind> = {
  PCB: 'board',
  Resistors: 'resistor',
  Diodes: 'diode',
  Sockets: 'socket',
  'Crystals and oscillators': 'crystal',
  'Small capacitors': 'capacitor',
  Inductors: 'inductor',
  Transistors: 'transistor',
  'Electrolytic capacitors': 'electrolytic',
  Relays: 'relay',
  Switches: 'switch',
  Transformers: 'transformer',
  Potentiometers: 'pot',
  Hardware: 'hardware',
  Chips: 'chip',
  'Other parts': 'other',
};

/** The drawing for a build stage, which tells electrolytics and sockets apart. */
export const kindOfStage = (stage: BuildStage): PartKind => BY_STAGE[stage];

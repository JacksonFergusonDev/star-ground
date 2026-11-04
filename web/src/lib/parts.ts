// What a part's value says to a builder at the bench: its value written the
// way they prefer (4.7k or 4k7), a pot's taper, and the drawing for its kind.
// The API sends values in decimal form ('4.7k', '100n', 'B100K'); everything
// here reads that text and never changes what was read.

import type { ComponentCategory } from './api';

/** How a value is written: '4.7k', or the European '4k7' (IEC 60062's RKM code). */
export type Notation = 'decimal' | 'rkm';

// Categories whose values are numbers with a unit prefix.
const NUMERIC = new Set<string>(['Resistors', 'Capacitors', 'Inductors', 'Potentiometers']);

// A number with an optional SI prefix, written either way: '4.7k', '4k7',
// '390R', '100n', '4u7'. Lowercase 'm' is left out: in parts lists it means
// mega as often as milli.
const VALUE = /^(\d+)(?:[.,](\d+))?\s*([pnuµkKMRr]?)(\d*)$/;

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
  Unknown: 'other',
  PCB: 'board',
};

/** The drawing for a shopping list category ('Resistors'); 'other' for one it doesn't know. */
export const kindOfCategory = (category: string): PartKind =>
  BY_CATEGORY[category as ComponentCategory] ?? 'other';

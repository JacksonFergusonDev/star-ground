// The builder's stock as rows the app can edit: each part's category, its
// value and how many they have. The stock travels as CSV text (columns
// Category, Part, Qty), the same file a builder uploads or downloads, so the
// counting pages read it into rows, change them and write it back.
//
// The server reads the values: it normalizes '4K7' to '4.7k' and matches a
// stock row to the parts lists' rows ignoring case. Here a row is matched to
// a part by the same rules, so changing a count replaces the rows it means
// instead of adding a second row the server would add to it.

import type { ComponentCategory } from './api';
import { magnitude } from './parts';
import type { Stock } from './server';

/** One row of the stock: a part and how many the builder has. */
export interface StockRow {
  category: string;
  part: string;
  qty: number;
}

/** The name a stock counted in the app goes by, until the builder downloads it. */
const COUNTED = 'Counted in Star Ground';

// Categories whose values are numbers the server normalizes ('4k7' is '4.7k').
const NUMERIC = new Set<string>(['Resistors', 'Capacitors']);

/** Splits a CSV or TSV line, minding quoted fields. */
function cells(line: string, separator: string): string[] {
  const out: string[] = [];
  let cell = '';
  let quoted = false;
  for (const ch of line) {
    if (ch === '"') quoted = !quoted;
    else if (ch === separator && !quoted) {
      out.push(cell.trim());
      cell = '';
    } else cell += ch;
  }
  out.push(cell.trim());
  return out;
}

/**
 * The rows of a stock CSV, in its order. A row without a part, or whose count
 * isn't a whole number, is left out, as the server leaves it out.
 */
export function readStock(text: string): StockRow[] {
  const lines = text.split(/\r?\n/).filter((l) => l.trim());
  const first = lines[0] ?? '';
  // Spreadsheets separate columns with commas, tabs, or semicolons where '4,7' is 4.7.
  const separator = first.includes('\t') ? '\t' : first.includes(';') ? ';' : ',';
  const head = cells(first, separator).map((h) => h.toLowerCase());
  const [category, part, qty] = ['category', 'part', 'qty'].map((name) => head.indexOf(name));
  if (part === undefined || part < 0) return [];
  return lines.slice(1).flatMap((line) => {
    const row = cells(line, separator);
    const name = row[part] ?? '';
    const count = Number(qty !== undefined && qty >= 0 ? row[qty] : '');
    if (!name || !Number.isInteger(count)) return [];
    return [{ category: (category !== undefined && row[category]) || '', part: name, qty: count }];
  });
}

/** A cell as CSV writes it: quoted when it holds a comma, quote or line break. */
function cell(text: string): string {
  return /[",\r\n]/.test(text) ? `"${text.replaceAll('"', '""')}"` : text;
}

/** The rows as a stock CSV, header first. */
function writeStock(rows: StockRow[]): string {
  const lines = rows.map((r) => [r.category, r.part, String(r.qty)].map(cell).join(','));
  return ['Category,Part,Qty', ...lines].join('\n') + '\n';
}

/** Whether two numbers are the same value, allowing for floating point ('100n' and '0.1u'). */
const close = (a: number, b: number): boolean => Math.abs(a - b) <= Math.abs(a) * 1e-9;

/**
 * Whether two values in a category are the same part: equal ignoring case,
 * or for resistors and capacitors the same number written another way
 * ('4k7' and '4.7k'), with the same words after it ('100u 25V').
 */
export function samePart(category: string, a: string, b: string): boolean {
  const [x, y] = [a.trim().toLowerCase(), b.trim().toLowerCase()];
  if (x === y) return true;
  if (!NUMERIC.has(category)) return false;
  const [xHead = '', ...xRest] = a.trim().split(/\s+/);
  const [yHead = '', ...yRest] = b.trim().split(/\s+/);
  if (xRest.join(' ').toLowerCase() !== yRest.join(' ').toLowerCase()) return false;
  const [m, n] = [magnitude(unitless(xHead)), magnitude(unitless(yHead))];
  return m !== null && n !== null && close(m, n);
}

/** A value without its unit, as the server reads it: '100nF' is '100n', '10kΩ' is '10k'. */
function unitless(head: string): string {
  return head.replace(/(?:f|ohms?|Ω)$/i, '');
}

/** Whether a row is the part: the same value, as `samePart` reads it. */
const means = (row: StockRow, category: string, part: string): boolean =>
  row.category === category && samePart(category, row.part, part);

/** How many of a part the rows hold, adding up every row that means it. */
export function have(rows: StockRow[], category: string, part: string): number {
  return rows.filter((r) => means(r, category, part)).reduce((sum, r) => sum + r.qty, 0);
}

/**
 * The rows with the builder's count of a part: every row that means it is
 * replaced by one row, written as given, or none when the count is 0.
 */
export function setHave(rows: StockRow[], category: string, part: string, qty: number): StockRow[] {
  const others = rows.filter((r) => !means(r, category, part));
  const at = rows.findIndex((r) => means(r, category, part));
  if (qty <= 0) return others;
  const row = { category, part, qty };
  // A part already listed keeps its place, so the file reads in the builder's order.
  if (at < 0) return [...others, row];
  const before = rows.slice(0, at).filter((r) => others.includes(r)).length;
  return [...others.slice(0, before), row, ...others.slice(before)];
}

/** The stock with these rows, keeping its name; none when there are no rows. */
export function withRows(stock: Stock | null, rows: StockRow[]): Stock | null {
  if (!rows.length) return null;
  return { name: stock?.name ?? COUNTED, text: writeStock(rows) };
}

/**
 * Saves the stock to the builder's computer as the CSV it is, under its own
 * name, so counts typed into this browser survive it being cleared.
 */
export function downloadStock(stock: Stock): void {
  const name = stock.name.toLowerCase().endsWith('.csv') ? stock.name : 'stock.csv';
  const url = URL.createObjectURL(new Blob([stock.text], { type: 'text/csv' }));
  const link = document.createElement('a');
  link.href = url;
  link.download = name;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

/** The categories a builder can list a part under, as the parts lists use them. */
export const CATEGORIES: ComponentCategory[] = [
  'Resistors',
  'Capacitors',
  'Potentiometers',
  'Diodes',
  'Transistors',
  'ICs',
  'Optoelectronics',
  'Switches',
  'Inductors',
  'Transformers',
  'Relays',
  'Crystals/Oscillators',
  'Hardware/Misc',
];

/** The rows with more of a part: a count added to what the rows already hold. */
export function addHave(rows: StockRow[], category: string, part: string, qty: number): StockRow[] {
  return setHave(rows, category, part, have(rows, category, part) + qty);
}

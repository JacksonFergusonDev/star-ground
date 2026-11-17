// What the app's terms mean, for a builder meeting them for the first time.
// Each is shown by an (i) marker beside the term (Explain.svelte), and kept
// here so a term reads the same wherever it appears.

/** One explained term. */
export interface Entry {
  /** The term as the marker names it to a screen reader. */
  term: string;
  /** What it means, in a few sentences. */
  text: string;
}

export const GLOSSARY = {
  build: {
    term: 'Build',
    text: "This document describes more than one version of the pedal, such as with and without a mod. Pick the one you're building, and only its parts go on the shopping list.",
  },
  ocr: {
    term: 'OCR',
    text: 'OCR (optical character recognition) reads text from a picture of it. This parts list is a picture, so there is no text to copy. OCR can misread a value, so Star Ground lists every part it read for you to check against the document.',
  },
  'hardware-kit': {
    term: 'Hardware kit',
    text: 'The parts that go in the box but not on the circuit board: the enclosure, the input and output jacks, the DC power jack, the footswitch, and the status LED with its resistor. Star Ground adds them unless a document lists its own. Untick it if you have them already.',
  },
  sockets: {
    term: 'Sockets',
    text: "A chip socket is soldered to the board in the chip's place, and the chip plugs into it. Soldering heat never reaches the chip, and you can swap or replace it later without desoldering. Star Ground adds one for each chip that takes one.",
  },
  notation: {
    term: 'Values',
    text: '4.7k and 4k7 are the same value. The second puts the letter where the decimal point goes, because a small printed point is easy to miss. Pick the one your build document uses.',
  },
  bands: {
    term: 'Bands',
    text: 'How many coloured bands your resistors have: five on most 1% metal film resistors, four on 5% carbon film. The drawing beside each resistor follows your choice.',
  },
  buy: {
    term: 'Buy',
    text: 'Usually more than the builds need: cheap parts that are easy to lose or break come with spares. Resistors get at least 5 more, rounded up to a multiple of 10; small capacitors 5 more (10 for 100n); capacitors of 1 µF and up and transistors 1 more; diodes and LEDs 5 more, and at least 10.',
  },
  checks: {
    term: 'Check these parts',
    text: "Star Ground checks each reading against itself: a board label such as R1 with no value, two rows that disagree, a value that isn't a real part. These are the places that didn't add up. Look each one up in the document before ordering; the other parts aren't affected.",
  },
  'counted-by-value': {
    term: 'Counted by value',
    text: "Some documents list a part by its value and a count, such as '10k × 4', instead of a row for each label on the board, such as R1. There's nothing to point to on the page, so check the count instead.",
  },
  'stock-csv': {
    term: 'Stock CSV',
    text: 'A spreadsheet saved as CSV, one row per part, with the columns Category (such as Resistors), Part (such as 10k) and Qty. The Export page saves your stock in this form as Updated stock, ready to upload next time.',
  },
  'supplier-cart': {
    term: 'Supplier cart',
    text: 'Mouser and Digi-Key, two large electronics stores, can fill a cart from a file. Chips, transistors and other parts with a part number go in by number. Resistors and capacitors go in by description, and you pick a product for each.',
  },
} satisfies Record<string, Entry>;

/** A term the glossary explains. */
export type Term = keyof typeof GLOSSARY;

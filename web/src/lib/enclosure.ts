// A project's pedal as a builder pictures it: an enclosure with a knob for
// each pot, named as the parts list names it (VOLUME, GAIN), a toggle for each
// switch, an LED and a footswitch. It's a picture, not a drilling template:
// the knobs follow the layouts builders usually use, not the document's
// drill guide, and the drawing carries no sizes.

import type { ProjectResult } from './api';

/** The paints an enclosure can have, as powder coats are commonly sold. */
export const PAINTS = {
  aluminium: { name: 'Raw aluminium', color: '#b9bdc2', ink: 'dark' },
  black: { name: 'Black', color: '#2b2c30', ink: 'light' },
  cream: { name: 'Cream', color: '#e6dfcc', ink: 'dark' },
  red: { name: 'Red', color: '#b5312c', ink: 'light' },
  orange: { name: 'Orange', color: '#d8732c', ink: 'dark' },
  yellow: { name: 'Yellow', color: '#e2b93b', ink: 'dark' },
  green: { name: 'Green', color: '#3c8a57', ink: 'light' },
  teal: { name: 'Teal', color: '#2d8288', ink: 'light' },
  blue: { name: 'Blue', color: '#2f5aa3', ink: 'light' },
  purple: { name: 'Purple', color: '#6a4a9e', ink: 'light' },
  pink: { name: 'Pink', color: '#d27ca2', ink: 'dark' },
} as const;

export type Paint = keyof typeof PAINTS;

/** How the paint is finished: flat, glossy, or hammertone's dimpled metal. */
export const FINISHES = {
  matte: 'Matte',
  gloss: 'Gloss',
  hammered: 'Hammered',
} as const;

export type Finish = keyof typeof FINISHES;

/** A card's finish: the builder's choice, or matte until they pick one. */
export const finishOf = (chosen: Finish | null | undefined): Finish =>
  chosen && chosen in FINISHES ? chosen : 'matte';

const PAINT_KEYS = Object.keys(PAINTS) as Paint[];

/** A card's paint: the builder's choice, or one picked from its id so each project differs. */
export function paintOf(id: string, chosen: Paint | null | undefined): Paint {
  if (chosen && chosen in PAINTS) return chosen;
  let hash = 0;
  for (const c of id) hash = (hash * 31 + c.charCodeAt(0)) >>> 0;
  return PAINT_KEYS[hash % PAINT_KEYS.length] as Paint;
}

/** What goes on the enclosure's face. */
export interface Face {
  /** Each knob's label, or '' for a pot named only by number (VR1). */
  knobs: string[];
  toggles: number;
}

// A pot named only by its number says nothing worth printing on a knob.
const NUMBERED = /^(VR|RV|POT|P|R|SW|S)\d+[A-Z]?$/i;
// Switches set on the board, not on the face.
const ON_BOARD = /DIP|TACT|SLIDE|TRIM/i;
const ROTARY = /ROTARY|\d+P\d+T/i;

/** The knobs and toggles a project's parts list puts on the face. */
export function faceOf(project: ProjectResult | null): Face {
  if (!project) return { knobs: [], toggles: 0 };
  const knobs: string[] = [];
  let toggles = 0;
  for (const part of project.parts) {
    if (part.category === 'Potentiometers') knobs.push(NUMBERED.test(part.ref) ? '' : part.ref);
    else if (part.category === 'Switches' && !ON_BOARD.test(part.value)) {
      // A rotary switch wears a knob.
      if (ROTARY.test(part.value)) knobs.push(NUMBERED.test(part.ref) ? '' : part.ref);
      else toggles += 1;
    }
  }
  for (const counted of project.counted) {
    if (counted.category === 'Potentiometers') knobs.push(...Array<string>(counted.count).fill(''));
  }
  return { knobs, toggles };
}

/** A point on the face, in the drawing's units. */
interface At {
  x: number;
  y: number;
}

/** The face laid out: the enclosure's size, and where each control goes. */
export interface Layout {
  width: number;
  height: number;
  knob: number;
  /** Each knob, its label, and the width its row leaves the label. */
  knobs: (At & { label: string; room: number })[];
  toggles: At[];
  led: At;
  footswitch: At;
}

// Knobs per row for each count, as builders usually drill them.
function rows(count: number): number[] {
  if (count <= 3) return count ? [count] : [];
  if (count === 4) return [2, 2];
  if (count <= 6) return [3, count - 3];
  if (count <= 9) return [3, 3, count - 6].filter(Boolean);
  const perRow = 4;
  return Array.from({ length: Math.ceil(count / perRow) }, (_, i) =>
    Math.min(perRow, count - i * perRow),
  );
}

/** Lays out a face: a narrow box for up to three knobs, wider for more. */
export function layout(face: Face): Layout {
  const count = face.knobs.length;
  const width = count <= 3 ? 60 : count <= 6 ? 66 : 92;
  const perRow = rows(count);
  // A knob takes at most this share of its slot in the fullest row, so
  // neighbours never touch: three across a narrow box get smaller knobs.
  const slot = width / (Math.max(1, ...perRow) + 1);
  const knob = Math.min(count <= 1 ? 9 : 8, slot * 0.4);
  const rowGap = knob * 2 + 9;
  const knobs: Layout['knobs'] = [];
  let index = 0;
  perRow.forEach((n, row) => {
    for (let i = 0; i < n; i += 1) {
      knobs.push({
        x: (width * (i + 1)) / (n + 1),
        y: 18 + row * rowGap,
        label: face.knobs[index] ?? '',
        room: (width / (n + 1)) * 0.94,
      });
      index += 1;
    }
  });
  const below = perRow.length ? 18 + (perRow.length - 1) * rowGap + knob + 10 : 20;
  const toggles = Array.from({ length: face.toggles }, (_, i) => ({
    x: (width * (i + 1)) / (face.toggles + 1),
    y: below + 4,
  }));
  // The LED and footswitch sit near the bottom, below everything else.
  const after = face.toggles ? below + 12 : below;
  const height = Math.max(count <= 6 ? 112 : 120, after + 46);
  return {
    width,
    height,
    knob,
    knobs,
    toggles,
    led: { x: width / 2, y: height - 38 },
    footswitch: { x: width / 2, y: height - 20 },
  };
}

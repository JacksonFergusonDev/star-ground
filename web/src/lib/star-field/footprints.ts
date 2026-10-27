// Through-hole footprints at their real sizes, in board units (0.1 inch). Each
// is drawn about its origin with its long axis east; pads are listed in pin
// order.

import { boundsOf, quarterTurn, vec, type Rect, type Vec } from './geometry';

export type PartKind =
  'resistor' | 'diode' | 'film-cap' | 'electrolytic' | 'transistor' | 'pot' | 'terminal' | 'star';

export interface Pad {
  at: Vec;
  radius: number;
  drill: number;
  square: boolean;
}

/** Silkscreen: outlines, filled marks, and arcs (angles clockwise from east). */
export type Silk =
  | { kind: 'line'; points: Vec[]; closed: boolean }
  | { kind: 'fill'; points: Vec[] }
  | { kind: 'arc'; center: Vec; radius: number; start: number; end: number };

interface Footprint {
  pads: Pad[];
  silk: Silk[];
}

const pad = (x: number, y: number, radius = 0.36, square = false): Pad => ({
  at: vec(x, y),
  radius,
  drill: radius * 0.47,
  square,
});

const box = (x0: number, y0: number, x1: number, y1: number): Silk => ({
  kind: 'line',
  points: [vec(x0, y0), vec(x1, y0), vec(x1, y1), vec(x0, y1)],
  closed: true,
});

const line = (...points: Vec[]): Silk => ({ kind: 'line', points, closed: false });

const circle = (x: number, y: number, radius: number): Silk => ({
  kind: 'arc',
  center: vec(x, y),
  radius,
  start: 0,
  end: Math.PI * 2,
});

// An electrolytic's negative side is printed solid: the circle's segment
// beyond x = `from`.
function negativeBand(radius: number, from: number): Silk {
  const reach = Math.acos(from / radius);
  const points: Vec[] = [];
  for (let i = 0; i <= 12; i++) {
    const angle = -reach + (2 * reach * i) / 12;
    points.push(vec(Math.cos(angle) * radius, Math.sin(angle) * radius));
  }
  return { kind: 'fill', points };
}

// TO-92: a D shape, flat side toward +y, with the flat at y = 0.65.
const TO92_RADIUS = 1.25;
const TO92_FLAT = Math.asin(0.65 / TO92_RADIUS);

const FOOTPRINTS: Record<PartKind, Footprint> = {
  // Axial, 0.4 inch pitch.
  resistor: {
    pads: [pad(-2, 0), pad(2, 0)],
    silk: [
      box(-1.3, -0.48, 1.3, 0.48),
      line(vec(-1.3, 0), vec(-1.55, 0)),
      line(vec(1.3, 0), vec(1.55, 0)),
    ],
  },
  // DO-35: cathode (pin 1) square, under the band.
  diode: {
    pads: [pad(-2, 0, 0.36, true), pad(2, 0)],
    silk: [
      box(-1, -0.4, 1, 0.4),
      {
        kind: 'fill',
        points: [vec(-0.85, -0.4), vec(-0.6, -0.4), vec(-0.6, 0.4), vec(-0.85, 0.4)],
      },
      line(vec(-1, 0), vec(-1.55, 0)),
      line(vec(1, 0), vec(1.55, 0)),
    ],
  },
  // Box film or ceramic capacitor, 0.2 inch pitch.
  'film-cap': {
    pads: [pad(-1, 0), pad(1, 0)],
    silk: [box(-1.75, -0.7, 1.75, 0.7)],
  },
  // 5 mm radial electrolytic, 2 mm pitch: positive (pin 1) square.
  electrolytic: {
    pads: [pad(-0.4, 0, 0.3, true), pad(0.4, 0, 0.3)],
    silk: [
      circle(0, 0, 1),
      negativeBand(1, 0.68),
      line(vec(-1.45, -0.95), vec(-1.05, -0.95)),
      line(vec(-1.25, -1.15), vec(-1.25, -0.75)),
    ],
  },
  // TO-92, 0.1 inch pitch: emitter, base, collector.
  transistor: {
    pads: [pad(-1, 0, 0.3), pad(0, 0, 0.3), pad(1, 0, 0.3)],
    silk: [
      {
        kind: 'arc',
        center: vec(0, 0),
        radius: TO92_RADIUS,
        start: Math.PI - TO92_FLAT,
        end: Math.PI * 2 + TO92_FLAT,
      },
      line(
        vec(-Math.cos(TO92_FLAT) * TO92_RADIUS, 0.65),
        vec(Math.cos(TO92_FLAT) * TO92_RADIUS, 0.65),
      ),
    ],
  },
  // 16 mm board-mount pot, 0.2 inch lug pitch: lug 1, wiper, lug 3. The body
  // and shaft sit toward -y.
  pot: {
    pads: [pad(-2, 0, 0.5), pad(0, 0, 0.5), pad(2, 0, 0.5)],
    silk: [
      circle(0, -4, 3.15),
      circle(0, -4, 1.05),
      line(vec(-3.15, -4), vec(-3.15, -1.2)),
      line(vec(3.15, -4), vec(3.15, -1.2)),
    ],
  },
  // A wire pad for a jack or the battery, square as off-board pads usually are.
  terminal: { pads: [pad(0, 0, 0.55, true)], silk: [] },
  // The star ground: one large pad where every ground return meets, and where
  // the jacks' ground wires land.
  star: {
    pads: [{ at: vec(0, 0), radius: 1.15, drill: 0.6, square: false }],
    silk: [circle(0, 0, 1.9)],
  },
};

/** A footprint turned by quarter turns and moved to `center`, with its bounds. */
export interface PlacedFootprint extends Footprint {
  bounds: Rect;
}

export function place(kind: PartKind, center: Vec, turns: number): PlacedFootprint {
  const source = FOOTPRINTS[kind];
  const move = (p: Vec): Vec => {
    const r = quarterTurn(p, turns);
    return vec(center.x + r.x, center.y + r.y);
  };
  const pads = source.pads.map((p) => ({ ...p, at: move(p.at) }));
  const silk = source.silk.map((s): Silk => {
    if (s.kind === 'arc') {
      const turn = (turns * Math.PI) / 2;
      return { ...s, center: move(s.center), start: s.start + turn, end: s.end + turn };
    }
    return { ...s, points: s.points.map(move) };
  });
  const extents: Vec[] = [];
  for (const p of pads) {
    extents.push(
      vec(p.at.x - p.radius, p.at.y - p.radius),
      vec(p.at.x + p.radius, p.at.y + p.radius),
    );
  }
  for (const s of silk) {
    if (s.kind === 'arc') {
      extents.push(
        vec(s.center.x - s.radius, s.center.y - s.radius),
        vec(s.center.x + s.radius, s.center.y + s.radius),
      );
    } else {
      extents.push(...s.points);
    }
  }
  return { pads, silk, bounds: boundsOf(extents) };
}

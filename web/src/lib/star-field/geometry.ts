// Plane geometry for the board, in board units: one unit is 0.1 inch, the
// pitch through-hole parts sit on.

export interface Vec {
  x: number;
  y: number;
}

export interface Rect {
  x0: number;
  y0: number;
  x1: number;
  y1: number;
}

export const vec = (x: number, y: number): Vec => ({ x, y });
export const distance = (a: Vec, b: Vec): number => Math.hypot(a.x - b.x, a.y - b.y);

/** Rotates by a quarter turn `turns` times, clockwise on screen. */
export function quarterTurn(a: Vec, turns: number): Vec {
  switch (((turns % 4) + 4) % 4) {
    case 1:
      return vec(-a.y, a.x);
    case 2:
      return vec(-a.x, -a.y);
    case 3:
      return vec(a.y, -a.x);
    default:
      return a;
  }
}

export function boundsOf(points: readonly Vec[], pad = 0): Rect {
  const xs = points.map((p) => p.x);
  const ys = points.map((p) => p.y);
  return {
    x0: Math.min(...xs) - pad,
    y0: Math.min(...ys) - pad,
    x1: Math.max(...xs) + pad,
    y1: Math.max(...ys) + pad,
  };
}

/** A small seeded generator (mulberry32), so the motion runs the same on every visit. */
export function seeded(seed: number): () => number {
  let state = seed >>> 0;
  return () => {
    state = (state + 0x6d2b79f5) >>> 0;
    let t = state;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

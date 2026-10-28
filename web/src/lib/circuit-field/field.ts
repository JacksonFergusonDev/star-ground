/**
 * Star Ground's hero background: a Big Muff π fuzz board (circuit.ts) with
 * current moving through it. The guitar's signal runs from the input pad
 * through every stage to the output, and ground returns run home to the star
 * ground. Returns leave their pads timed to reach the star together, so
 * wherever their paths join they meet and travel on as one brighter pulse.
 *
 * It follows house-style's motion rules (GUIDELINES.md): subdued shades of the
 * accent, well below the text in contrast. house-style's `runField` keeps it
 * still for reduced motion, paused off screen or in a hidden tab, and
 * stoppable with its pause button (`.hs-motion`). It draws with raw WebGL2: the still board is one texture, and
 * the current is one instanced draw of round-capped segments.
 */

import { resolveColor, runField, type Field, type FieldStyle } from 'house-style/js/field.js';
import { drawBoard } from './board';
import { seeded, vec } from './geometry';
import { buildBoard, type Board } from './layout';

const MAX_EVENTS = 64;
/** Pulse speeds (units a second), head, and tail (units, at ground speed). */
const GROUND_SPEED = 9;
const SIGNAL_SPEED = 30;
const HEAD = 0.6;
const TAIL = 7;
const STAR_PAD = { radius: 1.15, drill: 0.6 };

const QUAD_VERTEX = `#version 300 es
out vec2 vUv;
void main() {
  vec2 corner = vec2(gl_VertexID == 1 ? 3.0 : -1.0, gl_VertexID == 2 ? 3.0 : -1.0);
  vUv = corner * 0.5 + 0.5;
  gl_Position = vec4(corner, 0.0, 1.0);
}`;

const GUARD = `
uniform vec2 uViewport;
uniform float uWide;
// Keep the hero's copy quiet: the board fades in to the right of it on wide
// screens, and below it on narrow ones.
float guard() {
  float across = smoothstep(0.34, 0.66, gl_FragCoord.x / uViewport.x);
  float down = smoothstep(0.56, 0.86, 1.0 - gl_FragCoord.y / uViewport.y);
  return 0.1 + 0.9 * mix(down, across, uWide);
}`;

const BOARD_FRAGMENT = `#version 300 es
precision mediump float;
in vec2 vUv;
uniform sampler2D uBoard;
uniform float uAmount;
${GUARD}
out vec4 outColor;
void main() {
  outColor = texture(uBoard, vec2(vUv.x, 1.0 - vUv.y)) * uAmount * guard();
}`;

const CURRENT_VERTEX = `#version 300 es
layout(location = 0) in vec4 aSegment;
layout(location = 1) in vec4 aShape;
layout(location = 2) in vec2 aRange;
uniform vec2 uViewport;
uniform float uHalo;
out vec2 vPosition;
flat out vec4 vSegment;
flat out vec4 vShape;
flat out vec2 vRange;
void main() {
  vec2 corner = vec2(gl_VertexID & 1, gl_VertexID >> 1) * 2.0 - 1.0;
  vec2 a = aSegment.xy;
  vec2 b = aSegment.zw;
  float len = length(b - a);
  vec2 t = len > 0.001 ? (b - a) / len : vec2(1.0, 0.0);
  vec2 n = vec2(-t.y, t.x);
  float reach = aShape.x + uHalo;
  vPosition = (a + b) * 0.5 + t * corner.x * (len * 0.5 + reach) + n * corner.y * reach;
  vSegment = aSegment;
  vShape = aShape;
  vRange = aRange;
  vec2 clip = vPosition / uViewport * 2.0 - 1.0;
  gl_Position = vec4(clip.x, -clip.y, 0.0, 1.0);
}`;

const CURRENT_FRAGMENT = `#version 300 es
precision highp float;
in vec2 vPosition;
flat in vec4 vSegment;
flat in vec4 vShape;
flat in vec2 vRange;
uniform vec3 uColor;
uniform float uAmount;
uniform float uTime;
uniform float uHalo;
uniform float uStar;
uniform int uCount;
uniform vec4 uEvents[${MAX_EVENTS}];
${GUARD}
out vec4 outColor;
void main() {
  vec2 a = vSegment.xy;
  vec2 ab = vSegment.zw - a;
  float h = clamp(dot(vPosition - a, ab) / max(dot(ab, ab), 0.0001), 0.0, 1.0);
  float dist = length(vPosition - a - ab * h);
  float radius = vShape.x;
  float hole = vShape.y;
  float copper = 1.0 - smoothstep(radius - 0.7, radius + 0.7, dist);
  float halo = exp(-max(dist - radius, 0.0) / uHalo * 2.5) * 0.16;
  float open = hole > 0.0 ? smoothstep(hole - 0.7, hole + 0.7, dist) : 1.0;

  float energy;
  if (vRange.x < 0.0) {
    energy = uStar;
  } else {
    float d = mix(vShape.z, vShape.w, h);
    float sum = 0.0;
    for (int i = 0; i < ${MAX_EVENTS}; i++) {
      if (i >= uCount) break;
      // Source, seconds until arrival, speed, and strength.
      vec4 event = uEvents[i];
      if (event.x < vRange.x || event.x > vRange.y) continue;
      // How far the pulse's head is from here, along the copper.
      float x = d - event.y * event.z;
      float tail = ${TAIL.toFixed(1)} * sqrt(event.z / ${GROUND_SPEED.toFixed(1)});
      sum += event.w * (x < 0.0 ? exp(x / ${HEAD.toFixed(1)}) : exp(-x / tail));
    }
    energy = 1.0 - exp(-sum * 1.1);
  }
  float alpha = (copper * 0.9 + halo) * open * energy * uAmount * guard();
  outColor = vec4(uColor * alpha, alpha);
}`;

interface PulseEvent {
  source: number;
  arrival: number;
  speed: number;
  strength: number;
}

/**
 * Starts the board on `canvas`, with `button` as its pause control.
 *
 * @returns Controls, or nothing when the browser has no WebGL2 (the hero keeps
 *   its plain background).
 */
export function startCircuitField(
  canvas: HTMLCanvasElement,
  button: HTMLButtonElement,
): Field | undefined {
  if (!canvas.isConnected) return undefined;
  const context = canvas.getContext('webgl2', {
    alpha: true,
    premultipliedAlpha: true,
    antialias: false,
  });
  if (!context) return undefined;
  const gl: WebGL2RenderingContext = context;

  const boardProgram = program(gl, QUAD_VERTEX, BOARD_FRAGMENT);
  const currentProgram = program(gl, CURRENT_VERTEX, CURRENT_FRAGMENT);
  const uniforms = {
    board: locations(gl, boardProgram, ['uBoard', 'uAmount', 'uViewport', 'uWide']),
    current: locations(gl, currentProgram, [
      'uViewport',
      'uWide',
      'uHalo',
      'uColor',
      'uAmount',
      'uTime',
      'uStar',
      'uCount',
      'uEvents',
    ]),
  };
  const texture = gl.createTexture();
  const instances = gl.createBuffer();
  const vao = gl.createVertexArray();
  gl.bindVertexArray(vao);
  gl.bindBuffer(gl.ARRAY_BUFFER, instances);
  const stride = 10 * 4;
  [4, 4, 2].forEach((size, location, sizes) => {
    const offset = sizes.slice(0, location).reduce((total, n) => total + n, 0) * 4;
    gl.enableVertexAttribArray(location);
    gl.vertexAttribPointer(location, size, gl.FLOAT, false, stride, offset);
    gl.vertexAttribDivisor(location, 1);
  });
  gl.bindVertexArray(null);

  const paper = document.createElement('canvas');
  const board: Board = buildBoard();
  let instanceCount = 0;
  let wide = false;
  let pixelScale = 1;
  let amount = 0.36;
  let color: [number, number, number] = [0.29, 0.87, 0.5];
  let font = 'monospace';
  let boardColors = { mask: '', edge: '', copper: '', pad: '', silk: '' };

  let laidOut = false;
  let time = 0;
  let nextWave = 0;
  let nextSignal = 0;
  let events: PulseEvent[] = [];
  const eventData = new Float32Array(MAX_EVENTS * 4);
  let random = seeded(11);

  // The board's colours are baked into its texture, so a new style lays it out again.
  function applyStyle(style: FieldStyle) {
    const accent = style.color;
    const text = resolveColor(getComputedStyle(document.body).color);
    color = [accent[0] / 255, accent[1] / 255, accent[2] / 255];
    amount = style.strength;
    font =
      getComputedStyle(document.documentElement).getPropertyValue('--mono').trim() || 'monospace';
    const rgba = (c: number[], a: number) => `rgba(${c[0]}, ${c[1]}, ${c[2]}, ${a})`;
    boardColors = {
      mask: rgba(accent, 0.045),
      edge: rgba(text, 0.3),
      copper: rgba(accent, 0.42),
      pad: rgba(text, 0.5),
      silk: rgba(text, 0.42),
    };
    if (laidOut) layout();
  }

  // Fits the board to the canvas and uploads its still layers. On wide screens
  // it sits right of the copy, aligned to the content column; on narrow ones,
  // below it.
  function layout() {
    const width = canvas.clientWidth || 1;
    const height = canvas.clientHeight || 1;
    pixelScale = Math.min(window.devicePixelRatio || 1, 2);
    canvas.width = Math.round(width * pixelScale);
    canvas.height = Math.round(height * pixelScale);
    wide = width > 900;
    let unitCss: number;
    let origin;
    if (wide) {
      const column = Math.min(1160, width - 64);
      const right = (width + column) / 2;
      unitCss = Math.min((column * 0.5) / board.width, (height * 0.8) / board.height);
      origin = vec(right - board.width * unitCss, (height - board.height * unitCss) / 2);
    } else {
      unitCss = (width - 32) / board.width;
      origin = vec(16, height - board.height * unitCss - 24);
    }
    const unit = unitCss * pixelScale;
    const o = vec(origin.x * pixelScale, origin.y * pixelScale);

    paper.width = canvas.width;
    paper.height = canvas.height;
    const ctx = paper.getContext('2d');
    if (ctx) drawBoard(ctx, board, { unit, origin: o, font, ...boardColors });
    gl.bindTexture(gl.TEXTURE_2D, texture);
    gl.pixelStorei(gl.UNPACK_PREMULTIPLY_ALPHA_WEBGL, true);
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, paper);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.NEAREST);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.NEAREST);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);

    const x = (v: number) => o.x + v * unit;
    const y = (v: number) => o.y + v * unit;
    const data: number[] = [];
    for (const s of board.segments) {
      data.push(x(s.a.x), y(s.a.y), x(s.b.x), y(s.b.y));
      data.push((s.width / 2) * unit, 0, s.da, s.db, s.first, s.last);
    }
    for (const p of board.pads) {
      data.push(x(p.at.x), y(p.at.y), x(p.at.x), y(p.at.y));
      data.push(p.radius * unit, p.drill * unit, p.distance, p.distance, p.first, p.last);
    }
    const sx = x(board.star.x);
    const sy = y(board.star.y);
    data.push(sx, sy, sx, sy, STAR_PAD.radius * unit, STAR_PAD.drill * unit, 0, 0, -1, -1);
    instanceCount = data.length / 10;
    gl.bindBuffer(gl.ARRAY_BUFFER, instances);
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array(data), gl.STATIC_DRAW);
    laidOut = true;
  }

  // Starts as if the circuit had been running, so the first frame isn't empty.
  function prime() {
    random = seeded(11);
    events = [];
    nextWave = time - Math.max(...board.ground) / GROUND_SPEED;
    nextSignal = time - board.signal.length / SIGNAL_SPEED;
    schedule();
  }

  // A note every few seconds runs from the input to the output; between them,
  // a few ground pins at a time send current home, timed to reach the star
  // together.
  function schedule() {
    while (nextSignal <= time) {
      const arrival = nextSignal + board.signal.length / SIGNAL_SPEED;
      events.push({ source: board.signal.source, arrival, speed: SIGNAL_SPEED, strength: 1 });
      nextSignal += 3.2 + random() * 2.4;
    }
    while (nextWave <= time) {
      const pins = board.ground.map((_, id) => id).sort(() => random() - 0.5);
      const chosen = pins.slice(0, 2 + Math.floor(random() * 3));
      const far = Math.max(...chosen.map((id) => board.ground[id] ?? 0));
      const arrival = nextWave + far / GROUND_SPEED + 0.3;
      if (events.length + chosen.length <= MAX_EVENTS) {
        for (const source of chosen) {
          events.push({ source, arrival, speed: GROUND_SPEED, strength: 0.75 });
        }
      }
      nextWave += 1.4 + random() * 1.8;
    }
    events = events.filter((e) => (time - e.arrival) * e.speed < TAIL * 6);
  }

  function starEnergy(): number {
    let sum = 0.25;
    const arrivals = new Set(
      events
        .filter((e) => e.source !== board.signal.source && e.arrival <= time)
        .map((e) => e.arrival),
    );
    for (const arrival of arrivals) sum += 0.9 * Math.exp(-(time - arrival) * 2.4);
    return 1 - Math.exp(-sum);
  }

  function draw() {
    const { width, height } = canvas;
    gl.viewport(0, 0, width, height);
    gl.clearColor(0, 0, 0, 0);
    gl.clear(gl.COLOR_BUFFER_BIT);
    gl.enable(gl.BLEND);

    gl.useProgram(boardProgram);
    gl.blendEquation(gl.FUNC_ADD);
    gl.blendFunc(gl.ONE, gl.ONE_MINUS_SRC_ALPHA);
    gl.activeTexture(gl.TEXTURE0);
    gl.bindTexture(gl.TEXTURE_2D, texture);
    gl.uniform1i(uniforms.board.uBoard, 0);
    gl.uniform1f(uniforms.board.uAmount, Math.min(1, amount * 1.6));
    gl.uniform2f(uniforms.board.uViewport, width, height);
    gl.uniform1f(uniforms.board.uWide, wide ? 1 : 0);
    gl.drawArrays(gl.TRIANGLES, 0, 3);

    eventData.fill(0);
    events.forEach((e, i) =>
      eventData.set([e.source, e.arrival - time, e.speed, e.strength], i * 4),
    );
    gl.useProgram(currentProgram);
    // Overlapping round caps take the brighter value instead of doubling.
    gl.blendEquation(gl.MAX);
    gl.uniform2f(uniforms.current.uViewport, width, height);
    gl.uniform1f(uniforms.current.uWide, wide ? 1 : 0);
    gl.uniform1f(uniforms.current.uHalo, 5 * pixelScale);
    gl.uniform3f(uniforms.current.uColor, ...color);
    gl.uniform1f(uniforms.current.uAmount, Math.min(1, amount * 2.4));
    gl.uniform1f(uniforms.current.uTime, 0);
    gl.uniform1f(uniforms.current.uStar, starEnergy());
    gl.uniform1i(uniforms.current.uCount, events.length);
    gl.uniform4fv(uniforms.current.uEvents, eventData);
    gl.bindVertexArray(vao);
    gl.drawArraysInstanced(gl.TRIANGLE_STRIP, 0, 4, instanceCount);
    gl.bindVertexArray(null);
  }

  // Laying the board out redraws its texture, so a resize waits until the
  // window settles; the first layout is immediate.
  let resizing = 0;
  function resize() {
    if (!laidOut) return layout();
    window.clearTimeout(resizing);
    resizing = window.setTimeout(() => {
      layout();
      draw();
    }, 120);
  }

  prime();
  return runField(canvas, button, {
    style: applyStyle,
    resize,
    draw(now, delta) {
      time = now;
      if (delta > 0) schedule();
      draw();
    },
    dispose() {
      window.clearTimeout(resizing);
      gl.getExtension('WEBGL_lose_context')?.loseContext();
    },
  });
}

function program(gl: WebGL2RenderingContext, vertex: string, fragment: string): WebGLProgram {
  const result = gl.createProgram();
  for (const [type, source] of [
    [gl.VERTEX_SHADER, vertex],
    [gl.FRAGMENT_SHADER, fragment],
  ] as const) {
    const shader = gl.createShader(type);
    if (!shader) throw new Error('Could not create a shader.');
    gl.shaderSource(shader, source);
    gl.compileShader(shader);
    if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) {
      throw new Error(gl.getShaderInfoLog(shader) ?? 'Shader failed to compile.');
    }
    gl.attachShader(result, shader);
  }
  gl.linkProgram(result);
  if (!gl.getProgramParameter(result, gl.LINK_STATUS)) {
    throw new Error(gl.getProgramInfoLog(result) ?? 'Program failed to link.');
  }
  return result;
}

function locations<T extends string>(
  gl: WebGL2RenderingContext,
  target: WebGLProgram,
  names: T[],
): Record<T, WebGLUniformLocation | null> {
  return Object.fromEntries(
    names.map((name) => [name, gl.getUniformLocation(target, name)]),
  ) as Record<T, WebGLUniformLocation | null>;
}

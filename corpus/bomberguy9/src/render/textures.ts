import * as THREE from 'three/webgpu';
import { at, clamp01, fbm, hash2, lerp, periodicVoronoi, smoothstep } from './noise';

/**
 * Procedural surface textures.
 *
 * Every arena surface is generated at runtime from a height field: the height
 * drives a derived normal map (so flat boxes catch the light like real
 * material), a roughness map, and the albedo. Nothing is downloaded, nothing
 * seams, and a theme can be recoloured without new assets.
 */

export type SurfaceKind = 'panel' | 'rock' | 'ice' | 'cobble' | 'circuit' | 'crate' | 'tiles';

export interface SurfaceSpec {
  kind: SurfaceKind;
  /** Dominant colour. */
  base: number;
  /** Recesses, grout and shadowed detail. */
  shade: number;
  /** Trim, glow and highlights. */
  accent: number;
  seed: number;
  size?: number;
  /** Multiplier on the derived normal strength. */
  relief?: number;
}

export interface SurfaceMaps {
  map: THREE.Texture;
  normalMap: THREE.Texture;
  roughnessMap: THREE.Texture;
  emissiveMap: THREE.Texture | null;
  dispose(): void;
}

const rgbOf = (hex: number): [number, number, number] => [
  ((hex >> 16) & 255) / 255,
  ((hex >> 8) & 255) / 255,
  (hex & 255) / 255,
];

interface Fields {
  size: number;
  height: Float32Array;
  rough: Float32Array;
  albedo: Float32Array;
  emissive: Float32Array;
  hasEmissive: boolean;
}

function makeFields(size: number): Fields {
  return {
    size,
    height: new Float32Array(size * size),
    rough: new Float32Array(size * size).fill(0.75),
    albedo: new Float32Array(size * size * 3),
    emissive: new Float32Array(size * size * 3),
    hasEmissive: false,
  };
}

function setPixel(f: Fields, i: number, r: number, g: number, b: number): void {
  f.albedo[i * 3] = r;
  f.albedo[i * 3 + 1] = g;
  f.albedo[i * 3 + 2] = b;
}

function addEmissive(f: Fields, i: number, r: number, g: number, b: number): void {
  if (r + g + b <= 0) return;
  f.emissive[i * 3] += r;
  f.emissive[i * 3 + 1] += g;
  f.emissive[i * 3 + 2] += b;
  f.hasEmissive = true;
}

/* -------------------------------------------------------------------------- */
/* Surface recipes                                                             */
/* -------------------------------------------------------------------------- */

/** Riveted metal panels with a recessed inner plate and a lit trim edge. */
function panel(f: Fields, spec: SurfaceSpec): void {
  const { size } = f;
  const base = rgbOf(spec.base);
  const shade = rgbOf(spec.shade);
  const accent = rgbOf(spec.accent);
  const grain = fbm(size, 8, 4, spec.seed);
  const dirt = fbm(size, 3, 3, spec.seed + 31);

  for (let y = 0; y < size; y++) {
    for (let x = 0; x < size; x++) {
      const i = y * size + x;
      const u = x / size;
      const v = y / size;

      // Border frame, then a recessed plate inside it.
      const edge = Math.min(u, v, 1 - u, 1 - v);
      const frame = smoothstep(0.055, 0.075, edge);
      const inner = 1 - smoothstep(0.1, 0.12, edge);
      let h = 0.62 + frame * 0.16 - inner * 0.06;

      // A cross seam splitting the plate into quarters.
      const seam =
        Math.max(
          1 - smoothstep(0.0, 0.012, Math.abs(u - 0.5)),
          1 - smoothstep(0.0, 0.012, Math.abs(v - 0.5)),
        ) * frame;
      h -= seam * 0.12;

      // Rivets in the frame corners.
      let rivet = 0;
      for (const [rx, ry] of [
        [0.12, 0.12],
        [0.88, 0.12],
        [0.12, 0.88],
        [0.88, 0.88],
      ]) {
        const d = Math.hypot(u - rx, v - ry);
        rivet = Math.max(rivet, 1 - smoothstep(0.03, 0.048, d));
      }
      h += rivet * 0.16;
      h += (grain[i] - 0.5) * 0.05;

      const wear = clamp01(dirt[i] * 1.3 - 0.15);
      const lit = clamp01(h * 1.15 - 0.15);
      const tone = 0.66 + wear * 0.26;
      const r = lerp(shade[0], base[0], lit) * tone;
      const g = lerp(shade[1], base[1], lit) * tone;
      const b = lerp(shade[2], base[2], lit) * tone;

      // A thin lit strip just inside the frame reads as powered machinery.
      const strip = (1 - smoothstep(0.016, 0.028, Math.abs(edge - 0.088))) * 0.9;
      setPixel(
        f,
        i,
        lerp(r, accent[0], strip * 0.7) + rivet * 0.12,
        lerp(g, accent[1], strip * 0.7) + rivet * 0.12,
        lerp(b, accent[2], strip * 0.7) + rivet * 0.12,
      );
      addEmissive(f, i, accent[0] * strip * 0.22, accent[1] * strip * 0.22, accent[2] * strip * 0.22);
      f.height[i] = h;
      f.rough[i] = clamp01(0.62 - rivet * 0.3 + wear * 0.25 - strip * 0.2);
    }
  }
}

/** Cracked volcanic rock with glowing seams in the fissures. */
function rock(f: Fields, spec: SurfaceSpec): void {
  const { size } = f;
  const base = rgbOf(spec.base);
  const shade = rgbOf(spec.shade);
  const accent = rgbOf(spec.accent);
  const bumps = fbm(size, 4, 5, spec.seed);
  const fine = fbm(size, 28, 2, spec.seed + 77);
  const cracks = periodicVoronoi(size, 3, spec.seed + 5, 0.95);
  const veins = periodicVoronoi(size, 3, spec.seed + 91, 0.9);

  for (let i = 0; i < size * size; i++) {
    const crack = 1 - smoothstep(0.0, 0.09, cracks.edge[i]);
    const vein = 1 - smoothstep(0.0, 0.045, veins.edge[i]);
    let h = 0.5 + (bumps[i] - 0.5) * 0.55 + (fine[i] - 0.5) * 0.18;
    h -= crack * 0.35;

    const lit = clamp01(h * 1.25 - 0.1);
    let r = lerp(shade[0], base[0], lit);
    let g = lerp(shade[1], base[1], lit);
    let b = lerp(shade[2], base[2], lit);

    // Heat glows out of the deepest fissures only.
    const glow = clamp01(vein * crack * 1.6);
    r = lerp(r, accent[0], glow);
    g = lerp(g, accent[1], glow);
    b = lerp(b, accent[2], glow);
    setPixel(f, i, r, g, b);
    addEmissive(f, i, accent[0] * glow * 0.9, accent[1] * glow * 0.9, accent[2] * glow * 0.9);
    f.height[i] = h;
    f.rough[i] = clamp01(0.92 - glow * 0.5 - (bumps[i] - 0.5) * 0.2);
  }
}

/** Faceted ice: flat planes meeting at bright fracture lines. */
function ice(f: Fields, spec: SurfaceSpec): void {
  const { size } = f;
  const base = rgbOf(spec.base);
  const shade = rgbOf(spec.shade);
  const accent = rgbOf(spec.accent);
  const facets = periodicVoronoi(size, 3, spec.seed, 0.9);
  const inner = periodicVoronoi(size, 5, spec.seed + 17, 0.8);
  const cloud = fbm(size, 5, 4, spec.seed + 41);

  for (let i = 0; i < size * size; i++) {
    const tilt = hash2(facets.id[i], 3, spec.seed + 5) - 0.5;
    const crack = 1 - smoothstep(0.0, 0.05, facets.edge[i]);
    const hairline = 1 - smoothstep(0.0, 0.02, inner.edge[i]);
    // Each facet is a plane at its own height; the cracks cut between them.
    let h = 0.55 + tilt * 0.4 + facets.f1[i] * 0.18 - crack * 0.28 - hairline * 0.08;
    h += (cloud[i] - 0.5) * 0.08;

    const depth = clamp01(0.35 + facets.f1[i] * 0.9);
    let r = lerp(shade[0], base[0], depth);
    let g = lerp(shade[1], base[1], depth);
    let b = lerp(shade[2], base[2], depth);
    const rim = clamp01(crack * 0.9 + hairline * 0.4);
    r = lerp(r, accent[0], rim * 0.7);
    g = lerp(g, accent[1], rim * 0.7);
    b = lerp(b, accent[2], rim * 0.7);
    setPixel(f, i, r, g, b);
    addEmissive(f, i, accent[0] * rim * 0.28, accent[1] * rim * 0.28, accent[2] * rim * 0.28);
    f.height[i] = h;
    f.rough[i] = clamp01(0.18 + cloud[i] * 0.35 + rim * 0.2);
  }
}

/** Mossy cobbles: domed stones in deep grout, with growth in the gaps. */
function cobble(f: Fields, spec: SurfaceSpec): void {
  const { size } = f;
  const base = rgbOf(spec.base);
  const shade = rgbOf(spec.shade);
  const accent = rgbOf(spec.accent);
  const stones = periodicVoronoi(size, 3, spec.seed, 0.75);
  const grit = fbm(size, 20, 3, spec.seed + 12);
  const moss = fbm(size, 4, 4, spec.seed + 55);

  for (let i = 0; i < size * size; i++) {
    const grout = 1 - smoothstep(0.02, 0.12, stones.edge[i]);
    const dome = smoothstep(0.6, 0.05, stones.f1[i]);
    let h = 0.36 + dome * 0.5 - grout * 0.34 + (grit[i] - 0.5) * 0.1;

    const tone = hash2(stones.id[i], 11, spec.seed) * 0.28 + 0.72;
    let r = base[0] * tone;
    let g = base[1] * tone;
    let b = base[2] * tone;
    r = lerp(shade[0], r, clamp01(1 - grout * 0.85));
    g = lerp(shade[1], g, clamp01(1 - grout * 0.85));
    b = lerp(shade[2], b, clamp01(1 - grout * 0.85));

    // Moss settles where water would: in the grout and on low stones.
    const growth = clamp01((moss[i] - 0.42) * 3.2) * clamp01(grout * 1.1 + 0.15);
    r = lerp(r, accent[0], growth * 0.85);
    g = lerp(g, accent[1], growth * 0.85);
    b = lerp(b, accent[2], growth * 0.85);
    setPixel(f, i, r, g, b);
    f.height[i] = h + growth * 0.05;
    f.rough[i] = clamp01(0.86 + growth * 0.1 - dome * 0.12);
  }
}

/** Printed circuit board: etched traces, pads and a solder-mask surface. */
/**
 * One seated component, not a miniature circuit board.
 *
 * This is the whole finding, and it took two attempts to see. `BoxGeometry`
 * gives every face the full 0..1 UV range, so a block wears the *entire*
 * texture -- and at full-arena zoom a block is about 27 screen pixels tall. The
 * original recipe drew an 8x8 trace lattice, which is eight features across 27
 * pixels; the first attempt at improving it went to 12 and made things worse,
 * because more detail at that scale is less detail. The stage read as flat
 * green noise for exactly this reason.
 *
 * So a block carries **one IC package** that fills most of the face: dark epoxy
 * body, pale silkscreen outline, a pin-1 notch, and two rows of legs running out
 * to the edge over a strip of solder mask. Three or four features, each several
 * pixels across, which is what survives being small.
 *
 * The value range matters as much as the shapes. Mask is pushed dark and legs
 * are bright, so a block reads light-on-dark from across the board rather than
 * mid-green on mid-green -- the stage's whole problem was that floor, blocks and
 * backdrop shared one hue at one value.
 */
function circuit(f: Fields, spec: SurfaceSpec): void {
  const { size } = f;
  const base = rgbOf(spec.base);
  const shade = rgbOf(spec.shade);
  const accent = rgbOf(spec.accent);
  const speckle = fbm(size, 40, 2, spec.seed + 3);
  const weave = fbm(size, 9, 1, spec.seed + 11);

  /** Half-extents of the package body, as a fraction of the face. */
  const BODY_X = 0.3;
  const BODY_Y = 0.2;
  /** Legs per side. Few and fat: eight tiny ones vanish at 27 pixels. */
  const LEGS = 5;

  for (let y = 0; y < size; y++) {
    for (let x = 0; x < size; x++) {
      const i = y * size + x;
      const u = x / size;
      const v = y / size;
      const ex = Math.abs(u - 0.5);
      const ey = Math.abs(v - 0.5);

      const body = (1 - smoothstep(BODY_X, BODY_X + 0.02, ex)) * (1 - smoothstep(BODY_Y, BODY_Y + 0.02, ey));
      const silk = Math.max(
        (1 - smoothstep(BODY_X + 0.045, BODY_X + 0.06, ex)) *
          (1 - smoothstep(BODY_Y + 0.045, BODY_Y + 0.06, ey)) -
          body,
        0,
      );

      // Legs: bands running from under the body out past the silkscreen, on the
      // two sides the body is widest, so the part has an obvious orientation.
      const phase = Math.abs((u * LEGS) % 1 - 0.5);
      const legRun = (1 - smoothstep(BODY_Y + 0.02, BODY_Y + 0.05, ey)) * 0;
      const legBand =
        smoothstep(BODY_Y - 0.01, BODY_Y + 0.01, ey) * (1 - smoothstep(0.38, 0.44, ey));
      const legs = clamp01((1 - smoothstep(0.2, 0.3, phase)) * legBand * (1 - body) + legRun);

      // A couple of traces leaving the part, so it looks connected to something.
      const traceY = 1 - smoothstep(0.012, 0.024, Math.abs(ey - 0.455));
      const trace = clamp01(traceY * (1 - body) * (1 - legs));

      // Pin-1 notch in the body's left edge.
      const notch = (1 - smoothstep(0.035, 0.06, Math.hypot(u - (0.5 - BODY_X + 0.05), v - 0.5))) * body;

      const metal = clamp01(Math.max(legs, trace));
      const fibre = 0.88 + weave[i] * 0.24;
      const boardTone = (0.5 + speckle[i] * 0.3) * fibre;

      let r = lerp(shade[0], base[0], boardTone);
      let g = lerp(shade[1], base[1], boardTone);
      let b = lerp(shade[2], base[2], boardTone);
      r = lerp(r, accent[0], metal * 0.95);
      g = lerp(g, accent[1], metal * 0.95);
      b = lerp(b, accent[2], metal * 0.95);
      const solid = clamp01(body - notch);
      r = lerp(r, 0.06, solid * 0.94);
      g = lerp(g, 0.065, solid * 0.94);
      b = lerp(b, 0.075, solid * 0.94);
      r = lerp(r, 0.78, silk * 0.75);
      g = lerp(g, 0.82, silk * 0.75);
      b = lerp(b, 0.8, silk * 0.75);

      setPixel(f, i, r, g, b);
      // Only the copper glows; a lit body would erase the contrast it exists for.
      const glow = metal * 0.5;
      addEmissive(f, i, accent[0] * glow, accent[1] * glow, accent[2] * glow);
      // The package stands proud of the board, the legs sit just above it.
      f.height[i] = 0.42 + solid * 0.34 + legs * 0.08 + trace * 0.03 + (speckle[i] - 0.5) * 0.03;
      f.rough[i] = clamp01(0.8 - metal * 0.5 + solid * 0.12 - silk * 0.12);
    }
  }
}

/** A braced supply crate: raised frame, diagonal straps and corner bolts. */
function crate(f: Fields, spec: SurfaceSpec): void {
  const { size } = f;
  const base = rgbOf(spec.base);
  const shade = rgbOf(spec.shade);
  const accent = rgbOf(spec.accent);
  const grain = fbm(size, 6, 4, spec.seed);
  const fine = fbm(size, 26, 2, spec.seed + 8);

  for (let y = 0; y < size; y++) {
    for (let x = 0; x < size; x++) {
      const i = y * size + x;
      const u = x / size;
      const v = y / size;
      const edge = Math.min(u, v, 1 - u, 1 - v);

      const frame = 1 - smoothstep(0.11, 0.135, edge);
      // Horizontal boards with a single diagonal brace. Asymmetric on purpose:
      // the renderer rotates each block by a random quarter-turn, which does
      // nothing at all to a symmetric pattern.
      const boards = 4;
      const bv = v * boards;
      const seam = 1 - smoothstep(0.0, 0.06, Math.min(bv - Math.floor(bv), 1 - (bv - Math.floor(bv))));
      const brace = (1 - smoothstep(0.055, 0.08, Math.abs(u - v))) * (1 - frame);

      let h =
        0.5 + frame * 0.16 + brace * 0.1 - seam * 0.1 * (1 - frame) +
        (grain[i] - 0.5) * 0.12 + (fine[i] - 0.5) * 0.06;

      let bolt = 0;
      for (const [bx, by] of [
        [0.05, 0.05],
        [0.95, 0.05],
        [0.05, 0.95],
        [0.95, 0.95],
        [0.5, 0.5],
      ]) {
        bolt = Math.max(bolt, 1 - smoothstep(0.03, 0.055, Math.hypot(u - bx, v - by)));
      }
      h += bolt * 0.14;

      const plank = 0.86 + grain[i] * 0.3;
      let r = base[0] * plank;
      let g = base[1] * plank;
      let b = base[2] * plank;
      const trim = clamp01(frame * 0.8 + brace * 0.55 + seam * 0.35 * (1 - frame));
      r = lerp(r, shade[0], trim * 0.6);
      g = lerp(g, shade[1], trim * 0.6);
      b = lerp(b, shade[2], trim * 0.6);
      r = lerp(r, accent[0], bolt * 0.8);
      g = lerp(g, accent[1], bolt * 0.8);
      b = lerp(b, accent[2], bolt * 0.8);

      setPixel(f, i, r, g, b);
      addEmissive(f, i, accent[0] * bolt * 0.18, accent[1] * bolt * 0.18, accent[2] * bolt * 0.18);
      f.height[i] = h;
      f.rough[i] = clamp01(0.8 - bolt * 0.45 + (grain[i] - 0.5) * 0.2);
    }
  }
}

/** Floor slabs: four tiles per texture, with grout, grime and hairline cracks. */
function tiles(f: Fields, spec: SurfaceSpec): void {
  const { size } = f;
  const base = rgbOf(spec.base);
  const shade = rgbOf(spec.shade);
  const accent = rgbOf(spec.accent);
  const grime = fbm(size, 3, 4, spec.seed);
  const grit = fbm(size, 22, 2, spec.seed + 19);
  const cracks = periodicVoronoi(size, 7, spec.seed + 61, 0.95);

  for (let y = 0; y < size; y++) {
    for (let x = 0; x < size; x++) {
      const i = y * size + x;
      const u = (x / size) * 2;
      const v = (y / size) * 2;
      const tu = u - Math.floor(u);
      const tv = v - Math.floor(v);
      const tile = (Math.floor(u) + Math.floor(v) * 2) | 0;

      const gap = Math.max(
        1 - smoothstep(0.0, 0.035, Math.min(tu, 1 - tu)),
        1 - smoothstep(0.0, 0.035, Math.min(tv, 1 - tv)),
      );
      const hairline = (1 - smoothstep(0.0, 0.02, cracks.edge[i])) * 0.6;
      let h = 0.62 - gap * 0.3 - hairline * 0.12 + (grit[i] - 0.5) * 0.06;

      const tone = 0.9 + hash2(tile, 7, spec.seed) * 0.2;
      const dirt = clamp01((grime[i] - 0.35) * 1.6);
      let r = lerp(base[0] * tone, shade[0], dirt * 0.55);
      let g = lerp(base[1] * tone, shade[1], dirt * 0.55);
      let b = lerp(base[2] * tone, shade[2], dirt * 0.55);
      r = lerp(r, shade[0], gap * 0.75);
      g = lerp(g, shade[1], gap * 0.75);
      b = lerp(b, shade[2], gap * 0.75);

      // A faint glow in the seams, which is what sells the arena as built
      // rather than painted.
      const seamGlow = gap * 0.5;
      setPixel(f, i, lerp(r, accent[0], seamGlow * 0.35), lerp(g, accent[1], seamGlow * 0.35), lerp(b, accent[2], seamGlow * 0.35));
      addEmissive(f, i, accent[0] * seamGlow * 0.22, accent[1] * seamGlow * 0.22, accent[2] * seamGlow * 0.22);
      f.height[i] = h;
      f.rough[i] = clamp01(0.8 + dirt * 0.15 - seamGlow * 0.25);
    }
  }
}

const RECIPES: Record<SurfaceKind, (f: Fields, s: SurfaceSpec) => void> = {
  panel,
  rock,
  ice,
  cobble,
  circuit,
  crate,
  tiles,
};

/* -------------------------------------------------------------------------- */
/* Assembly                                                                    */
/* -------------------------------------------------------------------------- */

function toTexture(data: Uint8ClampedArray, size: number, srgb: boolean): THREE.Texture {
  const canvas = document.createElement('canvas');
  canvas.width = size;
  canvas.height = size;
  const ctx = canvas.getContext('2d')!;
  const img = ctx.createImageData(size, size);
  img.data.set(data);
  ctx.putImageData(img, 0, 0);
  const tex = new THREE.CanvasTexture(canvas);
  tex.wrapS = tex.wrapT = THREE.RepeatWrapping;
  tex.anisotropy = 4;
  if (srgb) tex.colorSpace = THREE.SRGBColorSpace;
  return tex;
}

/** Central-difference normal map derived from the height field. */
function normalFrom(height: Float32Array, size: number, strength: number): Uint8ClampedArray {
  const out = new Uint8ClampedArray(size * size * 4);
  for (let y = 0; y < size; y++) {
    for (let x = 0; x < size; x++) {
      const dx = (at(height, size, x + 1, y) - at(height, size, x - 1, y)) * strength;
      const dy = (at(height, size, x, y + 1) - at(height, size, x, y - 1)) * strength;
      const len = Math.hypot(dx, dy, 1);
      const i = (y * size + x) * 4;
      out[i] = ((-dx / len) * 0.5 + 0.5) * 255;
      out[i + 1] = ((-dy / len) * 0.5 + 0.5) * 255;
      out[i + 2] = (1 / len) * 0.5 * 255 + 127.5;
      out[i + 3] = 255;
    }
  }
  return out;
}

const cache = new Map<string, SurfaceMaps>();

/** Builds (and caches) the texture set for a surface. */
export function buildSurface(spec: SurfaceSpec): SurfaceMaps {
  const size = spec.size ?? 256;
  const key = `${spec.kind}|${spec.base}|${spec.shade}|${spec.accent}|${spec.seed}|${size}|${spec.relief ?? 1}`;
  const hit = cache.get(key);
  if (hit) return hit;

  const f = makeFields(size);
  RECIPES[spec.kind](f, spec);

  const albedo = new Uint8ClampedArray(size * size * 4);
  const rough = new Uint8ClampedArray(size * size * 4);
  const emissive = new Uint8ClampedArray(size * size * 4);
  for (let i = 0; i < size * size; i++) {
    albedo[i * 4] = clamp01(f.albedo[i * 3]) * 255;
    albedo[i * 4 + 1] = clamp01(f.albedo[i * 3 + 1]) * 255;
    albedo[i * 4 + 2] = clamp01(f.albedo[i * 3 + 2]) * 255;
    albedo[i * 4 + 3] = 255;
    const rv = clamp01(f.rough[i]) * 255;
    rough[i * 4] = rv;
    rough[i * 4 + 1] = rv;
    rough[i * 4 + 2] = rv;
    rough[i * 4 + 3] = 255;
    emissive[i * 4] = clamp01(f.emissive[i * 3]) * 255;
    emissive[i * 4 + 1] = clamp01(f.emissive[i * 3 + 1]) * 255;
    emissive[i * 4 + 2] = clamp01(f.emissive[i * 3 + 2]) * 255;
    emissive[i * 4 + 3] = 255;
  }

  const maps: SurfaceMaps = {
    map: toTexture(albedo, size, true),
    normalMap: toTexture(normalFrom(f.height, size, 16 * (spec.relief ?? 1)), size, false),
    roughnessMap: toTexture(rough, size, false),
    emissiveMap: f.hasEmissive ? toTexture(emissive, size, true) : null,
    dispose() {
      this.map.dispose();
      this.normalMap.dispose();
      this.roughnessMap.dispose();
      this.emissiveMap?.dispose();
    },
  };
  cache.set(key, maps);
  return maps;
}

/**
 * Clones a surface's textures with a different tiling. The floor needs one
 * texture instance per two world units; a block face needs exactly one.
 */
export function repeated(maps: SurfaceMaps, rx: number, ry: number): SurfaceMaps {
  const copy = <T extends THREE.Texture | null>(t: T): T => {
    if (!t) return t;
    const c = t.clone() as THREE.Texture;
    c.needsUpdate = true;
    c.wrapS = c.wrapT = THREE.RepeatWrapping;
    c.repeat.set(rx, ry);
    return c as T;
  };
  return {
    map: copy(maps.map),
    normalMap: copy(maps.normalMap),
    roughnessMap: copy(maps.roughnessMap),
    emissiveMap: copy(maps.emissiveMap),
    dispose() {
      this.map.dispose();
      this.normalMap.dispose();
      this.roughnessMap.dispose();
      this.emissiveMap?.dispose();
    },
  };
}

import * as THREE from 'three/webgpu';
import {
  cameraProjectionMatrix,
  instancedBufferAttribute,
  modelViewMatrix,
  positionGeometry,
  texture as textureNode,
  uniform,
  uv,
  vec3,
  vec4,
} from 'three/tsl';

/**
 * A single pooled GPU sprite cloud used for every spark, ember and chunk of
 * debris in the game. One draw call, CPU-side integration.
 *
 * Instanced quads rather than `THREE.Points`, because WebGPU only supports
 * one-pixel point primitives -- `PointsNodeMaterial` says so itself, and every
 * particle in the game would have become a single dim pixel. Each instance
 * carries its own centre, colour and size, and the quad is turned to face the
 * camera in view space, which is what `gl_PointSize` was doing implicitly.
 */
export class ParticleSystem {
  readonly points: THREE.Mesh;
  private capacity: number;
  private pos: Float32Array;
  private col: Float32Array;
  private siz: Float32Array;
  private vel: Float32Array;
  private life: Float32Array;
  private maxLife: Float32Array;
  private drag: Float32Array;
  private gravity: Float32Array;
  private cursor = 0;
  private geometry: THREE.InstancedBufferGeometry;
  private iPos: THREE.InstancedBufferAttribute;
  private iCol: THREE.InstancedBufferAttribute;
  private iSiz: THREE.InstancedBufferAttribute;
  private scaleU = uniform(1);

  constructor(capacity: number, texture: THREE.Texture) {
    this.capacity = capacity;
    this.pos = new Float32Array(capacity * 3);
    this.col = new Float32Array(capacity * 3);
    this.siz = new Float32Array(capacity);
    this.vel = new Float32Array(capacity * 3);
    this.life = new Float32Array(capacity);
    this.maxLife = new Float32Array(capacity);
    this.drag = new Float32Array(capacity);
    this.gravity = new Float32Array(capacity);

    /*
     * A unit quad, instanced `capacity` times.
     *
     * The per-instance arrays are the same ones the integrator already writes,
     * handed to the GPU as instanced attributes rather than vertex attributes,
     * so nothing about the CPU side changes.
     */
    const geo = new THREE.InstancedBufferGeometry();
    const quad = new THREE.PlaneGeometry(1, 1);
    geo.setAttribute('position', quad.getAttribute('position'));
    geo.setAttribute('uv', quad.getAttribute('uv'));
    geo.setIndex(quad.getIndex());
    quad.dispose();
    this.iPos = new THREE.InstancedBufferAttribute(this.pos, 3);
    this.iCol = new THREE.InstancedBufferAttribute(this.col, 3);
    this.iSiz = new THREE.InstancedBufferAttribute(this.siz, 1);
    geo.setAttribute('iPos', this.iPos);
    geo.setAttribute('iCol', this.iCol);
    geo.setAttribute('iSiz', this.iSiz);
    geo.instanceCount = capacity;
    this.geometry = geo;

    /*
     * The billboard, in view space.
     *
     * `gl_PointSize = psize * dpr * 300 / -z` was a world-sized sprite
     * expressed the long way round: multiply it by the world-units-per-pixel
     * at that depth and the depth cancels, leaving `psize * 600 / (P11 * h)`,
     * where `h` is the canvas height in CSS pixels and `P11` the projection
     * matrix's `[1][1]`. The device pixel ratio cancels too, which is why
     * there is no longer anything for `setPixelRatio` to do.
     *
     * `setSpriteScale` carries that factor in, from the one place that knows
     * both the camera and the canvas. Reproducing it rather than choosing a
     * constant is the difference between a port and a redesign.
     *
     * The corner offset is added to the instance centre *after* the model-view
     * transform, so the quad always faces the camera.
     */
    const material = new THREE.MeshBasicNodeMaterial({
      // Carries over the old shader's `if (tex.a < 0.02) discard;` -- overdraw
      // on big overlapping additive quads is what this costs on a phone.
      alphaTest: 0.02,
      transparent: true,
      depthWrite: false,
      blending: THREE.AdditiveBlending,
    });
    const centre = modelViewMatrix.mul(vec4(instancedBufferAttribute(this.iPos, 'vec3'), 1));
    const corner = positionGeometry.xy
      .mul(instancedBufferAttribute(this.iSiz, 'float'))
      .mul(this.scaleU);
    material.vertexNode = cameraProjectionMatrix.mul(
      vec4(centre.xyz.add(vec3(corner, 0)), 1),
    );
    const tex = textureNode(texture, uv());
    material.colorNode = vec4(instancedBufferAttribute(this.iCol, 'vec3').mul(tex.rgb), tex.a);
    // A dead particle is sized zero, which collapses its quad to a point and
    // draws nothing -- the same trick the point cloud used.

    this.points = new THREE.Mesh(geo, material);
    this.points.frustumCulled = false;
    this.points.renderOrder = 5;
  }

  /** World units per unit of `psize`. See the material comment for where it comes from. */
  setSpriteScale(f: number): void {
    this.scaleU.value = f;
  }

  /** Emits `count` particles from a point with randomised velocity. */
  burst(
    x: number,
    y: number,
    z: number,
    count: number,
    opts: {
      color: THREE.Color;
      colorJitter?: number;
      speed: number;
      speedJitter?: number;
      size: number;
      sizeJitter?: number;
      life: number;
      lifeJitter?: number;
      gravity?: number;
      drag?: number;
      spread?: number;
      upBias?: number;
    },
  ): void {
    const {
      color,
      colorJitter = 0.12,
      speed,
      speedJitter = 0.5,
      size,
      sizeJitter = 0.4,
      life,
      lifeJitter = 0.4,
      gravity = -6,
      drag = 2.4,
      spread = 1,
      upBias = 0.4,
    } = opts;

    for (let n = 0; n < count; n++) {
      const i = this.cursor;
      this.cursor = (this.cursor + 1) % this.capacity;

      const theta = Math.random() * Math.PI * 2;
      const phi = Math.acos(1 - Math.random() * spread);
      const s = speed * (1 + (Math.random() - 0.5) * 2 * speedJitter);

      this.pos[i * 3] = x;
      this.pos[i * 3 + 1] = y;
      this.pos[i * 3 + 2] = z;
      this.vel[i * 3] = Math.sin(phi) * Math.cos(theta) * s;
      this.vel[i * 3 + 1] = Math.cos(phi) * s + upBias * s;
      this.vel[i * 3 + 2] = Math.sin(phi) * Math.sin(theta) * s;

      const j = 1 + (Math.random() - 0.5) * 2 * colorJitter;
      this.col[i * 3] = color.r * j;
      this.col[i * 3 + 1] = color.g * j;
      this.col[i * 3 + 2] = color.b * j;

      this.siz[i] = size * (1 + (Math.random() - 0.5) * 2 * sizeJitter);
      this.maxLife[i] = life * (1 + (Math.random() - 0.5) * 2 * lifeJitter);
      this.life[i] = this.maxLife[i];
      this.gravity[i] = gravity;
      this.drag[i] = drag;
    }
  }

  update(dt: number): void {
    const { pos, vel, life, maxLife, siz, col } = this;
    for (let i = 0; i < this.capacity; i++) {
      if (life[i] <= 0) {
        if (siz[i] !== 0) siz[i] = 0;
        continue;
      }
      life[i] -= dt;
      if (life[i] <= 0) {
        siz[i] = 0;
        continue;
      }
      const d = Math.max(0, 1 - this.drag[i] * dt);
      vel[i * 3] *= d;
      vel[i * 3 + 1] = vel[i * 3 + 1] * d + this.gravity[i] * dt;
      vel[i * 3 + 2] *= d;
      pos[i * 3] += vel[i * 3] * dt;
      pos[i * 3 + 1] += vel[i * 3 + 1] * dt;
      pos[i * 3 + 2] += vel[i * 3 + 2] * dt;
      if (pos[i * 3 + 1] < 0.02) {
        pos[i * 3 + 1] = 0.02;
        vel[i * 3 + 1] *= -0.32;
      }
      const t = life[i] / maxLife[i];
      const fade = t * t;
      col[i * 3] *= 1;
      siz[i] = Math.max(0, siz[i] * 0.995);
      // Fade by shrinking; additive blending makes this read as dimming too.
      if (t < 0.5) siz[i] *= 0.94 + fade * 0.05;
    }
    this.iPos.needsUpdate = true;
    this.iCol.needsUpdate = true;
    this.iSiz.needsUpdate = true;
  }

  clear(): void {
    this.life.fill(0);
    this.siz.fill(0);
    this.iSiz.needsUpdate = true;
  }

  dispose(): void {
    this.geometry.dispose();
    (this.points.material as THREE.Material).dispose();
  }
}

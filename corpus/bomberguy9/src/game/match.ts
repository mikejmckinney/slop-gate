import { BOT_PROFILES, botInput, createBotMemory, type BotMemory } from '../shared/ai';
import { BOMB_FUSE_TICKS, DT, TICK_HZ } from '../shared/constants';
import { Rng } from '../shared/rng';
import { Sim, type PlayerSeed } from '../shared/sim';
import type { GameEvent, GameState, Input, MatchSettings } from '../shared/types';
import { sampleBalance, type BalanceSample } from '../shared/sample';
import type { RenderBomb, RenderEnemy, RenderPlayer } from '../render/renderer';
import type { ControllerId, InputManager } from './input';

export interface MatchView {
  players: RenderPlayer[];
  /** Empty except in Expedition, which is the only mode with creatures. */
  enemies: RenderEnemy[];
  bombs: RenderBomb[];
}

export interface Match {
  readonly state: GameState;
  readonly localIds: string[];
  update(dt: number, input: InputManager): void;
  view(): MatchView;
  drainEvents(): GameEvent[];
  dispose(): void;
  /** Online matches report their own latency; offline is always zero. */
  readonly ping: number;
}

interface Snapshot {
  players: Map<string, { x: number; y: number }>;
  enemies: Map<string, { x: number; y: number }>;
  bombs: Map<number, { x: number; y: number; lift: number }>;
}

const MAX_CATCHUP = 5;

/**
 * A match simulated entirely in the browser: single player, local versus,
 * survival and campaign all run through here. The same class also backs the
 * offline fallback when the network drops mid-session.
 */
export class LocalMatch implements Match {
  sim: Sim;
  readonly localIds: string[];
  readonly ping = 0;

  private controllers: Map<string, ControllerId>;
  private botMem = new Map<string, BotMemory>();
  private rng: Rng;
  private accumulator = 0;
  private pending: GameEvent[] = [];
  private prev: Snapshot = { players: new Map(), enemies: new Map(), bombs: new Map() };
  private curr: Snapshot = { players: new Map(), enemies: new Map(), bombs: new Map() };
  private alpha = 0;
  private waveSeed = 0;

  /**
   * The same five-second balance samples the room takes, for a match it never
   * sees.
   *
   * Quick Match, Local Versus and the campaign are simulated here and only a
   * summary is posted, so every balance question about how the game plays for
   * an actual person was being answered by bots. These ride along with the
   * result for anyone signed in.
   */
  readonly samples: BalanceSample[] = [];

  constructor(settings: MatchSettings, seeds: PlayerSeed[], controllers: Map<string, ControllerId>) {
    this.sim = new Sim(settings, seeds);
    this.controllers = controllers;
    this.localIds = [...controllers.keys()];
    this.rng = new Rng(settings.seed ^ 0x5f3a71);
    for (const seed of seeds) {
      if (seed.bot) this.botMem.set(seed.id, createBotMemory());
    }
    this.capture(this.prev);
    this.capture(this.curr);
  }

  /** Expedition has one human and no bot controller state to restore. */
  restoreRoom(sim: Sim): void {
    this.sim = sim;
    this.accumulator = this.alpha = 0;
    this.pending = [];
    this.capture(this.prev);
    this.capture(this.curr);
  }

  update(dt: number, input: InputManager): void {
    this.accumulator += Math.min(dt, 0.25);
    let steps = 0;
    while (this.accumulator >= DT && steps < MAX_CATCHUP) {
      this.accumulator -= DT;
      steps++;
      this.swapSnapshots();
      const inputs = this.collectInputs(input);
      const events = this.sim.step(inputs);
      if (events.length) this.pending.push(...events);
      sampleBalance(this.sim.state, this.samples);
      this.capture(this.curr);
      this.maybeSpawnWave();
    }
    if (steps === MAX_CATCHUP) this.accumulator = 0;
    this.alpha = this.accumulator / DT;
  }

  private collectInputs(input: InputManager): Map<string, Input> {
    const map = new Map<string, Input>();
    for (const [id, controller] of this.controllers) map.set(id, input.read(controller));
    for (const p of this.sim.state.players) {
      if (!p.bot) continue;
      let mem = this.botMem.get(p.id);
      if (!mem) {
        mem = createBotMemory();
        this.botMem.set(p.id, mem);
      }
      map.set(p.id, botInput(this.sim, p, mem, () => this.rng.float()));
    }
    return map;
  }

  /** Survival mode: once the arena is clear, send in a harder wave. */
  private maybeSpawnWave(): void {
    const s = this.sim.state;
    if (s.settings.mode !== 'survival' || s.phase === 'over') return;
    const botsLeft = s.players.filter((p) => p.bot && (p.alive || p.lives > 0)).length;
    if (botsLeft > 0) return;

    const wave = s.wave;
    const count = Math.min(6, 2 + Math.floor(wave / 2));
    const level = Math.min(BOT_PROFILES.length - 1, Math.floor((wave - 1) / 2));
    const seeds: PlayerSeed[] = [];
    for (let i = 0; i < count; i++) {
      this.waveSeed++;
      seeds.push({
        id: `wave${wave}-${i}`,
        name: `${BOT_PROFILES[level].name} ${i + 1}`,
        slot: (i + 2) % 8,
        team: 99,
        bot: true,
        botLevel: level,
      });
    }
    this.sim.spawnWave(seeds);
    for (const seed of seeds) this.botMem.set(seed.id, createBotMemory());
    // Reward the survivors between waves.
    for (const p of s.players) {
      if (!p.bot && p.alive) p.score += 250 * wave;
    }
  }

  private swapSnapshots(): void {
    const t = this.prev;
    this.prev = this.curr;
    this.curr = t;
  }

  private capture(into: Snapshot): void {
    into.players.clear();
    into.enemies.clear();
    into.bombs.clear();
    for (const p of this.sim.state.players) into.players.set(p.id, { x: p.x, y: p.y });
    for (const e of this.sim.state.enemies) into.enemies.set(e.id, { x: e.x, y: e.y });
    for (const b of this.sim.state.bombs) {
      into.bombs.set(b.id, { x: b.x, y: b.y, lift: liftOf(b.flight) });
    }
  }

  view(): MatchView {
    const s = this.sim.state;
    const a = this.alpha;
    const players: RenderPlayer[] = s.players.map((p) => {
      const from = this.prev.players.get(p.id);
      const to = this.curr.players.get(p.id) ?? { x: p.x, y: p.y };
      const x = from ? from.x + (to.x - from.x) * a : p.x;
      const y = from ? from.y + (to.y - from.y) * a : p.y;
      return {
        id: p.id,
        slot: p.slot,
        x,
        y,
        dir: p.dir,
        alive: p.alive,
        moving: p.moving,
        shield: s.tick < p.shieldUntil,
        ghost: s.tick < p.ghostUntil,
        invuln: s.tick < p.invulnUntil,
        local: this.controllers.has(p.id),
        name: p.name,
      };
    });

    const enemies: RenderEnemy[] = s.enemies.map((e) => {
      const from = this.prev.enemies.get(e.id);
      const to = this.curr.enemies.get(e.id) ?? { x: e.x, y: e.y };
      return {
        id: e.id,
        kind: e.kind,
        x: from ? from.x + (to.x - from.x) * a : e.x,
        y: from ? from.y + (to.y - from.y) * a : e.y,
        dir: e.dir,
        alive: e.alive,
        hurt: s.tick < e.hurtUntil,
        hunted: e.hunted,
      };
    });

    const bombs: RenderBomb[] = s.bombs.map((b) => {
      const from = this.prev.bombs.get(b.id);
      const to = this.curr.bombs.get(b.id) ?? { x: b.x, y: b.y, lift: liftOf(b.flight) };
      const x = from ? from.x + (to.x - from.x) * a : b.x;
      const y = from ? from.y + (to.y - from.y) * a : b.y;
      const lift = from ? from.lift + (to.lift - from.lift) * a : to.lift;
      const owner = s.players.find((p) => p.id === b.ownerId);
      const progress = b.fuse === Number.MAX_SAFE_INTEGER ? 0 : 1 - Math.max(0, b.fuse) / BOMB_FUSE_TICKS;
      return { id: b.id, slot: owner?.slot ?? 0, x, y, progress, remote: b.remote, lift };
    });

    return { players, enemies, bombs };
  }

  drainEvents(): GameEvent[] {
    if (!this.pending.length) return EMPTY_EVENTS;
    const out = this.pending;
    this.pending = [];
    return out;
  }

  get state(): GameState {
    return this.sim.state;
  }

  dispose(): void {
    this.pending = [];
    this.botMem.clear();
  }
}

const EMPTY_EVENTS: GameEvent[] = [];

/** Punched bombs arc; height peaks halfway through the flight. */
function liftOf(flight: number): number {
  if (flight <= 0) return 0;
  const punchTicks = Math.round(0.45 * TICK_HZ);
  const t = 1 - flight / punchTicks;
  return Math.sin(t * Math.PI) * 1.35;
}

import { BLAST_TICKS, BOMB_FUSE_TICKS, SNAPSHOT_HZ_ACTUAL, TICK_HZ } from '../shared/constants';
import {
  BFLAG,
  PFLAG,
  PROTOCOL_VERSION,
  decodePos,
  type ClientMessage,
  type LobbyPlayer,
  type ServerMessage,
  type Snapshot,
} from '../shared/protocol';
import { Sim, movePlayer, movePlayerHazard, overlapsBomb, type PlayerSeed } from '../shared/sim';
import type { GameEvent, GameState, MatchSettings, PlayerState } from '../shared/types';
import type { RenderBomb, RenderPlayer } from '../render/renderer';
import type { Match, MatchView } from '../game/match';
import type { InputManager } from '../game/input';

/** Close code the server uses when it declines a connection outright. */
export const CLOSE_REFUSED = 4409;

/**
 * An identity that survives a dropped connection.
 *
 * Every socket used to be a brand-new player: reconnect after a blip and the
 * room seated you as a stranger while a stand-in bot finished the match in your
 * bomber. The room keeps a disconnected player's seat for a short grace period
 * and matches it on this.
 */
function sessionId(): string {
  const KEY = 'bg9.session';
  try {
    const found = localStorage.getItem(KEY);
    if (found) return found;
    const made = crypto.randomUUID();
    localStorage.setItem(KEY, made);
    return made;
  } catch {
    // Private browsing with storage blocked. No reconnect identity, but the
    // game still works — you just come back as a new player, as before.
    return crypto.randomUUID();
  }
}
/** Failed connection attempts before the client stops trying by itself. */
const MAX_RETRIES = 6;

export interface NetHandlers {
  onWelcome?: (you: string, room: string, host: string) => void;
  onLobby?: (players: LobbyPlayer[], settings: MatchSettings, host: string, countdown: number) => void;
  onStart?: (settings: MatchSettings, seeds: PlayerSeed[]) => void;
  onEnd?: (winners: string[], standings: Array<{ id: string; name: string; slot: number; score: number; kills: number; deaths: number }>) => void;
  onChat?: (name: string, text: string, system: boolean) => void;
  onClose?: (reason: string) => void;
  onError?: (message: string) => void;
  onOpen?: () => void;
  /**
   * The room will not have us — it is full, or the build is too old. Distinct
   * from `onClose`, which means the link dropped and we are coming back.
   */
  onRefused?: (message: string) => void;
}

/**
 * WebSocket transport. Reconnects with backoff and keeps a round-trip estimate
 * that gets reported back so the lobby can show everyone's ping.
 */
export class NetClient {
  private ws: WebSocket | null = null;
  private handlers: NetHandlers = {};
  private room = '';
  private name = '';
  private closedByUs = false;
  private retries = 0;
  /** Last thing the server said before hanging up, used to explain a refusal. */
  private lastError = '';
  private readonly session = sessionId();
  /**
   * This page's connection, as opposed to this device's player.
   *
   * A session id alone cannot tell a reconnect from a second tab: both present
   * the same one, and the room has to answer opposite ways -- give the seat
   * back, or seat a second player. This is minted once per page load and never
   * stored, so the same value coming back is the same page returning, and a
   * different one is genuinely somebody else.
   */
  private readonly conn = crypto.randomUUID();
  private pingTimer = 0;
  private lastPingSent = 0;
  private rtt = 0;

  youId = '';
  hostId = '';
  connected = false;
  /** Latest decoded snapshot, consumed by `OnlineMatch`. */
  snapshotSink: ((snap: Snapshot) => void) | null = null;
  eventSink: ((events: GameEvent[]) => void) | null = null;

  connect(room: string, name: string, handlers: NetHandlers): void {
    this.room = room;
    this.name = name;
    this.handlers = handlers;
    this.closedByUs = false;
    this.retries = 0;
    this.lastError = '';
    this.open();
  }

  private open(): void {
    const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
    const url =
      `${proto}//${location.host}/ws?room=${encodeURIComponent(this.room)}` +
      `&name=${encodeURIComponent(this.name)}&sid=${encodeURIComponent(this.session)}` +
      `&cid=${encodeURIComponent(this.conn)}`;
    const ws = new WebSocket(url);
    this.ws = ws;

    ws.addEventListener('open', () => {
      this.connected = true;
      this.retries = 0;
      this.send({ t: 'hello', name: this.name, version: PROTOCOL_VERSION });
      this.handlers.onOpen?.();
      this.pingTimer = window.setInterval(() => {
        this.lastPingSent = performance.now();
        this.send({ t: 'ping', id: this.lastPingSent, rtt: this.rtt });
      }, 2000);
    });

    ws.addEventListener('message', (ev) => {
      let msg: ServerMessage;
      try {
        msg = JSON.parse(String(ev.data)) as ServerMessage;
      } catch {
        return;
      }
      this.dispatch(msg);
    });

    ws.addEventListener('close', (ev) => {
      this.connected = false;
      window.clearInterval(this.pingTimer);
      if (this.closedByUs) return;

      // A refusal is final. Retrying it just loops the player through
      // "Lost connection. Reconnecting…" until they give up, which is exactly
      // what a room that had filled itself with bots used to do to the second
      // device that tried to join.
      if (ev.code === CLOSE_REFUSED) {
        this.closedByUs = true;
        this.handlers.onRefused?.(this.lastError || 'The room would not let us in.');
        return;
      }

      if (this.retries >= MAX_RETRIES) {
        this.closedByUs = true;
        this.handlers.onRefused?.(this.lastError || 'Could not reach the server.');
        return;
      }

      this.handlers.onClose?.('disconnected');
      // Exponential backoff, capped, so a server restart is survivable.
      const delay = Math.min(8000, 500 * Math.pow(2, this.retries++));
      window.setTimeout(() => {
        if (!this.closedByUs) this.open();
      }, delay);
    });

    ws.addEventListener('error', () => {
      this.handlers.onError?.('Connection error');
    });
  }

  private dispatch(msg: ServerMessage): void {
    switch (msg.t) {
      case 'welcome':
        this.youId = msg.you;
        this.hostId = msg.host;
        this.handlers.onWelcome?.(msg.you, msg.room, msg.host);
        break;
      case 'lobby':
        this.hostId = msg.host;
        this.handlers.onLobby?.(msg.players, msg.settings, msg.host, msg.countdown);
        break;
      case 'start':
        this.handlers.onStart?.(msg.settings, msg.seeds);
        break;
      case 'snap':
        this.snapshotSink?.(msg);
        break;
      case 'ev':
        this.eventSink?.(msg.e);
        break;
      case 'end':
        this.handlers.onEnd?.(msg.winners, msg.standings);
        break;
      case 'chat':
        this.handlers.onChat?.(msg.name, msg.text, msg.from === 'system');
        break;
      case 'pong':
        this.rtt = performance.now() - msg.id;
        break;
      case 'error':
        // Held onto: if the socket closes immediately afterwards, this is the
        // reason, and the close event itself carries nothing useful.
        this.lastError = msg.message;
        this.handlers.onError?.(msg.message);
        break;
    }
  }

  send(msg: ClientMessage): void {
    if (this.ws?.readyState !== WebSocket.OPEN) return;
    this.ws.send(JSON.stringify(msg));
  }

  close(): void {
    this.closedByUs = true;
    window.clearInterval(this.pingTimer);
    this.ws?.close();
    this.ws = null;
    this.connected = false;
  }

  get ping(): number {
    return Math.round(this.rtt);
  }

  get isHost(): boolean {
    return this.youId !== '' && this.youId === this.hostId;
  }
}

/* -------------------------------------------------------------------------- */
/* Online match                                                                */
/* -------------------------------------------------------------------------- */

interface SnapPlayer {
  x: number;
  y: number;
  dir: number;
  flags: number;
}

interface SnapFrame {
  tick: number;
  at: number;
  players: Map<number, SnapPlayer>;
  bombs: Map<number, { x: number; y: number; fuse: number; power: number; flags: number; slot: number }>;
}

/**
 * Starting guess for the gap between snapshots, which interpolation is paced
 * against. Derived rather than written down: it was hard-coded at 15Hz, and a
 * server that broadcasts at a different rate would have left every client
 * interpolating against a number that was simply wrong until the running
 * average pulled it straight.
 */
const SNAP_INTERVAL_MS = 1000 / SNAPSHOT_HZ_ACTUAL;

/**
 * Mirrors the authoritative simulation.
 *
 * Remote entities are interpolated between the last two snapshots; the local
 * bomber is predicted forward with the same movement code the server runs and
 * eased back toward authority whenever a snapshot disagrees. That keeps input
 * feeling instant without ever letting the client win an argument.
 */
export class OnlineMatch implements Match {
  private mirror: Sim;
  private net: NetClient;
  private prev: SnapFrame | null = null;
  private curr: SnapFrame | null = null;
  private interval = SNAP_INTERVAL_MS;
  private pending: GameEvent[] = [];
  private seq = 1;
  private inputTimer = 0;
  private predicted: PlayerState | null = null;
  private predictedTick = 0;
  private localSlot = -1;
  /*
   * `enemies` is always empty here and always will be. Creatures belong to
   * Expedition, which is a local run -- so they are never in a room's state
   * and never on the wire, and the snapshot format did not have to learn
   * about them. This satisfies `MatchView` and documents why.
   */
  private view_: MatchView = { players: [], enemies: [], bombs: [] };

  readonly localIds: string[];

  constructor(settings: MatchSettings, seeds: PlayerSeed[], net: NetClient) {
    this.mirror = new Sim(settings, seeds);
    this.mirror.state.phase = 'countdown';
    this.net = net;
    this.localIds = seeds.filter((s) => s.id === net.youId).map((s) => s.id);
    this.localSlot = seeds.find((s) => s.id === net.youId)?.slot ?? -1;

    net.snapshotSink = (snap) => this.onSnapshot(snap);
    net.eventSink = (events) => this.onEvents(events);
  }

  private onSnapshot(snap: Snapshot): void {
    const now = performance.now();
    if (this.curr) {
      const dt = now - this.curr.at;
      // Smooth the observed cadence so jitter does not distort interpolation.
      if (dt > 8 && dt < 500) this.interval = this.interval * 0.85 + dt * 0.15;
    }

    const frame: SnapFrame = { tick: snap.k, at: now, players: new Map(), bombs: new Map() };
    for (const row of snap.p) {
      frame.players.set(row[0], { x: decodePos(row[1]), y: decodePos(row[2]), dir: row[3], flags: row[4] });
    }
    for (const row of snap.b) {
      frame.bombs.set(row[0], {
        slot: row[1],
        x: decodePos(row[2]),
        y: decodePos(row[3]),
        fuse: row[4],
        power: row[5],
        flags: row[6],
      });
    }
    this.prev = this.curr ?? frame;
    this.curr = frame;

    const state = this.mirror.state;
    state.tick = snap.k;
    state.phase = snap.phase;
    state.timeLeft = snap.time;
    state.wave = snap.wave;

    for (let i = 0; i < snap.g.length; i += 2) state.grid[snap.g[i]] = snap.g[i + 1];
    for (let i = 0; i < snap.i.length; i += 2) state.items[snap.i[i]] = snap.i[i + 1];

    // Authoritative player stats drive the HUD directly.
    for (const row of snap.p) {
      const p = state.players.find((x) => x.slot === row[0]);
      if (!p) continue;
      p.dir = row[3] as typeof p.dir;
      p.alive = (row[4] & PFLAG.ALIVE) !== 0;
      p.moving = (row[4] & PFLAG.MOVING) !== 0;
      p.kick = (row[4] & PFLAG.KICK) !== 0;
      p.punch = (row[4] & PFLAG.PUNCH) !== 0;
      p.remote = (row[4] & PFLAG.REMOTE) !== 0;
      p.pierce = (row[4] & PFLAG.PIERCE) !== 0;
      p.shieldUntil = row[4] & PFLAG.SHIELD ? snap.k + 1 : 0;
      p.ghostUntil = row[18];
      p.invulnUntil = row[4] & PFLAG.INVULN ? snap.k + 1 : 0;
      p.charged = Boolean(row[4] & PFLAG.CHARGED);
      p.hp = row[12];
      p.resolve = row[13] / 100;
      p.lives = row[5];
      p.maxBombs = row[6];
      p.power = row[7];
      p.speedLevel = row[8];
      p.curse = row[9] as typeof p.curse;
      p.curseUntil = row[19];
      p.carryUntil = row[14];
      p.carryVx = row[15];
      p.carryVy = row[16];
      p.launched = Boolean(row[17]);
      p.kills = row[10];
      p.score = row[11];
    }

    // Mirror bombs so prediction can collide against them.
    state.bombs = snap.b.map((row) => ({
      id: row[0],
      ownerId: state.players.find((p) => p.slot === row[1])?.id ?? '',
      x: decodePos(row[2]),
      y: decodePos(row[3]),
      fuse: row[4] < 0 ? Number.MAX_SAFE_INTEGER : row[4],
      power: row[5],
      pierce: (row[6] & BFLAG.PIERCE) !== 0,
      remote: (row[6] & BFLAG.REMOTE) !== 0,
      vx: 0,
      vy: 0,
      flight: row[6] & BFLAG.FLYING ? 1 : 0,
      flightFrom: [decodePos(row[2]), decodePos(row[3])],
      flightTo: [decodePos(row[2]), decodePos(row[3])],
      // The local player can always walk off a bomb underneath them; the
      // server is the authority on when that stops being true.
      noCollide: state.players.filter(p => {
        const pos = frame.players.get(p.slot);
        return pos && overlapsBomb(pos, { x: decodePos(row[2]), y: decodePos(row[3]) });
      }).map(p => p.id),
    }));

    const server = frame.players.get(this.localSlot);
    if (server) {
      this.predictedTick = snap.k;
      const local = state.players.find(p => p.slot === this.localSlot)!;
      if (!this.predicted) {
        this.predicted = { ...local, x: server.x, y: server.y };
      } else {
        const { x, y } = this.predicted;
        Object.assign(this.predicted, local, { x, y });
        const err = Math.hypot(server.x - this.predicted.x, server.y - this.predicted.y);
        if (err > 1.4) {
          // Something big happened (a respawn, a teleporting punch, packet
          // loss). Trust the server outright.
          this.predicted.x = server.x;
          this.predicted.y = server.y;
        } else {
          const k = err > 0.35 ? 0.42 : 0.16;
          this.predicted.x += (server.x - this.predicted.x) * k;
          this.predicted.y += (server.y - this.predicted.y) * k;
        }
      }
    }
  }

  private onEvents(events: GameEvent[]): void {
    this.pending.push(...events);
    // Keep a client-side blast list so the minimap and HUD look alive.
    for (const e of events) {
      if (e.t !== 'explode') continue;
      this.mirror.state.blasts.push({
        id: this.mirror.state.nextBlastId++,
        ownerId: '',
        // The event already carries where the bomb stood and how far it
        // reached, which is what damage falloff is measured against.
        ox: e.x,
        oy: e.y,
        power: e.power,
        cells: e.cells,
        kinds: e.kinds,
        axes: e.axes,
        ticksLeft: BLAST_TICKS,
      });
    }
  }

  update(dt: number, input: InputManager): void {
    const state = this.mirror.state;

    for (let i = state.blasts.length - 1; i >= 0; i--) {
      state.blasts[i].ticksLeft -= dt * TICK_HZ;
      if (state.blasts[i].ticksLeft <= 0) state.blasts.splice(i, 1);
    }

    const local = state.players.find((p) => p.slot === this.localSlot);
    const intent = input.read(0);

    // Integrate hazards at the same fixed tick as the server, even on a 144Hz display.
    this.inputTimer += Math.min(dt, 0.25);
    const step = 1 / TICK_HZ;
    while (this.inputTimer >= step) {
      this.inputTimer -= step;
      this.net.send({ t: 'input', seq: this.seq++, mx: intent.mx, my: intent.my, bomb: intent.bomb, action: intent.action });
      if (local && this.predicted && local.alive && state.phase !== 'countdown' && state.phase !== 'over') {
        this.predictedTick++;
        movePlayer(state, this.predicted, intent, this.predictedTick);
        movePlayerHazard(state, this.predicted, this.predictedTick);
        for (const bomb of state.bombs) {
          if (!overlapsBomb(bomb, this.predicted)) {
            bomb.noCollide = bomb.noCollide.filter(id => id !== local.id);
          }
        }
      }
    }
    if (local && !local.alive) this.predicted = null;

    this.buildView();
  }

  private buildView(): void {
    const state = this.mirror.state;
    const now = performance.now();
    const alpha = this.curr ? Math.min(1.6, Math.max(0, (now - this.curr.at) / this.interval)) : 0;

    const players: RenderPlayer[] = [];
    for (const p of state.players) {
      const a = this.prev?.players.get(p.slot);
      const b = this.curr?.players.get(p.slot);
      let x = p.x;
      let y = p.y;
      if (a && b) {
        // Extrapolate slightly past the newest snapshot rather than stalling.
        x = a.x + (b.x - a.x) * alpha;
        y = a.y + (b.y - a.y) * alpha;
      } else if (b) {
        x = b.x;
        y = b.y;
      }
      const isLocal = p.slot === this.localSlot;
      if (isLocal && this.predicted && p.alive) {
        x = this.predicted.x;
        y = this.predicted.y;
        p.dir = this.predicted.dir;
        p.moving = this.predicted.moving;
      }
      // Keep the mirror in sync so the HUD and minimap read sensible positions.
      p.x = x;
      p.y = y;
      players.push({
        id: p.id,
        slot: p.slot,
        x,
        y,
        dir: p.dir,
        alive: p.alive,
        moving: p.moving,
        shield: state.tick < p.shieldUntil,
        ghost: state.tick < p.ghostUntil,
        invuln: state.tick < p.invulnUntil,
        local: isLocal,
        name: p.name,
      });
    }

    const bombs: RenderBomb[] = [];
    for (const [id, b] of this.curr?.bombs ?? []) {
      const a = this.prev?.bombs.get(id);
      const x = a ? a.x + (b.x - a.x) * alpha : b.x;
      const y = a ? a.y + (b.y - a.y) * alpha : b.y;
      bombs.push({
        id,
        slot: b.slot,
        x,
        y,
        progress: b.fuse < 0 ? 0 : 1 - Math.max(0, b.fuse) / BOMB_FUSE_TICKS,
        remote: (b.flags & BFLAG.REMOTE) !== 0,
        lift: b.flags & BFLAG.FLYING ? 1.1 : 0,
      });
    }

    this.view_ = { players, enemies: [], bombs };
  }

  view(): MatchView {
    return this.view_;
  }

  drainEvents(): GameEvent[] {
    if (!this.pending.length) return [];
    const out = this.pending;
    this.pending = [];
    return out;
  }

  get state(): GameState {
    return this.mirror.state;
  }

  get ping(): number {
    return this.net.ping;
  }

  dispose(): void {
    this.net.snapshotSink = null;
    this.net.eventSink = null;
    this.pending = [];
  }
}

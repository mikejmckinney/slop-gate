import { audio } from './audio/manager';
import { InputManager, bindTouchControls } from './game/input';
import { LocalMatch, type Match } from './game/match';
import { Training } from './game/training';
import { NetClient, OnlineMatch } from './net/client';
import { loadBomberModel, setBomberModelParts } from './render/bomberModel';
import { beginBoot, type BootGuard } from './ui/bootguard';
import { BomberPreview } from './render/preview';
import {
  GameRenderer,
  gpuQuirks,
  gpuRenderer,
  tierFor,
  bomberModelEnabled,
  setQualityOverrides,
  setSafeMode,
  type Quality,
  type QualityTier,
} from './render/renderer';
import { BOT_PROFILES } from './shared/ai';
import { Item, SLOT_COLORS } from './shared/constants';
import {
  ARENAS,
  arenaCapacity,
  arenaVariant,
  getArena,
  seedForLook,
  type ArenaDef,
} from './shared/maps';
import { BOMBERS } from './shared/constants';
import { Sim, type PlayerGear, type PlayerSeed } from './shared/sim';
import type { SimCheckpoint } from './shared/checkpoint';
import { Progression, browserStorage } from './ui/progression';
import type { GameEvent, MatchSettings, WinCondition } from './shared/types';
import {
  EXPEDITION_BRANCHES,
  EXPEDITION_ROOMS,
  EXPEDITION_RULES,
  UNLOCKS,
  GOAL_GLYPH,
  GOAL_TEXT,
  isFinalRoom,
  playableRoom,
  roomDetail,
  rollBranches,
  rollRoom,
  roomSeconds,
  salvageFor,
  startingGear,
  type RoomDef,
} from './shared/expedition';
import { trialSettings } from './shared/trial';
import { Hud } from './ui/hud';
import { Screens, type BoardScope, type SetupContext } from './ui/screens';
import {
  judge as judgeQuality,
  newState as newAutoState,
  type AutoState,
} from './ui/autoquality';
import * as haptics from './ui/haptics';
import * as pressure from './ui/pressure';
import { detectQuality, loadPrefs, randomName, savePrefs, type Prefs } from './ui/store';
import { startPerf, type PerfRun } from './ui/perf';
import { toast } from './ui/toast';
import { setupAutoUpdate, setupInstallPrompt } from './pwa/update';

declare const __APP_VERSION__: string;
declare const __BUILD_ID__: string;

/* -------------------------------------------------------------------------- */
/* Bootstrap                                                                   */
/* -------------------------------------------------------------------------- */

const canvas = document.getElementById('scene') as HTMLCanvasElement;
const hudRoot = document.getElementById('hud') as HTMLElement;
const touchRoot = document.getElementById('touch') as HTMLElement;
let touch: ReturnType<typeof bindTouchControls> | null = null;

/**
 * Shows or hides the on-screen controls, clearing anything still held.
 *
 * Always go through this rather than setting `hidden` directly: hiding the
 * layer while a finger is on the stick strands that pointer, and the stick
 * stays dead until the page is reloaded.
 */
function showTouch(visible: boolean): void {
  // Only on a real change, and only ever clearing on the way *out*. Resetting
  // on show meant the `touchstart` listener below — which fires on every touch
  // of the screen, right after the `pointerdown` that grabbed the stick — tore
  // the stick down again a moment after the player took hold of it. That killed
  // touch control outright on both iOS and Android.
  if (touchRoot.hidden === !visible) return;
  if (!visible) touch?.reset();
  touchRoot.hidden = !visible;
}
const uiRoot = document.getElementById('ui') as HTMLElement;

let prefs = loadPrefs();
const progression = new Progression(browserStorage(), { salvage: prefs.salvage, unlocks: prefs.unlocks,
  bestRoom: prefs.bestExpedition, runs: prefs.expeditionRuns, clears: prefs.expeditionClears },
  applyProgression, message => toast(message, { tone: 'info', key: 'progress-sync' }));
const input = new InputManager();
const hud = new Hud(hudRoot);
const net = new NetClient();
/**
 * Whether this load is a measurement, which changes two things that would
 * otherwise decide what a `?perf=` run measured without saying so.
 *
 * The boot guard keeps a device on the reduced settings that last worked for
 * it, which is right for playing and wrong for measuring: a sweep would
 * inherit whatever step a previous run left behind and report the numbers
 * under the tier in its header. Its third step is `detail: 0`, which draws no
 * scenery ring at all, so a ladder could measure props that were not there. A
 * measurement starts the guard clean, and records the step it ran at.
 *
 * And the service worker holds a waiting update until the player accepts it --
 * but a sweep hides the UI, so the offer could never be seen, and the run
 * would measure whatever build was already cached. A measurement takes the
 * reload instead.
 */
const MEASURING: boolean = (() => {
  try {
    return new URLSearchParams(location.search).has('perf');
  } catch {
    return false;
  }
})();

const updater = setupAutoUpdate(__BUILD_ID__, { auto: MEASURING });


let renderer: GameRenderer;
let guard: BootGuard | null = null;
/** Frames since boot, so the guard can tell drawing from a stopped tab. */
let framesDrawn = 0;
let preview: BomberPreview | null = null;
let match: Match | null = null;
let attract: LocalMatch | null = null;
let training: Training | null = null;
let screens: Screens;
let mode: 'menu' | 'playing' | 'paused' = 'menu';
let matchKind: SetupContext['kind'] = 'solo';
let matchStartedAt = 0;
let campaignStage = 0;
let onlineRoom = '';
/** Serialised settings we last pushed to the room, to avoid an echo loop. */
let pushedSettings = '';
let pushedBots = '';
let lastFrame = performance.now();
let fixedStep: number | null = null;
let resultsShown = false;
let endTimer: number | null = null;
let fpsAccum = 0;
let fpsFrames = 0;
/** The first sampling window is boot cost, not frame rate. See `watchdog`. */
let fpsWarmedUp = false;
/**
 * The tier `auto` is currently running, once measurement has moved it.
 *
 * Without this, `resolveQuality()` kept answering with `detectQuality()` --
 * the tier that was *guessed* -- so a second drop recomputed the next rung
 * from the original guess and set the same tier it had already set. The
 * device had two lives and spent one of them standing still.
 */
let autoTier: Quality | null = null;
/** The climbing and dropping loop's state; see ui/autoquality.ts. */
let auto: AutoState | null = null;

/*
 * What this device learned last time, if it was the same build and this is
 * not a measurement.
 *
 * A measurement starts from the guess rather than from what a previous
 * session settled on, for the same reason it resets the boot guard: a run
 * that inherits state reports numbers under a tier its header did not name.
 */
if (!MEASURING && prefs.autoBuild === __BUILD_ID__ && prefs.quality === 'auto') {
  autoTier = prefs.autoTier;
}
/** The on-device frame-cost readout, when `?perf=` asked for one. */
let perf: PerfRun | null = null;

const CAMPAIGN_STAGES: Array<{ map: string; bots: number; level: number; lives: number; label: string }> = [
  { map: 'neon-grid', bots: 1, level: 0, lives: 3, label: 'First Sparks' },
  { map: 'neon-grid', bots: 2, level: 0, lives: 2, label: 'Crossfire' },
  { map: 'overgrowth', bots: 2, level: 1, lives: 2, label: 'Into the Green' },
  { map: 'glacier', bots: 3, level: 1, lives: 2, label: 'Cold Corridors' },
  { map: 'magma-core', bots: 3, level: 2, lives: 1, label: 'Open Season' },
  { map: 'circuit', bots: 4, level: 2, lives: 1, label: 'Short Circuit' },
  { map: 'the-void', bots: 3, level: 2, lives: 1, label: 'No Cover' },
  { map: 'circuit', bots: 5, level: 3, lives: 1, label: 'Overload' },
  { map: 'the-void', bots: 3, level: 3, lives: 1, label: 'Nightmare' },
];

interface Expedition {
  id: string;
  rooms: RoomDef[];
  index: number;
  /** Gear at room entry; live pickups are in the checkpoint. */
  gear: PlayerGear;
  parked: boolean;
  choices: RoomDef[];
  cleared: number;
  roomSeed?: number;
  checkpoint?: SimCheckpoint;
}

let expedition: Expedition | null = null;

// Keep the legacy address; checkpoints carry their own version.
const RUN_KEY = 'bg9.run.v1';

function saveExpedition(): void {
  try {
    if (expedition) localStorage.setItem(RUN_KEY, JSON.stringify(expedition));
    else localStorage.removeItem(RUN_KEY);
  } catch {
    toast('This browser could not save the run. Keep this tab open to continue.', { tone: 'bad', key: 'run-save' });
  }
}

let savedRoomTick = -1;
function checkpointExpedition(): void {
  if (!expedition || expedition.choices.length || !(match instanceof LocalMatch) ||
    matchKind !== 'adventure' || match.state.settings.seed !== expedition.roomSeed) return;
  if (match.state.phase === 'over' && match.state.winners.includes('local-0')) {
    expedition.cleared = Math.max(expedition.cleared, expedition.index + 1);
  }
  expedition.checkpoint = match.sim.checkpoint();
  savedRoomTick = match.state.tick;
  saveExpedition();
}

/**
 * Takes only what `PlayerGear` declares, and only where the type matches.
 *
 * `createPlayer` already clamps every number it is handed, so the range is
 * somebody else's job. What it cannot defend against is a *string*: it
 * survives `Math.floor` as `NaN`, and `Math.max(lo, Math.min(hi, NaN))` is
 * `NaN`, which then spreads through a player's speed for the rest of the run.
 */
function readGear(raw: unknown): PlayerGear {
  const g = (raw ?? {}) as Record<string, unknown>;
  const num = (v: unknown) => (typeof v === 'number' && Number.isFinite(v) ? v : undefined);
  const bool = (v: unknown) => v === true;
  return {
    maxBombs: num(g.maxBombs),
    power: num(g.power),
    speedLevel: num(g.speedLevel),
    kick: bool(g.kick),
    punch: bool(g.punch),
    remote: bool(g.remote),
    pierce: bool(g.pierce),
  };
}

/**
 * Reads a saved run back, and refuses one it cannot play.
 *
 * The threat model here is not a tamperer, it is our own future. A run names
 * arenas by id and bot profiles by index, and resuming one is now part of
 * *boot* -- so an arena retired or a profile dropped between the save and the
 * resume would take the game down before the menu ever appeared, on a device
 * whose owner has no idea there is a stale run behind it. Every field that
 * gets dereferenced later is therefore checked here, and anything that fails
 * any of it discards the run rather than half-playing it.
 *
 * The version in `RUN_KEY` handles a change of *shape*; this handles a change
 * of *content*, which a version bump cannot see coming.
 */
function loadExpedition(): Expedition | null {
  try {
    const raw = localStorage.getItem(RUN_KEY);
    if (!raw) return null;
    const run = JSON.parse(raw) as Expedition;
    // An upper bound rather than the current room count, so tuning the length
    // of a run does not throw away every run already in progress.
    if (!Array.isArray(run.rooms) || !run.rooms.length || run.rooms.length > 64) return null;
    if (!Number.isInteger(run.index) || run.index < 0 || run.index >= run.rooms.length) return null;
    // The doors get the same scrutiny as the route: a saved choice is entered
    // the moment it is picked, so an unplayable one is a crash a tap away.
    if (!Array.isArray(run.choices) || run.choices.length > EXPEDITION_BRANCHES) return null;
    for (const r of [...run.rooms, ...run.choices]) {
      if (!playableRoom(r)) return null;
    }
    if (run.checkpoint && (!Sim.restore(run.checkpoint) || run.checkpoint.state.players.length !== 1 ||
      run.checkpoint.state.players[0].id !== 'local-0' || run.checkpoint.state.players[0].bot ||
      run.checkpoint.state.settings.mapId !== run.rooms[run.index].map ||
      run.checkpoint.state.settings.roomGoal !== run.rooms[run.index].goal)) return null;
    run.id = typeof run.id === 'string' && /^[\w:-]{1,80}$/.test(run.id) ? run.id : crypto.randomUUID();
    // Old route-only saves can only resume at their recorded room entrance.
    run.cleared = Number.isInteger(run.cleared) ? Math.max(0, Math.min(run.index + 1, run.cleared)) :
      run.index + Number(run.choices.length > 0);
    run.roomSeed = run.checkpoint?.state.settings.seed ?? (Number.isInteger(run.roomSeed) ? run.roomSeed : undefined);
    run.gear = readGear(run.gear);
    run.parked = run.parked === true;
    return run;
  } catch {
    return null;
  }
}

/** The kill feed's colour for creatures: not a player slot, so not a slot colour. */
const ENEMY_FEED_COLOR = 0xd8465e;

/** Draws the outfitting screen from what the player has earned. */
function showOutfit(): void {
  const saved = loadExpedition();
  screens.showOutfit({
    salvage: prefs.salvage,
    runs: prefs.expeditionRuns,
    clears: prefs.expeditionClears,
    best: prefs.bestExpedition,
    parked: saved ? { room: saved.index + 1, total: EXPEDITION_ROOMS } : null,
    rows: UNLOCKS.map((u) => ({
      code: u.code,
      name: u.name,
      blurb: u.blurb,
      cost: u.cost,
      owned: prefs.unlocks.includes(u.code),
      afford: prefs.salvage >= u.cost,
    })),
  });
}

/**
 * Spends salvage on an unlock.
 *
 * Checked here rather than trusted from the button, because the button is
 * disabled in the DOM and a disabled button is a hint, not a rule.
 */
function buyUnlock(code: string): void {
  const unlock = UNLOCKS.find((u) => u.code === code);
  if (!unlock) return;
  if (prefs.unlocks.includes(code) || prefs.salvage < unlock.cost) return;
  progression.add({ id: crypto.randomUUID(), type: 'buy', code });
  applyProgression();
  audio.play('pickup');
  toast(`${unlock.name} unlocked.`, { tone: 'good' });
  void syncExpedition();
  showOutfit();
}

function startExpedition(): void {
  // Room one is not a choice. Offering three doors to a player who has never
  // seen a room is asking them to decide with nothing to decide on.
  const first = rollRoom(0, []);
  // What was bought is what room one starts with. It goes through `PlayerGear`
  // like a carried pickup, so the same clamps apply and the run's own drops
  // stack on top of it normally.
  expedition = { id: crypto.randomUUID(), rooms: [first], index: 0, gear: startingGear(prefs.unlocks), choices: [], parked: false, cleared: 0 };
  progression.add({ id: expedition.id + ':start', type: 'start' });
  applyProgression();
  void syncExpedition();
  saveExpedition();
  enterExpeditionRoom();
}

/**
 * Offers the branch after a cleared room, or ends a completed run.
 *
 * Returns true when it has taken over -- the caller must not show a scoreboard
 * for a run that is still going.
 */
function offerBranch(): boolean {
  const run = expedition;
  if (!run) return false;
  const next = run.index + 1;
  if (next >= EXPEDITION_ROOMS) return false;
  /*
   * The last room is announced, not chosen.
   *
   * Three doors onto three versions of the same boss is a choice of wallpaper,
   * and offering one would say the ending is optional. Everything up to here
   * is a route the player picked; this is where the route runs out.
   */
  if (isFinalRoom(next)) {
    run.rooms.push(rollRoom(next, [run.rooms[run.index].map]));
    run.index = next;
    run.choices = [];
    run.checkpoint = undefined;
    run.roomSeed = undefined;
    saveExpedition();
    enterExpeditionRoom();
    return true;
  }
  run.checkpoint = undefined;
  run.choices = rollBranches(next, run.rooms[run.index]);
  saveExpedition();
  showBranch();
  return true;
}

/** Draws the route screen for whatever branch is currently on offer. */
function showBranch(): void {
  const run = expedition;
  if (!run?.choices.length) return;
  mode = 'menu';
  hud.show(false);
  showTouch(false);
  screens.showRoomChoice({
    depth: run.index + 1,
    total: EXPEDITION_ROOMS,
    taken: run.rooms.map((r) => r.label),
    choices: run.choices.map((c) => ({
      goal: GOAL_TEXT[c.goal],
      glyph: GOAL_GLYPH[c.goal],
      arena: c.label,
      detail: roomDetail(c),
    })),
  });
}

/** Takes the door the player picked. */
function takeBranch(i: number): void {
  const run = expedition;
  const pick = run?.choices[i];
  if (!run || !pick) return;
  run.rooms.push(pick);
  run.index += 1;
  run.choices = [];
  run.checkpoint = undefined;
  run.roomSeed = undefined;
  saveExpedition();
  enterExpeditionRoom();
}

/** Park the live room without concluding or paying out the run. */
function parkExpedition(): void {
  const run = expedition;
  if (!run) return;
  checkpointExpedition();
  run.parked = true;
  saveExpedition();
  expedition = null;
  toast(`Run parked at room ${run.index + 1}. Resume it from Expedition.`, { tone: 'info' });
}

function endExpedition(finished = false): void {
  const run = expedition;
  if (!run) return;
  const reached = run.index + 1;
  const earned = salvageFor(run.cleared, finished);
  const best = prefs.bestExpedition;
  progression.add({ id: run.id + ':finish', type: 'finish', cleared: run.cleared, reached, finished });
  applyProgression();
  if (reached > best) toast(`Deepest run yet: room ${reached}.`, { tone: 'good' });
  if (earned) toast(`+${earned} salvage.`, { tone: 'good' });
  expedition = null;
  saveExpedition();
  void syncExpedition();
}

function applyProgression(): void {
  const p = progression.value();
  prefs = { ...prefs, salvage: p.salvage, unlocks: p.unlocks, bestExpedition: p.bestRoom,
    expeditionRuns: p.runs, expeditionClears: p.clears };
  savePrefs(prefs);
  screens?.setPrefs(prefs);
  if (screens?.screen === 'outfit') showOutfit();
}

async function syncExpedition(): Promise<void> {
  if (signedInUser) await progression.sync(signedInUser);
}

/** Picks up a run that a reload interrupted, if there is one. */
function resumeExpedition(): boolean {
  const run = loadExpedition();
  if (!run) return false;
  expedition = run;
  expedition.parked = false;
  saveExpedition();
  matchKind = 'adventure';
  // Mid-decision reloads come back to the same three doors rather than to the
  // room behind one of them. Re-rolling here would turn a discarded tab into a
  // free re-draw, and dropping straight into a room would pick for them.
  if (run.choices.length) {
    showBranch();
    toast('Pick up where you left off.', { tone: 'good' });
    return true;
  }
  enterExpeditionRoom();
  toast(`Resumed at room ${run.index + 1}.`, { tone: 'good' });
  return true;
}

function enterExpeditionRoom(): void {
  const run = expedition;
  if (!run) return;
  const cfg = run.rooms[run.index];
  const mine = prefs.slot % BOMBERS.length;
  // One seed, and no free-slot juggling: the only bomber in the room is yours.
  const seeds: PlayerSeed[] = [
    { id: 'local-0', name: prefs.name, slot: mine, team: 0, bot: false, gear: run.gear },
  ];
  run.roomSeed ??= rollSeed(cfg.map);
  startLocalMatch(
    seeds,
    // Room objectives and timers override the unrelated match setup.
    buildSettings(
      {
        seed: run.roomSeed,
        mapId: cfg.map,
        mode: 'ffa',
        lives: 1,
        roundSeconds: roomSeconds(cfg.goal, cfg.enemies),
        winBy: 'lives',
        roomGoal: cfg.goal,
        enemies: cfg.enemies,
      },
      EXPEDITION_RULES,
    ),
    run.checkpoint ? Sim.restore(run.checkpoint) ?? undefined : undefined,
  );
  checkpointExpedition();
  hud.banner(
    `ROOM ${run.index + 1} / ${EXPEDITION_ROOMS} — ${GOAL_TEXT[cfg.goal]}`,
    2.4,
  );
  /*
   * The exit gets the training drills' floor marker, which already exists and
   * already pulses and beams. An objective the player has to be told about in
   * a banner that fades after two seconds is an objective they will lose a run
   * to; this one stays on screen because it is a place.
   */
  renderer.setMarkers(match && match.state.exitAt >= 0 ? [match.state.exitAt] : []);
}

/**
 * What the player carries out of a room they cleared.
 *
 * Read off the live player rather than tracked alongside it, so a pickup added
 * to the game later is carried without anybody remembering to add it here.
 */
function captureGear(): PlayerGear {
  const me = match?.state.players.find((p) => (match?.localIds ?? []).includes(p.id));
  if (!me) return expedition?.gear ?? {};
  return {
    maxBombs: me.maxBombs,
    power: me.power,
    speedLevel: me.speedLevel,
    kick: me.kick,
    punch: me.punch,
    remote: me.remote,
    pierce: me.pierce,
  };
}

/**
 * Called when a room ends. Returns true if the run continues, so the caller
 * knows not to show a scoreboard for something that is not over.
 */
function advanceExpedition(winners: string[]): boolean {
  const run = expedition;
  if (!run) return false;
  const cleared = winners.some((w) => (match?.localIds ?? []).includes(w));
  if (!cleared) return false;
  run.gear = captureGear();
  run.cleared = Math.max(run.cleared, run.index + 1);
  saveExpedition();
  // A cleared room leads to a choice, not to the next room. `offerBranch`
  // returns false only when the run is finished, which is the one case that
  // should fall through to a scoreboard.
  return offerBranch();
}

/**
 * A quality tier forced by `?quality=`, for a device that cannot reach the
 * settings screen.
 *
 * The tiers are already a bisect of the render path, which is what makes this
 * worth having: `low` draws no shadows, no bloom and no silhouette ring, and
 * `medium` puts the ring and shadows back without bloom. Three page loads on a
 * device that crashes therefore say *which subsystem* crashes it -- and that is
 * not a question anything here can answer from a machine that works.
 *
 * Deliberately not saved to preferences. It lasts as long as the URL does, so
 * it cannot strand anyone in a tier they did not choose.
 */
const FORCED_QUALITY: Quality | null = (() => {
  try {
    const asked = new URLSearchParams(location.search).get('quality');
    return asked === 'low' || asked === 'medium' || asked === 'high' || asked === 'ultra' ? asked : null;
  } catch {
    return null;
  }
})();

/**
 * Subsystem switches from `?gfx=`, applied on top of the tier.
 *
 * `?quality=` narrows a crash to a tier; six things change between two
 * neighbouring tiers, and a device that only reports "crashed" cannot say
 * which. Each of these turns exactly one of them off, so the first flag that
 * stops the crash is the answer.
 *
 *   ?quality=medium&gfx=noring        the scenery ring and its custom shader
 *   ?quality=medium&gfx=noshadows     shadow maps
 *   ?quality=medium&gfx=nolights      the dynamic bomb lights
 *   ?quality=medium&gfx=noparticles   the particle system
 *   ?quality=medium&gfx=noambience    drifting atmosphere
 *   ?quality=medium&gfx=dpr1          render at 1x instead of 1.5x
 *   ?quality=ultra&gfx=nobloom        the post-processing composer
 *   ?quality=ultra&gfx=noglow         the bloom pass, keeping the composer
 *   ?quality=ultra&gfx=noaa           multisampling on the default framebuffer
 *   ?quality=ultra&gfx=post8bit       half-float post targets, for 8-bit ones
 *   ?gfx=noprops                      circuit's solid prop ring, not the silhouettes
 *   ?gfx=near2                        near plane 0.5 -> 2, for depth precision
 *   ?gfx=hdr                          present beyond SDR, where the display allows
 *   ?gfx=hdr&hdrknee=2&hdrgain=1      and the numbers that shape it
 *   ?gfx=hdr&hdremissive=5            how hard the glowing things are driven
 *   ?gfx=nohdr                        force standard range on a display that claims HDR
 *
 * They combine, which matters: `noaa` on its own cannot say anything about
 * multisampling while bloom's thirteen render targets are still in the frame.
 * `noglow,noaa` is the run that isolates it.
 *
 * `nobloom` exists because bloom is the only thing separating a tier that
 * renders the arena in black rectangles on PowerVR from one that does not, and
 * it was the one difference with no switch. It is also the only pass that
 * reads the whole framebuffer back mid-frame, which on a tile-based renderer
 * is the operation that forces every tile to resolve -- the same shape of
 * problem as the mid-frame material recompile documented in `render/renderer`.
 */
/**
 * The two numbers the HDR look turns on, from the URL.
 *
 * `?gfx=hdr&hdrknee=2&hdrgain=1`. They are on the URL rather than in the tier
 * because neither can be chosen from a screenshot: a screenshot is standard
 * range, so the only place the difference between a knee of 2 and a knee of 6
 * exists is on a screen that can show it, in the room it will be played in.
 * A phone can reload with a different pair; it cannot open a console.
 */
function hdrNumber(name: string, min: number, max: number): number | undefined {
  try {
    const raw = new URLSearchParams(location.search).get(name);
    if (raw === null) return undefined;
    const n = Number(raw);
    return Number.isFinite(n) ? Math.min(max, Math.max(min, n)) : undefined;
  } catch {
    return undefined;
  }
}

const GFX_FLAGS: Record<string, Partial<QualityTier>> = {
  noring: { backdropDetail: 0 },
  noshadows: { shadows: false, shadowMap: 0 },
  nolights: { maxLights: 0 },
  noparticles: { particles: 0 },
  noambience: { ambience: 0 },
  nobloom: { bloom: false },
  noglow: { glow: false },
  // Both backends run the same materials, so this separates a bad port from a
  // bad driver -- the only question a phone in somebody else's hand can answer.
  webgl: { forceWebGL: true },
  /*
   * Present beyond standard range, on a display that can show it.
   *
   * Off by default and deliberately a flag rather than a tier: the amount of
   * headroom a bomb flash should be given is a decision about how a dark room
   * feels, and it cannot be made from a screenshot or from this machine. The
   * SDR image is identical either way -- the flag only decides whether the
   * energy the grade clips gets handed to the display or thrown away.
   */
  hdr: {
    hdrOutput: true,
    hdrKnee: hdrNumber('hdrknee', 0, 32),
    hdrHeadroom: hdrNumber('hdrgain', 0, 8),
    hdrEmissive: hdrNumber('hdremissive', 1, 12),
  },
  // The way back, for a screen that claims range it does not really have.
  nohdr: { hdrOutput: false },
  post8bit: { postHdr: false },
  // Only circuit builds the solid prop ring, and circuit is the only arena the
  // flicker has been seen on. `noring` drops it together with the silhouettes.
  noprops: { sceneProps: false },
  // What sits between `noprops` (still flickers) and `noring` (clean).
  nosilhouettes: { silhouettes: false },
  nobeacons: { beacons: false },
  // Depth precision, which is what a near plane really buys. See `cameraNear`.
  near2: { cameraNear: 2 },
  near5: { cameraNear: 5 },
  dpr1: { pixelRatioCap: 1 },
  // Shadows are the answer on at least one device; these say which part.
  shadowpcf: { shadowType: 'pcf' },
  shadowbasic: { shadowType: 'basic' },
  shadow512: { shadowMap: 512 },
  shadow256: { shadowMap: 256 },
  // Puts back what a GPU quirk switched off, so a driver update can be tested
  // without a deploy. This is how we find out the workaround is no longer needed.
  forceshadows: { shadows: true, shadowMap: 1024 },
  // The other half of the same idea: PowerVR is denied the loaded bomber, and
  // this asks for it back, so a driver update can be tested without a deploy.
  forcemodel: { bomberModel: true },
  // And the third: tile-based GPUs are denied post-processing because it draws
  // black rectangles over the arena. This asks for it back.
  forcebloom: { bloom: true },
  // Shadows on, one participant at a time removed from the pass. The standalone
  // repro renders every feature the arena uses without complaint, so the fault
  // is in the real scene and this is what is left to ask.
  noshadowbombers: { shadows: true, shadowMap: 1024, shadowBombers: false },
  noshadowblocks: { shadows: true, shadowMap: 1024, shadowBlocks: false },
  noshadowfloor: { shadows: true, shadowMap: 1024, shadowFloor: false },
  // The other thing `low` turns off, and the one nothing has varied yet.
  noaa: { antialias: false },
  aa: { antialias: true },
  // Climbing the other way. Taking things *out* of medium has produced a
  // contradiction -- shadows are needed for the crash, but nothing that takes
  // part in them matters -- so these add one piece of medium to low instead,
  // starting from a tier that is known to work. Combine with `forceshadows`.
  detail1: { detail: 1, backdropDetail: 1 },
  particles1400: { particles: 1400 },
  lights2: { maxLights: 2 },
  dpr15: { pixelRatioCap: 1.5 },
  // A quarter-scale backbuffer. PowerVR is a tile-based renderer, where every
  // framebuffer switch flushes the tile buffer, and the standalone repro that
  // survives shadows draws into a 256px canvas while the game draws into most
  // of a phone screen. If size is what separates them, this shows it.
  dpr025: { pixelRatioCap: 0.25 },
  // Sky, plinth and ground as well as the ring -- everything around the board,
  // and every hand-written shader in it.
  nobackdrop: { backdrop: false },
  // Stay on the procedural bomber: no glTF, no skinned mesh, and nothing
  // swapped into a scene that is already rendering.
  nomodel: { bomberModel: false },
  ambience6: { ambience: 0.6 },
};

/**
 * What `nomodel` removes, one piece at a time.
 *
 * `?gfx=forceshadows,nomodel` was the first thing all session to run with
 * shadows on, so the loaded bomber is the cause. Round one split it four ways
 * and the device answered clearly:
 *
 *   preloadmodel    crashed both browsers   -- not the late swap
 *   nomodelshader   crashed both browsers   -- not the custom program
 *   noscarf         ran in one of two       -- the skinned mesh contributes
 *   nomodeltex      ran in both             -- the textures are necessary
 *
 * So the textures are what matter, and round two splits them. They are not tier
 * fields: the model is built in bomberModel.ts, which cannot import `tier`
 * without closing a cycle with renderer.ts.
 */
const MODEL_PART_FLAGS: Record<string, Partial<Parameters<typeof setBomberModelParts>[0]>> = {
  // Hide Ninjabot's scarf; the character bodies remain skinned.
  noscarf: { scarf: false },
  // Compatibility with old diagnostic links. Authored PBR has no slot tint shader.
  nomodelshader: { shader: false },
  // Include restored roughness/metalness maps in the all-textures diagnostic.
  nomodeltex: { textures: false },
  // Prepared base-colour and normal textures are capped at 512px.
  nobasemap: { baseMap: false },
  nonormalmap: { normalMap: false },
  // Keep maps but remove their mip chains to isolate sampler/upload behavior.
  nomodelmips: { mipmaps: false },
  // Round three. `nobasemap` ran in both browsers and `nonormalmap` and
  // `nomodelmips` ran in neither, so the base map alone is what matters -- and
  // two things about it could be why. These separate them: the same pixels at
  // the same size, re-uploaded from a canvas rather than from the decoded WebP,
  // and then the same again at half and quarter size.
  modeltexcanvas: { baseMapResample: 1024 },
  modeltex512: { baseMapResample: 512 },
  modeltex256: { baseMapResample: 256 },
};

/**
 * What a start gives up after the one before it crashed.
 *
 * Rung 0 is an ordinary start. Each rung after it is what the boot guard falls
 * back to when the previous attempt never lived long enough to be crossed out;
 * see ui/bootguard.ts.
 *
 * Every rung must take something away that a GPU quirk has not already taken,
 * or a device spends one of its three lives on a change of nothing. That is
 * what the first version of this got wrong: its first rung dropped the loaded
 * bomber, which was exactly what the PowerVR quirk was already doing, so the
 * one device this was written for burned a rung standing still. So rung 1 is
 * the whole of the known rescue at once -- no shadows and no loaded model --
 * rather than the first half of it.
 *
 * A rung is not reached by guessing about a device. It is reached because that
 * device already failed one rung up.
 */
const SAFE_STEPS: Array<Partial<QualityTier>> = [
  {},
  { bomberModel: false, shadows: false, shadowMap: 0 },
  {
    bomberModel: false,
    shadows: false,
    shadowMap: 0,
    bloom: false,
    particles: 0,
    ambience: 0,
    antialias: false,
    pixelRatioCap: 1,
  },
  {
    bomberModel: false,
    shadows: false,
    shadowMap: 0,
    bloom: false,
    particles: 0,
    ambience: 0,
    antialias: false,
    pixelRatioCap: 1,
    backdrop: false,
    maxLights: 0,
    detail: 0,
  },
];

/**
 * Switches that are not part of a quality tier.
 *
 * `preloadmodel` is the timing half of the model question: it awaits the glTF
 * before the renderer exists, so bombers are built from it on the very first
 * frame instead of being rebuilt into a scene that is already drawing. If that
 * alone is enough, nothing about the model's *content* is at fault and the fix
 * is to ship the load this way.
 *
 * `nopreview` turns off the second WebGL context -- the offscreen renderer that
 * draws your bomber behind the menu panels. Chrome's notes for this GPU read
 * "Imagination drivers are buggy with context switching", which made it a
 * suspect; it crashed with shadows on, so it is not the cause, and the flag
 * stays only because it strips the scene.
 */
const APP_FLAGS = ['nopreview', 'preloadmodel', 'resetguard', ...Object.keys(MODEL_PART_FLAGS)] as const;

/**
 * Pulls the camera back, from `?pad=`.
 *
 * 1 frames the arena exactly; the game plays at 1.06 in attract and 1.14 in a
 * match, both of which put the scenery ring mostly off the top of the screen.
 * That is the shipping composition and it is the right thing to measure -- but
 * a ring off screen is a ring paying for its vertices and none of its pixels,
 * so `?pad=1.7` is how the other half of the cost gets asked for. It changes
 * what a `?perf=` run means, which is why the run records it.
 */
const CAM_PAD: number | null = (() => {
  try {
    const raw = Number(new URLSearchParams(location.search).get('pad'));
    return Number.isFinite(raw) && raw > 0 ? raw : null;
  } catch {
    return null;
  }
})();

/** Which flags were asked for, so the loading screen can say so. */
const GFX_ASKED: string[] = (() => {
  try {
    const raw = new URLSearchParams(location.search).get('gfx') ?? '';
    const known = (f: string) => f in GFX_FLAGS || (APP_FLAGS as readonly string[]).includes(f);
    return raw.split(',').map((f) => f.trim()).filter(known);
  } catch {
    return [];
  }
})();

const gfx = (name: string) => GFX_ASKED.includes(name);

setQualityOverrides(
  Object.assign({}, ...GFX_ASKED.filter((f) => f in GFX_FLAGS).map((f) => GFX_FLAGS[f])),
);

setBomberModelParts(
  Object.assign({}, ...GFX_ASKED.filter((f) => f in MODEL_PART_FLAGS).map((f) => MODEL_PART_FLAGS[f])),
);

function resolveQuality(): Quality {
  if (FORCED_QUALITY) return FORCED_QUALITY;
  if (prefs.quality !== 'auto') return prefs.quality;
  return autoTier ?? detectQuality();
}

async function boot(): Promise<void> {
  screens = new Screens(uiRoot, prefs, {
    onAction: handleAction,
    onPrefChange: patchPrefs,
    onChat: (text) => net.send({ t: 'chat', text }),
    onSignIn: signIn,
    onSignOut: signOut,
    onJoinRoom: (raw) => {
      const code = parseRoomCode(raw);
      if (!code) {
        toast('That does not look like a room code.', { tone: 'bad', timeout: 3500 });
        return;
      }
      void joinOnline(code);
    },
    onBoardScope: (scope) => {
      void refreshLeaderboard(scope);
    },
    onNameChange: (name) => {
      patchPrefs({ name });
      void fetch('/api/profile', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ name }),
      }).catch(() => {});
    },
    onReady: (ready) => net.send({ t: 'ready', ready }),
    onInvite: copyInvite,
    onSlotChange: (slot) => {
      patchPrefs({ slot });
      preview?.setSlot(slot);
      screens.updateBomberStage();
    },
  });
  // The build id is a commit SHA in CI; show a short prefix, compare the whole.
  screens.setBuildTag(
    `v${__APP_VERSION__} · ${__BUILD_ID__.slice(0, 8)}` +
      (FORCED_QUALITY ? ` · ${FORCED_QUALITY}` : '') +
      (GFX_ASKED.length ? ` · ${GFX_ASKED.join('+')}` : ''),
  );
  screens.setBoot(0.08, 'Starting engine…');

  // If the last start with these settings never lived long enough to be
  // crossed out, it crashed the driver -- so this one gives something up
  // rather than repeating it. Keyed by build id too, so a deploy starts clean.
  guard = beginBoot(
    `${__BUILD_ID__}|${resolveQuality()}|${GFX_ASKED.join(',')}`,
    gfx('resetguard') || MEASURING,
  );
  setSafeMode(SAFE_STEPS[Math.min(guard.level, SAFE_STEPS.length - 1)]);

  // Normally the bomber glTF is fetched fire-and-forget from the renderer and
  // every bomber is rebuilt when it lands. This waits for it first, so the
  // first frame already has it and nothing is swapped into a live scene.
  if (gfx('preloadmodel')) {
    screens.setBoot(0.1, 'Loading bomber…');
    await Promise.allSettled(SLOT_COLORS.map((_, slot) => loadBomberModel(slot)));
  }

  const built = await createRenderer();
  if (!built) {
    const why = diagnoseWebgl();
    screens.setBoot(1, why.short);
    toast(why.advice, { tone: 'bad', timeout: 0 });
    return;
  }
  renderer = built;
  watchContextLoss();
  renderer.initPost();
  renderer.cameraMode = prefs.cameraMode;
  hud.showMinimap = prefs.minimap;

  // One offscreen preview renderer serves both the menu hero and the
  // character-select stage; they always show the same bomber.
  try {
    if (gfx('nopreview')) throw new Error('disabled by ?gfx=nopreview');
    preview = new BomberPreview(512, bomberModelEnabled(resolveQuality()));
    preview.setSlot(prefs.slot);
    for (const id of ['menu-preview', 'bomber-preview']) {
      const el = document.getElementById(id) as HTMLCanvasElement | null;
      if (el) preview.attach(el);
    }
  } catch {
    preview = null; // a second WebGL context is a nicety, not a requirement
  }
  // Thumbnails are rendered on demand when the character select is opened.
  // Doing all eight during boot costs a visible pause on a slow device, and
  // most players never open that screen.
  if (preview) {
    screens.bomberThumb = slot => preview!.thumbnail(slot, 160);
    let refresh = 0;
    preview.onModelReady = () => {
      if (refresh || screens.screen !== 'bombers') return;
      refresh = requestAnimationFrame(() => { refresh = 0; screens.renderBombers(); });
    };
  }
  screens.updateBomberStage();
  screens.updateMenuBadges(
    prefs.campaign,
    CAMPAIGN_STAGES.length,
    prefs.bestSurvivalWave,
    prefs.bestExpedition,
  );

  screens.setBoot(0.35, 'Building arenas…');
  input.attach();
  input.onPause = () => {
    if (mode === 'playing') pause();
    else if (mode === 'paused') resume();
  };
  touch = bindTouchControls(touchRoot, input);
  wireChrome();

  screens.setBoot(0.55, 'Loading audio…');
  await loadMediaConfig();
  // Kicked off, not waited on: a slow media host must not hold up the menu.
  void startAudio();

  /*
   * Chromium only, and only the `cpu` source anywhere -- so this is a Pixel
   * getting a leading indicator and an iPhone carrying on with frame rate
   * alone. Started before the loop so the first sampling window already has
   * something to read.
   */
  pressure.watch();

  screens.setBoot(0.8, 'Starting attract mode…');
  startAttract();
  /*
   * Before the first frame, so the readout's own clock starts where the frame
   * loop does. It returns null unless `?perf=` asked for it, and a build that
   * nobody asked makes no element and does no work.
   */
  perf = startPerf({
    canvas,
    stats: () => renderer.stats(),
    memory: () => renderer.memory(),
    quality: () => resolveQuality(),
    gpu: () => gpuRenderer || 'unknown gpu',
    pad: () => renderer.cameraPadding,
    arena: () => shownArena,
    /*
     * The tier as resolved, not as asked for. `detail` is the field that
     * decides whether the scenery ring is drawn at all, and both a `low` tier
     * and the boot guard's third safe step set it to 0 -- so a run that does
     * not read it back cannot tell "the props are expensive" from "there were
     * never any props".
     */
    detail: () => tierFor(resolveQuality()).detail,
    safeLevel: () => guard?.level ?? 0,
    frameFormat: () => renderer.frameFormat(),
    settle: () => guard?.crossOut(),
    pressure: () => pressure.peakPressure(),
    setUiHidden: (hidden) => {
      uiRoot.style.display = hidden ? 'none' : '';
      hudRoot.style.display = hidden ? 'none' : '';
    },
  });
  requestAnimationFrame(frame);

  // The loop is running. The guard crosses this configuration out only once it
  // has watched the loop keep running, so a driver that dies a few seconds in
  // -- which is what this one does -- still counts as a failed start.
  guard?.survived(() => framesDrawn);
  if (guard?.recovered) {
    toast('The last start crashed, so some graphics are off. Settings → Graphics to change it.', {
      tone: 'info',
      timeout: 8000,
    });
  }

  screens.setBoot(1, 'Ready.');
  window.setTimeout(() => {
    screens.show('menu', { replace: true });
    void refreshProfile();
  }, 240);

  setupInstallPrompt();
  handleDeepLink();
}

/**
 * Why the renderer could not start.
 *
 * "WebGL is unavailable on this device" was printed for every failure, and it
 * is often simply untrue: this engine needs **WebGL 2**, and a phone whose
 * driver has WebGL 1 but not 2 -- which is a thing Chrome decides per driver,
 * and revisits when it updates -- got told it had no WebGL at all. That is a
 * dead end for the player and an unusable bug report for us, since the one
 * screenshot they can send says nothing about what actually happened.
 *
 * The probe is a fresh canvas rather than the game's: asking the one that just
 * failed would report the failure, not the capability.
 */
/** What the renderer's constructor last threw, if anything. */
let bootError = '';

function diagnoseWebgl(): { short: string; advice: string } {
  let gl2 = false;
  let gl1 = false;
  let reason = '';
  try {
    const probe = document.createElement('canvas');
    probe.addEventListener('webglcontextcreationerror', (e) => {
      reason = (e as WebGLContextEvent).statusMessage || '';
    });
    gl2 = !!probe.getContext('webgl2');
    if (!gl2) gl1 = !!(probe.getContext('webgl') || probe.getContext('experimental-webgl'));
  } catch {
    /* a browser that throws on getContext has answered the question */
  }
  if (gl2) {
    // WebGL 2 is there and the context still would not build. Either the tab is
    // out of contexts, or our own construction threw -- and `bootError` is the
    // only one of those two that can say which.
    return {
      short: bootError ? `Could not start the graphics engine: ${bootError}` : 'Could not start the graphics engine.',
      advice: 'Graphics failed to start. Close other tabs using 3D and reload, then open /api/diag if it keeps happening.',
    };
  }
  if (gl1) {
    // Deliberately hedged. This is the reading of a device that has *just*
    // refused us three contexts, and a browser that is out of them refuses a
    // fourth the same way it refuses one it cannot make -- so "no WebGL 2 on
    // this device" is a conclusion this code is not entitled to draw. On a
    // recent phone it is almost always wrong, and /api/diag settles it from a
    // page that has not spent any contexts.
    return {
      short: 'Could not get WebGL 2, which the game needs.',
      advice:
        'Detonarium needs WebGL 2. Open /api/diag on this device for a report — it will say whether WebGL 2 is missing or just unavailable right now.',
    };
  }
  // Drivers write essays. The first clause is the part that names the cause.
  const brief = reason.split(/[,.]/)[0]?.trim().slice(0, 80) ?? '';
  return {
    short: brief ? `WebGL is unavailable: ${brief}.` : 'WebGL is unavailable on this device.',
    advice: 'Detonarium needs WebGL 2. Open /api/diag on this device for a report on why.',
  };
}

/**
 * Builds the renderer, retrying a couple of times before giving up.
 *
 * A browser allows only a handful of live WebGL contexts, and Chrome on Android
 * can still be holding the previous page's when the next one asks — so a plain
 * reload could land on "WebGL is unavailable on this device" on hardware that
 * had been running the game perfectly a second earlier. Contexts are released
 * on `pagehide` now, and this covers the rest.
 */
async function createRenderer(): Promise<GameRenderer | null> {
  for (let attempt = 0; attempt < 3; attempt++) {
    let built: GameRenderer | null = null;
    try {
      built = new GameRenderer(canvas, resolveQuality(), prefs.hdr);
      /*
       * The backend comes up asynchronously and nothing may draw before it
       * has. Missing this is a black screen with no error at all, which is
       * exactly what it cost the first time: every frame was queued against a
       * renderer that did not yet have a device.
       */
      await built.init();
      return built;
    } catch (err) {
      bootError = err instanceof Error ? err.message : String(err);
      console.warn(`WebGL context attempt ${attempt + 1} failed`, err);
      // A constructor that throws *after* three has taken the context leaves it
      // alive with nothing holding it. Three of those and the tab is out of
      // contexts, so the next question anyone asks -- including our own "do you
      // have WebGL 2?" -- is answered by the mess we made rather than by the
      // device. Hand it back before trying again.
      try {
        built?.dispose();
      } catch {
        /* half-built; there may be nothing to dispose */
      }
      if (attempt < 2) await new Promise((r) => window.setTimeout(r, 250 * (attempt + 1)));
    }
  }
  return null;
}

/**
 * Hands both GL contexts back when the page goes away, and copes with the
 * driver taking one from us mid-session.
 */
function watchContextLoss(): void {
  const release = () => {
    checkpointExpedition();
    try {
      preview?.dispose();
      renderer?.dispose();
    } catch {
      /* tearing down on the way out; nothing here is worth a console error */
    }
  };
  // `pagehide` rather than `unload`: it is the one that fires on iOS, and the
  // one that survives the back/forward cache.
  window.addEventListener('pagehide', release);

  canvas.addEventListener('webglcontextlost', (e) => {
    checkpointExpedition();
    // Without preventDefault the browser will not even try to give it back.
    e.preventDefault();
    toast('Graphics context lost. Reloading…', { tone: 'bad', timeout: 0, key: 'gl' });
    window.setTimeout(() => location.reload(), 1200);
  });
}

/**
 * Brings the audio up.
 *
 * Called once during boot and again on every early gesture. The boot call is
 * the one that matters: building the graph and decoding the effects needs no
 * user interaction, and where the browser permits autoplay — desktop Chrome,
 * an installed PWA, a returning player — the music starts with the menu instead
 * of waiting for a click nobody knows they have to make. Where it does not, the
 * work is done and the first tap only has to lift the suspension.
 */
async function startAudio(): Promise<void> {
  const wasAudible = audio.running;
  audio.settings = { master: prefs.master, music: prefs.music, sfx: prefs.sfx, muted: prefs.muted };
  if (!audio.ready) {
    await audio.prime();
    audio.applyVolumes();
    void audio.preloadCore();
  } else if (!(await audio.resume())) {
    return;
  }
  // Only on the transition into audible. Calling this on every click would cut
  // the victory fanfare back to menu music the moment the player touches the
  // scoreboard.
  if (!wasAudible && audio.running && mode === 'menu') audio.playMusic('menu', 1.6);
}

function wireChrome(): void {
  applyProgression();
  window.addEventListener('online', () => { void syncExpedition(); });
  window.addEventListener('storage', e => {
    if (e.key?.startsWith('bg9.expedition.v2.')) applyProgression();
  });
  const gesture = () => {
    if (!audio.ready) {
      void startAudio();
      return;
    }
    // Cheap when there is nothing to do, and it is the only thing that retries
    // a `play()` the browser refused. An online match starts its arena track
    // from a WebSocket message rather than a tap, which iOS blocks outright —
    // the player's first touch of the stick is what recovers it.
    if (!audio.running || audio.blocked) void audio.resume();
  };
  window.addEventListener('pointerdown', gesture, { once: false });
  window.addEventListener('keydown', gesture, { once: false });
  window.addEventListener('touchstart', gesture, { passive: true });
  // Coming back to a backgrounded tab suspends the context on some browsers.
  document.addEventListener('visibilitychange', () => {
    if (!document.hidden) gesture();
  });
  input.onAnyInput = gesture;

  window.addEventListener('resize', () => renderer.resize());
  window.addEventListener('orientationchange', () => window.setTimeout(() => renderer.resize(), 220));
  document.getElementById('btn-pause')?.addEventListener('click', () => pause());
  hud.onDrillSkip(() => {
    audio.play('ui-click');
    training?.skip();
  });
  hud.onDrillDone(() => {
    audio.play('ui-click');
    matchKind = 'solo';
    startLocalMatch(buildSoloSeeds(), buildSettings());
  });

  // Touch controls only appear once the player actually touches the screen, so
  // a laptop with a touchscreen does not get thumbsticks it never asked for.
  window.addEventListener(
    'touchstart',
    () => {
      if (mode === 'playing') showTouch(true);
    },
    { passive: true },
  );

  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'hidden') {
      checkpointExpedition();
      if (mode === 'playing' && matchKind !== 'online') pause();
    } else void syncExpedition();
  });

  document.getElementById('rotate-hint')?.addEventListener('click', (e) => {
    (e.currentTarget as HTMLElement).hidden = true;
  });
}

function handleDeepLink(): void {
  const params = new URLSearchParams(location.search);
  /*
   * An *interrupted* run outranks the menu. A parked one does not.
   *
   * The distinction is the whole point of the flag: nobody chose to stop when
   * the graphics context died or the phone discarded the tab, so picking that
   * run back up is restoring what was happening. Somebody who quit did choose,
   * and dropping them into room six when they opened the game to play
   * something else is the trap this save originally created.
   *
   * Neither outranks a URL that asked for something specific -- a link someone
   * was sent should still do what it says.
   */
  if (!params.get('quick') && !params.get('online') && !params.get('room') && !params.get('arena')) {
    const saved = loadExpedition();
    if (saved && !saved.parked) {
      window.setTimeout(() => resumeExpedition(), 400);
      return;
    }
  }
  if (params.get('quick')) {
    window.setTimeout(() => handleAction('quick'), 400);
  } else if (params.get('online') || params.get('room')) {
    window.setTimeout(() => joinOnline(params.get('room') || undefined), 400);
  } else if (params.get('arena')) {
    // `?arena=circuit&look=1` puts a named arena and a named look behind the
    // menu straight away. The menu's attract match otherwise rolls its own, so
    // checking a change to one stage meant reloading until it came up -- and a
    // look is derived from the seed, so a variant could take several more.
    // Same path the capture tools use: the look is derived from the seed, so
    // it is asked for through prefs rather than passed to the attract match.
    const id = params.get('arena') as string;
    const look = Number(params.get('look') ?? -1);
    pinnedArena = id;
    window.setTimeout(() => {
      patchPrefs({ arena: id, arenaLook: Number.isFinite(look) ? look : -1 });
      startAttract(id);
    }, 400);
  }
}

/* -------------------------------------------------------------------------- */
/* Preferences                                                                 */
/* -------------------------------------------------------------------------- */

function patchPrefs(patch: Partial<Prefs>): void {
  const before = prefs;
  prefs = { ...prefs, ...patch };
  savePrefs(prefs);
  screens.setPrefs(prefs);

  if (patch.quality !== undefined && renderer) renderer.setQuality(resolveQuality());
  /*
   * HDR is decided when the canvas is configured and cannot be switched live:
   * the output format, the colour space and the emissive strengths baked into
   * the materials are all set at construction. So this one reloads.
   *
   * From the menu that is invisible. Mid-match it would cost the match, so it
   * says so and waits -- and a run in progress survives it either way, which
   * is the whole reason `RUN_KEY` exists.
   */
  if (patch.hdr !== undefined && patch.hdr !== before.hdr) {
    if (mode === 'menu') location.reload();
    else toast('HDR changes the next time the game loads.', { tone: 'info' });
  }
  if (patch.cameraMode !== undefined && renderer) renderer.cameraMode = prefs.cameraMode;
  if (patch.minimap !== undefined) hud.showMinimap = prefs.minimap;
  if (
    patch.master !== undefined ||
    patch.music !== undefined ||
    patch.sfx !== undefined ||
    patch.muted !== undefined
  ) {
    audio.settings = { master: prefs.master, music: prefs.music, sfx: prefs.sfx, muted: prefs.muted };
    audio.applyVolumes();
  }
  if (patch.arena && patch.arena !== before.arena && attract) startAttract(prefs.arena);
  /*
   * Settings that change the shape of the settings panel redraw it.
   *
   * The win condition decides whether Lives or Kill target is offered, hit
   * points decide whether comeback is offered at all, and several rows read
   * back the value in their own description. Chips only repaint themselves, so
   * without this the panel keeps describing the setting you just left --
   * which `winBy` has quietly done since the row was added.
   */
  if (
    patch.winBy !== undefined ||
    patch.hitPoints !== undefined ||
    patch.resolve !== undefined ||
    patch.loseGearOnDeath !== undefined ||
    patch.catchup !== undefined ||
    patch.arenaEvents !== undefined ||
    patch.deflect !== undefined ||
    patch.lives !== undefined ||
    patch.killTarget !== undefined ||
    patch.suddenDeath !== undefined
  ) {
    // The setup and lobby panels, not the global Settings screen: that is
    // where the match rules live.
    screens.refreshOptions();
  }
  if (patch.mode !== undefined) screens.renderSettings();
  if (matchKind === 'online' && net.isHost && screens.screen === 'lobby') {
    // Nudge the room; the next lobby broadcast reconciles the rest.
    pushedSettings = '';
    pushedBots = '';
    net.send({
      t: 'settings',
      patch: {
        mapId: prefs.arena,
        arenaLook: prefs.arenaLook,
        mode: prefs.mode,
        roundSeconds: prefs.roundSeconds,
        lives: prefs.lives,
        winBy: prefs.winBy,
        killTarget: prefs.killTarget,
        suddenDeath: prefs.suddenDeath,
        friendlyFire: prefs.friendlyFire,
        itemDensity: prefs.itemDensity,
        // The room clamps these on the way in; the host is only proposing.
        hitPoints: prefs.hitPoints,
        resolve: prefs.resolve,
        loseGearOnDeath: prefs.loseGearOnDeath,
        catchup: prefs.catchup,
        arenaEvents: prefs.arenaEvents,
        deflect: prefs.deflect,
      },
    });
    if (patch.bots !== undefined || patch.botLevel !== undefined) {
      net.send({ t: 'bots', count: prefs.bots, level: prefs.botLevel });
    }
  }
  audio.play('ui-click');
}

/* -------------------------------------------------------------------------- */
/* Menu actions                                                                */
/* -------------------------------------------------------------------------- */

function handleAction(action: string): void {
  void startAudio();
  if (action !== 'back') audio.play('ui-click');
  else audio.play('ui-back');

  /*
   * The doors on the route screen, matched by prefix rather than enumerated:
   * how many there are is `EXPEDITION_BRANCHES`, and a switch listing them
   * would be a second place to keep that number in step.
   */
  if (action.startsWith('room-')) {
    takeBranch(Number(action.slice(5)));
    return;
  }
  if (action.startsWith('buy-')) {
    buyUnlock(action.slice(4));
    return;
  }

  switch (action) {
    case 'quick':
      matchKind = 'solo';
      // Quick match is the "just play" button, so it rolls an arena rather than
      // replaying whatever is saved in settings — which, for anyone who has
      // never opened settings, is Neon Grid every single time.
      startLocalMatch(buildSoloSeeds(), buildSettings({ mapId: rollArena().id }));
      break;
    case 'local':
      matchKind = 'local';
      screens.openSetup({ kind: 'local', title: 'Local Versus' });
      break;
    case 'survival':
      matchKind = 'survival';
      screens.openSetup({ kind: 'survival', title: 'Survival' });
      break;
    case 'adventure':
      matchKind = 'adventure';
      // Outfitting first. A run that starts the instant the card is pressed
      // gives the last run's payout nowhere to land.
      showOutfit();
      break;
    case 'run-start':
      matchKind = 'adventure';
      /*
       * A new run concludes whatever was parked before it starts.
       *
       * Not discarded: a run parked at room six *happened*, and its salvage is
       * owed. Silently dropping it would make "New Run" the one button in the
       * mode that destroys progress without saying so.
       */
      if (loadExpedition()) {
        expedition = loadExpedition();
        endExpedition();
      }
      startExpedition();
      break;
    case 'run-resume':
      matchKind = 'adventure';
      if (!resumeExpedition()) showOutfit();
      break;
    case 'abandon':
      // Deliberately ending a run rather than stepping out of it: pays out and
      // clears, where `quit` parks. Both land on the menu.
      endExpedition();
      quitToMenu();
      break;

    case 'campaign':
      matchKind = 'campaign';
      startCampaign(prefs.campaign);
      break;
    case 'training':
      matchKind = 'training';
      startTraining();
      break;
    case 'bombers':
      screens.openBombers();
      break;
    case 'online':
      screens.show('online');
      break;
    case 'quick-online':
      void joinOnline();
      break;
    case 'create-room':
      void joinOnline(privateRoomCode());
      break;
    case 'launch':
      launchFromSetup();
      break;
    case 'settings':
      screens.show('settings');
      break;
    case 'profile':
      screens.show('profile');
      void refreshProfile();
      break;
    case 'leaderboard':
      screens.show('leaderboard');
      void refreshLeaderboard();
      break;
    case 'how':
      screens.show('how');
      break;
    case 'resume':
      resume();
      break;
    case 'restart':
      restart();
      break;
    case 'quit':
      quitToMenu();
      break;
    case 'menu':
      quitToMenu();
      break;
    case 'rematch':
      // Online, the room may still be showing the finished board to everyone.
      // It queues the request, so say so rather than leaving a button that
      // looks like it did nothing.
      if (matchKind === 'online' && !net.isHost) {
        toast('Ready for the next round.', { tone: 'good', key: 'net', timeout: 2600 });
      } else if (matchKind === 'online') {
        toast('Starting the next round…', { key: 'net', timeout: 3000 });
      }
      restart();
      break;
    case 'host-start':
      net.send({ t: 'start' });
      break;
    case 'leave':
      leaveOnline();
      break;
  }
}

/**
 * A seed for a match on `mapId`.
 *
 * A chosen look never travels as its own value: it decides which seed gets
 * rolled, and the look is derived back out of that seed by everyone who draws
 * the board. The choice belongs to the arena it was made on, so it is ignored
 * for any other map -- Quick Match rolling a fresh arena, or a campaign stage
 * with a fixed one.
 */
function rollSeed(mapId: string): number {
  const look = mapId === prefs.arena ? prefs.arenaLook : -1;
  return look >= 0 ? seedForLook(getArena(mapId), look) : (Math.random() * 0x7fffffff) | 0;
}

/**
 * Rules under trial, switched from the URL.
 *
 * These change what the game *is* -- hit points make an elimination an
 * exchange rather than an instant, and resolve scales what that exchange costs
 * by how far behind you are. That is not something to flip on for everybody
 * while it is still being judged, and it is not something to hide behind a
 * rebuild either, because the one question left about it is how it feels and
 * only a person playing it can answer that.
 *
 * So they live where `?quality=` and `?gfx=` already live: on the URL, off by
 * default, and a plain link is exactly the game that shipped. Which keys are
 * read, and why hit points bring a win condition with them, is in
 * `shared/trial.ts` next to the parsing it describes.
 */
const TRIAL: Partial<MatchSettings> = trialSettings(
  typeof location === 'undefined' ? '' : location.search,
);

/**
 * `base` is a mode's own rule set: it replaces the preferences but `TRIAL`
 * still sweeps it, which is the difference between "this mode does not read
 * the setup panel" and "this mode cannot be measured from a URL". `overrides`
 * are the rules that define a mode and must survive even a trial flag.
 */
function buildSettings(
  overrides: Partial<MatchSettings> = {},
  base: Partial<MatchSettings> = {},
): MatchSettings {
  const mapId = overrides.mapId ?? prefs.arena;
  return {
    mapId,
    mode: matchKind === 'survival' ? 'survival' : prefs.mode,
    roundSeconds: prefs.roundSeconds,
    lives: prefs.lives,
    seed: rollSeed(mapId),
    suddenDeath: prefs.suddenDeath,
    friendlyFire: prefs.friendlyFire,
    itemDensity: prefs.itemDensity,
    // Survival is a wave mode: you are eliminated when your lives run out, and
    // "first to five kills" would end it in the opening seconds.
    winBy: matchKind === 'survival' ? 'lives' : prefs.winBy,
    killTarget: prefs.killTarget,
    hitPoints: prefs.hitPoints,
    resolve: prefs.resolve,
    loseGearOnDeath: prefs.loseGearOnDeath,
    catchup: prefs.catchup,
    arenaEvents: prefs.arenaEvents,
    deflect: prefs.deflect,
    ...base,
    // Before `overrides`, so a caller that means to pin a rule still wins.
    ...TRIAL,
    ...overrides,
  };
}

/** Slots not taken by a human, in order, so bots never clash with your pick. */
function freeSlots(taken: number[]): number[] {
  const out: number[] = [];
  for (let i = 0; i < BOMBERS.length; i++) if (!taken.includes(i)) out.push(i);
  return out;
}

function buildSoloSeeds(humans = 1): PlayerSeed[] {
  const arena = getArena(prefs.arena);
  const cap = arenaCapacity(arena);
  const bots = Math.max(0, Math.min(prefs.bots, cap - humans));
  const seeds: PlayerSeed[] = [];

  const humanSlots = [prefs.slot % BOMBERS.length];
  if (humans > 1) humanSlots.push(freeSlots(humanSlots)[0]);

  humanSlots.slice(0, humans).forEach((slot, i) => {
    seeds.push({
      id: `local-${i}`,
      name: humans > 1 ? `Player ${i + 1}` : prefs.name,
      slot,
      team: prefs.mode === 'teams' ? i % 2 : slot,
      bot: false,
    });
  });

  const pool = freeSlots(humanSlots);
  for (let i = 0; i < bots; i++) {
    const slot = pool[i % pool.length];
    seeds.push({
      id: `bot-${i}`,
      name: `${BOT_PROFILES[prefs.botLevel].name} ${i + 1}`,
      slot,
      team: prefs.mode === 'teams' ? (humans + i) % 2 : slot,
      bot: true,
      botLevel: prefs.botLevel,
    });
  }
  return seeds;
}

function launchFromSetup(): void {
  switch (matchKind) {
    case 'local': {
      const seeds = buildSoloSeeds(2);
      startLocalMatch(seeds, buildSettings());
      break;
    }
    case 'survival': {
      const seeds: PlayerSeed[] = [{ id: 'local-0', name: prefs.name, slot: 0, team: 0, bot: false }];
      const pool = freeSlots([prefs.slot % BOMBERS.length]);
      for (let i = 0; i < 2; i++) {
        seeds.push({
          id: `wave1-${i}`,
          name: `${BOT_PROFILES[0].name} ${i + 1}`,
          slot: pool[i % pool.length],
          team: 99,
          bot: true,
          botLevel: 0,
        });
      }
      startLocalMatch(
        seeds,
        buildSettings({
          mode: 'survival',
          roundSeconds: 3600,
          suddenDeath: false,
          lives: 3,
          // Pinned here and not only in `buildSettings`, because `TRIAL`
          // spreads after that ternary: `?win=kills` would otherwise end a
          // wave mode in the opening seconds. The drills pin theirs for the
          // same reason.
          winBy: 'lives',
        }),
      );
      break;
    }
    default:
      startLocalMatch(buildSoloSeeds(), buildSettings());
  }
}

function startCampaign(stage: number): void {
  campaignStage = Math.min(stage, CAMPAIGN_STAGES.length - 1);
  const cfg = CAMPAIGN_STAGES[campaignStage];
  const mine = prefs.slot % BOMBERS.length;
  const pool = freeSlots([mine]);
  const seeds: PlayerSeed[] = [{ id: 'local-0', name: prefs.name, slot: mine, team: 0, bot: false }];
  for (let i = 0; i < cfg.bots; i++) {
    seeds.push({
      id: `bot-${i}`,
      name: `${BOT_PROFILES[cfg.level].name} ${i + 1}`,
      slot: pool[i % pool.length],
      team: i + 1,
      bot: true,
      botLevel: cfg.level,
    });
  }
  startLocalMatch(
    seeds,
    // Every stage is authored as a life count — three at "First Sparks" down to
    // one at "Nightmare" — so the campaign is elimination whatever the player's
    // saved preference says.
    buildSettings({ mapId: cfg.map, mode: 'ffa', lives: cfg.lives, roundSeconds: 180, winBy: 'lives' }),
  );
  hud.banner(`STAGE ${campaignStage + 1} — ${cfg.label.toUpperCase()}`, 2.4);
}

/**
 * Training is a real tutorial: seven drills on a purpose-built empty arena,
 * each one arranging the board so the idea it teaches is the only thing to do.
 */
function startTraining(): void {
  const seeds: PlayerSeed[] = [
    { id: 'local-0', name: prefs.name, slot: prefs.slot % BOMBERS.length, team: 0, bot: false },
  ];
  const settings = buildSettings({
    mapId: 'dojo',
    mode: 'ffa',
    lives: 99,
    roundSeconds: 3600,
    suddenDeath: false,
    itemDensity: 0,
    // The drills own the win condition: a kill target would end the tutorial
    // the first time the sparring bot in drill 7 goes down.
    winBy: 'lives',
  });
  startLocalMatch(seeds, settings);
  training = new Training((match as LocalMatch).sim, shouldShowTouch());
  hud.banner('TRAINING', 1.4);
}

/* -------------------------------------------------------------------------- */
/* Match lifecycle                                                             */
/* -------------------------------------------------------------------------- */

function startLocalMatch(seeds: PlayerSeed[], settings: MatchSettings, restored?: Sim): void {
  disposeMatch();
  attract = null;

  const controllers = new Map<string, 0 | 1>();
  seeds.filter((s) => !s.bot).forEach((s, i) => controllers.set(s.id, (i === 0 ? 0 : 1) as 0 | 1));

  const local = new LocalMatch(settings, seeds, controllers);
  if (restored) local.restoreRoom(restored);
  match = local;
  enterMatch(settings);
}

function enterMatch(settings: MatchSettings): void {
  // Which look this arena wears is derived from the match seed, so every client
  // in the room lands on the same one without it being sent.
  const arena = arenaVariant(getArena(settings.mapId), settings.seed);
  const state = match!.state;
  renderer.cameraMode = prefs.cameraMode;
  // A little more margin than attract mode: in a real match you need to see a
  // blast coming from the edge of the board, not discover it at the boundary.
  renderer.cameraPadding = CAM_PAD ?? 1.14;
  shownArena = arena.id;
  renderer.orbit = 0;
  renderer.loadArena(arena, state.width, state.height, state.grid, state.hazards);
  renderer.syncItems(state.items);
  hud.reset(state);
  hud.show(true);
  screens.show('none');
  showTouch(shouldShowTouch());
  mode = 'playing';
  audio.sceneGain = 1;
  matchStartedAt = performance.now();
  resultsShown = false;
  if (endTimer !== null) {
    window.clearTimeout(endTimer);
    endTimer = null;
  }
  updater.setBusy(true);
  audio.playMusic(arena.track, 1.0);
  hud.banner('READY', 1.0);
}

function shouldShowTouch(): boolean {
  return input.touchUsed || (matchMedia('(hover: none) and (pointer: coarse)').matches && !input.keyboardUsed);
}

function pause(): void {
  if (mode !== 'playing') return;
  if (matchKind === 'online') {
    // Online matches keep running; the pause menu would only mislead.
    toast('You cannot pause an online match.', { timeout: 2000, key: 'pause' });
    return;
  }
  mode = 'paused';
  screens.show('pause');
  audio.play('ui-back');
}

function resume(): void {
  if (mode !== 'paused') return;
  mode = 'playing';
  screens.show('none');
  lastFrame = performance.now();
}

function restart(): void {
  if (!match) return quitToMenu();
  if (matchKind === 'adventure') {
    // A run is the unit here, so there is nothing to retry: this rolls a new
    // one, which is the whole reason the rooms are generated rather than
    // authored.
    startExpedition();
    return;
  }
  if (matchKind === 'campaign') {
    // `prefs.campaign` already advanced if the last stage was cleared, so this
    // is "next stage" on a win and "retry" on a loss.
    startCampaign(prefs.campaign);
    return;
  }
  if (matchKind === 'training') {
    startTraining();
    return;
  }
  const settings = { ...match.state.settings, seed: rollSeed(match.state.settings.mapId) };
  const seeds: PlayerSeed[] = match.state.players.map((p) => ({
    id: p.id,
    name: p.name,
    slot: p.slot,
    team: p.team,
    bot: p.bot,
    botLevel: p.botLevel,
  }));
  if (matchKind === 'online') {
    net.send({ t: 'start' });
    return;
  }
  startLocalMatch(seeds, settings);
}

function quitToMenu(): void {
  /*
   * Quitting parks the run; it does not end it.
   *
   * Permadeath means *dying* ends a run, not that leaving does -- which is what
   * every roguelite with a save-and-exit does, and a run here is five to
   * fifteen minutes on a device that rings. Ending it on quit made a phone call
   * cost room six of eight.
   *
   * Concluding a run is still available and still pays out: die, finish, or
   * abandon it from the route screen.
   */
  parkExpedition();
  // `bestRoom` defaults to 0, so omitting it blanked the Expedition badge on
  // every trip back to the menu -- including the trip that had just set it.
  screens.updateMenuBadges(
    prefs.campaign,
    CAMPAIGN_STAGES.length,
    prefs.bestSurvivalWave,
    prefs.bestExpedition,
  );
  disposeMatch();
  if (matchKind === 'online') leaveOnline();
  mode = 'menu';
  hud.show(false);
  showTouch(false);
  updater.setBusy(false);
  startAttract();
  screens.show('menu', { replace: true });
  audio.playMusic('menu', 1.2);
  void updater.checkNow();
}

/**
 * Picks an arena at random, avoiding the one just played.
 *
 * Shared by quick match and the attract loop: both want variety, and both were
 * capable of serving the same board twice in a row (or, for quick match, for
 * ever).
 */
function rollArena(): ArenaDef {
  const pool = ARENAS.filter((a) => a.id !== lastRolledArena);
  const from = pool.length ? pool : ARENAS;
  const picked = from[Math.floor(Math.random() * from.length)];
  lastRolledArena = picked.id;
  return picked;
}

let lastRolledArena = '';

/**
 * The arena `?arena=` or `dtn.setArena` asked for, and what attract restarts on.
 *
 * Attract mode rotates arenas when a demo match ends, which is right for a
 * player sitting on the menu and wrong for anyone who named one. A `?perf=`
 * run measures circuit because circuit is the only arena with a prop ring:
 * when the demo ended underneath it, the ring left with the arena, and the
 * rung either sat waiting for models nothing was going to ask for or -- worse
 * -- sampled a frame that no longer had any props in it and reported the
 * number as though it did.
 */
let pinnedArena: string | null = null;

/** Which arena is on screen, so a measurement can say what it measured. */
let shownArena = '';

/** Milliseconds spent on the finished board before the next demo starts. */
const ATTRACT_RESTART_MS = 2600;
let attractRestart = 0;

function disposeMatch(): void {
  match?.dispose();
  match = null;
  training = null;
  renderer?.setMarkers([]);
  hud.setObjective(null);
}

/* -------------------------------------------------------------------------- */
/* Attract mode                                                                */
/* -------------------------------------------------------------------------- */

/**
 * Attract mode: a bot-only match that plays behind the menus, and the same
 * thing the capture tools record.
 *
 * It loops. When the match reaches `over` the frame loop waits out
 * `ATTRACT_RESTART_MS` — long enough for the last explosion to finish rather
 * than cutting on the winning frame — and starts a fresh one on a *different*
 * arena, so a player sitting on the menu is shown the whole rotation instead of
 * whatever `Math.random` happened to pick twice.
 *
 * Nothing else stops it: it is disposed when a real match starts and rebuilt
 * when one ends. `requestAnimationFrame` does not fire in a hidden tab, so a
 * backgrounded menu costs nothing.
 */
function startAttract(arenaId?: string): void {
  disposeMatch();
  attractRestart = 0;
  // Background match: audible, but well behind the menu music.
  audio.sceneGain = 0.22;
  const arena = arenaId ? getArena(arenaId) : pinnedArena ? getArena(pinnedArena) : rollArena();
  shownArena = arena.id;
  const seeds: PlayerSeed[] = [];
  const count = Math.min(4, arenaCapacity(arena));
  for (let i = 0; i < count; i++) {
    seeds.push({
      id: `demo-${i}`,
      name: BOT_PROFILES[2].name,
      slot: i,
      team: i,
      bot: true,
      botLevel: 2 + (i % 2),
    });
  }
  const settings: MatchSettings = {
    mapId: arena.id,
    mode: 'ffa',
    roundSeconds: 90,
    // One life each. With three, a bot that "dies" on screen is back a second
    // later and the demo runs its full two minutes into sudden death before it
    // resolves — so the board a player is watching behind the menu looks like
    // it should have finished long ago, and does not.
    lives: 1,
    seed: rollSeed(arena.id),
    suddenDeath: true,
    friendlyFire: false,
    itemDensity: 1,
    // Elimination, so the demo actually ends and rotates to another arena
    // rather than four bots respawning at each other for ever.
    winBy: 'lives',
    killTarget: 5,
  };
  attract = new LocalMatch(settings, seeds, new Map());
  match = attract;
  renderer.loadArena(
    arenaVariant(arena, settings.seed),
    attract.state.width,
    attract.state.height,
    attract.state.grid,
    attract.state.hazards,
  );
  renderer.cameraMode = 'arena';
  renderer.cameraPadding = CAM_PAD ?? 1.06;
}

/* -------------------------------------------------------------------------- */
/* Online                                                                      */
/* -------------------------------------------------------------------------- */

/**
 * A short, unambiguous room code.
 *
 * No O/0, I/1 or similar: these get read aloud and typed in by hand. Private
 * rooms are simply names the quick-match probe never looks at, so an
 * unguessable one is all the privacy that is needed.
 */
function privateRoomCode(): string {
  const ALPHABET = 'abcdefghjkmnpqrstuvwxyz23456789';
  const bytes = crypto.getRandomValues(new Uint8Array(6));
  return Array.from(bytes, (b) => ALPHABET[b % ALPHABET.length]).join('');
}

/** Accepts a bare code or a whole invite link and returns the room name. */
function parseRoomCode(raw: string): string | null {
  let text = raw.trim();
  if (/^https?:\/\//i.test(text)) {
    try {
      text = new URL(text).searchParams.get('room') ?? '';
    } catch {
      return null;
    }
  }
  const clean = text.toLowerCase().replace(/[^a-z0-9-]/g, '').slice(0, 24);
  return clean || null;
}

async function joinOnline(room?: string): Promise<void> {
  matchKind = 'online';
  let target = room;
  if (!target) {
    try {
      const res = await fetch('/api/rooms/quick');
      const data = (await res.json()) as { room: string };
      target = data.room;
    } catch {
      target = 'arena-1';
    }
  }
  onlineRoom = target!;
  screens.openLobby(onlineRoom);
  screens.chat('', 'Connecting…', true);

  net.connect(onlineRoom, prefs.name, {
    onOpen: () => screens.chat('', 'Connected.', true),
    onLobby: (players, settings, host, countdown) => {
      void host;
      // The arena belongs to the room, for the host as much as anyone: it rolls
      // its own between matches, so the picker follows the broadcast rather
      // than this player's saved preference. Set before the lobby is drawn.
      screens.setLobbyArena(settings.mapId, settings.arenaLook ?? -1);
      if (!net.isHost) {
        // Guests follow the host's choices rather than their own saved ones.
        prefs = { ...prefs, ...settings, arena: settings.mapId };
        // The room's win condition decides what the HUD shows, so a guest has
        // to take the host's rather than keep their own.

        screens.setPrefs(prefs);
        screens.setLobby(players, net.isHost, countdown);
        return;
      }
      screens.setLobby(players, net.isHost, countdown);
      // Push local preferences up, but only when they actually differ.
      // Echoing on every lobby broadcast would loop at network speed.
      pushSettings(settings, players);
    },
    onStart: (settings, seeds) => {
      disposeMatch();
      attract = null;
      match = new OnlineMatch(settings, seeds, net);
      enterMatch(settings);
    },
    onEnd: (winners, standings) => showResults(winners, standings),
    onChat: (name, text, system) => screens.chat(name, text, system),
    onClose: () => {
      toast('Lost connection. Reconnecting…', { tone: 'bad', key: 'net', timeout: 3000 });
    },
    onRefused: (msg) => {
      // The room said no and meant it. Anything else leaves the player parked
      // on a lobby screen that will never fill in.
      toast(msg, { tone: 'bad', key: 'net', timeout: 5000 });
      leaveOnline();
    },
    onError: (msg) => toast(msg, { tone: 'bad', key: 'net' }),
  });
}

/** Reconciles the host's local preferences with the room, without echoing. */
function pushSettings(
  current: MatchSettings,
  players: Array<{ bot: boolean; botLevel: number }>,
): void {
  // Deliberately no `mapId`. The arena belongs to the room — the server rolls a
  // fresh one for each match — and echoing a joiner's saved preference into it
  // pinned every online match to whatever that one player last picked, which
  // for anybody who has never opened settings is Neon Grid for ever. Choosing
  // an arena from the settings screen still sends one, through `patchPrefs`.
  const desired = {
    mode: prefs.mode,
    roundSeconds: prefs.roundSeconds,
    lives: prefs.lives,
    winBy: prefs.winBy,
    killTarget: prefs.killTarget,
    suddenDeath: prefs.suddenDeath,
    friendlyFire: prefs.friendlyFire,
    itemDensity: prefs.itemDensity,
  };
  const differs = (Object.keys(desired) as Array<keyof typeof desired>).some(
    (k) => current[k] !== desired[k],
  );
  const key = JSON.stringify(desired);
  if (differs && key !== pushedSettings) {
    pushedSettings = key;
    net.send({ t: 'settings', patch: desired });
  }

  // Bots are a request, not a guarantee: the room clamps the count to the seats
  // the humans have not taken. Comparing against the roster we got back would
  // therefore re-send forever whenever it was clamped, so the check is against
  // what we last asked for.
  const wantBots = `${prefs.bots}/${prefs.botLevel}`;
  const bots = players.filter((p) => p.bot);
  if (wantBots !== pushedBots && (bots.length !== prefs.bots || bots.some((b) => b.botLevel !== prefs.botLevel))) {
    pushedBots = wantBots;
    net.send({ t: 'bots', count: prefs.bots, level: prefs.botLevel });
  }
}

function leaveOnline(): void {
  net.close();
  onlineRoom = '';
  pushedSettings = '';
  pushedBots = '';
  disposeMatch();
  mode = 'menu';
  hud.show(false);
  startAttract();
  screens.show('menu', { replace: true });
}

function copyInvite(): void {
  const url = `${location.origin}/?room=${encodeURIComponent(onlineRoom)}`;
  void navigator.clipboard
    ?.writeText(url)
    .then(() => toast('Invite link copied.', { tone: 'good', timeout: 2600 }))
    .catch(() => toast(url, { timeout: 8000 }));
}

/* -------------------------------------------------------------------------- */
/* Results                                                                     */
/* -------------------------------------------------------------------------- */

function showResults(
  winners: string[],
  standings: Array<{ id: string; name: string; slot: number; score: number; kills: number; deaths: number }>,
): void {
  if (resultsShown) return;
  resultsShown = true;
  mode = 'menu';
  hud.show(false);
  showTouch(false);
  updater.setBusy(false);

  const localIds = new Set(match?.localIds ?? []);
  const iWon = winners.some((w) => localIds.has(w));
  const title = winners.length === 0 ? 'Draw' : iWon ? 'Victory' : `${standings.find((s) => winners.includes(s.id))?.name ?? 'Nobody'} wins`;

  audio.duck(0.25, 2.4);
  audio.play(iWon ? 'victory-sting' : 'defeat-sting');
  if (iWon) audio.playMusic('victory', 0.4);

  screens.showResults(
    title,
    standings.map((s) => ({ ...s, win: winners.includes(s.id) })),
    matchKind === 'adventure'
      ? 'New Run'
      : matchKind === 'campaign'
        ? iWon && campaignStage + 1 < CAMPAIGN_STAGES.length
          ? 'Next Stage'
          : 'Retry Stage'
        : 'Rematch',
  );

  const state = match?.state;
  patchPrefs({
    totalMatches: prefs.totalMatches + 1,
    totalWins: prefs.totalWins + (iWon ? 1 : 0),
    bestSurvivalWave: Math.max(prefs.bestSurvivalWave, state?.wave ?? 0),
  });

  /*
   * Records the depth on its way out, and whether the run was actually
   * completed -- which only this path can tell: the last room ends the run
   * either way, and paying the completion bonus for dying in it would make the
   * ending a formality.
   */
  if (matchKind === 'adventure') {
    const cleared = iWon && (expedition?.index ?? 0) + 1 >= EXPEDITION_ROOMS;
    endExpedition(cleared);
  }

  if (matchKind === 'campaign' && iWon && campaignStage + 1 > prefs.campaign) {
    patchPrefs({ campaign: Math.min(CAMPAIGN_STAGES.length - 1, campaignStage + 1) });
    toast(`Stage ${campaignStage + 1} cleared.`, { tone: 'good' });
  }

  if (matchKind !== 'online' && state) {
    const me = state.players.find((p) => localIds.has(p.id));
    if (me) {
      void fetch('/api/offline-result', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({
          won: iWon,
          kills: me.kills,
          deaths: me.deaths,
          score: Math.max(0, me.score),
          wave: state.wave,
          durationMs: Math.round(performance.now() - matchStartedAt),
          /*
           * What the match looked like, not only how it ended.
           *
           * The online rooms have recorded this all along and local play has
           * not, so every balance question about how the game plays for a
           * person was being answered by bots -- while local play is where
           * most people, and every new one, actually meet it.
           */
          samples: match instanceof LocalMatch ? match.samples : undefined,
          /*
           * Whether this device could show HDR at all.
           *
           * One line, answering a question that would otherwise be settled by
           * taste: HDR output needs WebGPU, which means a renderer migration
           * and a re-grade of every arena. Worth knowing whether that reaches
           * five per cent of players or fifty before anybody starts.
           */
          hdr: (() => {
            try {
              return window.matchMedia('(dynamic-range: high)').matches;
            } catch {
              return null;
            }
          })(),
          rules: {
            mode: state.settings.mode,
            mapId: state.settings.mapId,
            seed: state.settings.seed,
            players: state.players.length,
            catchup: state.settings.catchup !== false,
            hitPoints: state.settings.hitPoints,
            resolve: state.settings.resolve,
            loseGearOnDeath: state.settings.loseGearOnDeath,
          },
        }),
      }).catch(() => {});
    }
  }
}

function localStandings(): Array<{ id: string; name: string; slot: number; score: number; kills: number; deaths: number }> {
  const state = match!.state;
  return [...state.players]
    .sort(rankBy(state.settings.winBy))
    .map((p) => ({ id: p.id, name: p.name, slot: p.slot, score: p.score, kills: p.kills, deaths: p.deaths }));
}

/**
 * Scoreboard order.
 *
 * Elimination ranks on lives remaining — surviving is the whole game. In a
 * deathmatch everybody has the same lives for ever, so ranking on them would
 * leave the board in seed order; kills are what the match was played for.
 */
export function rankBy(
  winBy: WinCondition,
): (a: { lives: number; score: number; kills: number; deaths: number }, b: typeof a) => number {
  if (winBy === 'lives') {
    return (a, b) => (b.lives || 0) - (a.lives || 0) || b.score - a.score || b.kills - a.kills;
  }
  return (a, b) => b.kills - a.kills || a.deaths - b.deaths || b.score - a.score;
}

/* -------------------------------------------------------------------------- */
/* Auth + leaderboard                                                          */
/* -------------------------------------------------------------------------- */

/**
 * Whether there is an account to sync progression to.
 *
 * Set from `/api/me`, which the app already calls. A separate probe would be a
 * second source of truth for the same fact.
 */
let signedInUser: string | null = null;

async function refreshProfile(): Promise<void> {
  try {
    const res = await fetch('/api/me');
    if (!res.ok) return;
    const data = (await res.json()) as {
      user: { id: string; name: string; anonymous: boolean } | null;
      stats?: {
        matches: number; wins: number; kills: number; deaths: number; best_wave: number;
        playtime_ms: number; elo: number; peak_elo: number; ranked_matches: number; ranked_wins: number;
      };
      rank?: number | null;
      rated?: number;
      pickups?: Array<{ item: number; count: number }>;
      achievements?: Array<{ code: string; name: string; blurb: string; earned: boolean; progress: number }>;
    };
    if (!data.user) {
      signedInUser = null;
      screens.renderProfile(null);
      return;
    }
    signedInUser = data.user.id;
    progression.activate(data.user.id);
    // Pull progression down on sign-in, so a second device arrives with what
    // the first one earned rather than starting over.
    void syncExpedition();
    // Settings and the profile used to disagree, and the profile was right: a
    // social sign-in brings the provider's name, the room labels you with the
    // account name (`X-DTN-Name` is set from the session, not from what the
    // client sends), and the settings field went on showing whatever this
    // device had saved. One name, and it is the account's.
    if (data.user.name && data.user.name !== prefs.name) {
      // Not `patchPrefs`: that one is the user changing a setting, and it
      // clicks. This is the app agreeing with the server.
      prefs = { ...prefs, name: data.user.name };
      savePrefs(prefs);
      screens.setPrefs(prefs);
      screens.renderSettings();
    }

    const st = data.stats;
    screens.renderProfile({
      name: data.user.name,
      provider: data.user.anonymous ? 'guest' : 'linked account',
      elo: st?.elo ?? 1000,
      peakElo: st?.peak_elo ?? 1000,
      rank: data.rank ?? null,
      rated: data.rated ?? 0,
      matches: st?.matches ?? 0,
      wins: st?.wins ?? 0,
      kills: st?.kills ?? 0,
      deaths: st?.deaths ?? 0,
      playtimeMs: st?.playtime_ms ?? 0,
      bestWave: st?.best_wave ?? 0,
      rankedMatches: st?.ranked_matches ?? 0,
      rankedWins: st?.ranked_wins ?? 0,
      pickups: data.pickups ?? [],
      achievements: data.achievements ?? [],
    });
  } catch {
    screens.renderProfile(null);
  }
}

async function signIn(provider: 'github' | 'google' | 'guest'): Promise<void> {
  try {
    if (provider === 'guest') {
      const res = await fetch('/api/auth/sign-in/anonymous', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: '{}',
      });
      if (!res.ok) throw new Error(String(res.status));
      await fetch('/api/profile', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ name: prefs.name }),
      });
      toast('Signed in as a guest. Your stats will sync from now on.', { tone: 'good' });
      void refreshProfile();
      return;
    }
    const res = await fetch('/api/auth/sign-in/social', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ provider, callbackURL: `${location.origin}/?signed-in=1` }),
    });
    const data = (await res.json()) as { url?: string; message?: string };
    if (data.url) location.href = data.url;
    else throw new Error(data.message || 'Provider is not configured on this deployment.');
  } catch (err) {
    toast(`Sign-in failed: ${(err as Error).message}`, { tone: 'bad', timeout: 6000 });
  }
}

async function signOut(): Promise<void> {
  try {
    // Better Auth rejects a POST with no content type outright — 415, before it
    // ever looks at the session. The old call sent none and ignored the status,
    // so the button reported success every time and signed nobody out.
    const res = await fetch('/api/auth/sign-out', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: '{}',
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
  } catch (err) {
    toast(`Could not sign out: ${(err as Error).message}`, { tone: 'bad', timeout: 5000 });
    void refreshProfile();
    return;
  }
  progression.activate(null);
  patchPrefs({ name: randomName() });
  toast('Signed out.', { timeout: 2400 });
  void refreshProfile();
}

/** Which ladder the leaderboard is showing; survives leaving and coming back. */
let boardScope: BoardScope = 'elo';

const BOARD_NOTE: Record<BoardScope, string> = {
  elo: 'Rating from online matches, and only those played by two or more signed-in players.',
  wins: 'Matches won, counting solo play as well as online.',
  score: 'Total points scored, counting solo play as well as online.',
};

async function refreshLeaderboard(scope: BoardScope = boardScope): Promise<void> {
  boardScope = scope;
  try {
    const res = await fetch(`/api/leaderboard?limit=50&scope=${scope}`);
    const data = (await res.json()) as {
      rows: Array<{ rank: number; name: string; score: number; wins: number; kills: number; elo: number; me: boolean }>;
    };
    screens.renderLeaderboard(data.rows, BOARD_NOTE[scope], scope);
  } catch {
    screens.renderLeaderboard(
      [
        { rank: 1, name: `${prefs.name} (this device)`, score: prefs.totalWins * 500, wins: prefs.totalWins, me: true },
      ],
      'Offline — showing local results only.',
      scope,
    );
  }
}

/* -------------------------------------------------------------------------- */
/* Frame loop                                                                  */
/* -------------------------------------------------------------------------- */

function frame(now: number): void {
  requestAnimationFrame(frame);
  framesDrawn++;
  perf?.frame(now);
  if (fixedStep !== null) return; // driven externally by the capture tool
  const dt = Math.min(0.1, (now - lastFrame) / 1000);
  lastFrame = now;
  renderFrame(dt);
}

/**
 * One frame of simulation + rendering.
 *
 * Split out from the rAF callback so the footage recorder can drive it at a
 * fixed timestep. Software rendering in CI manages about five frames a second,
 * and the game deliberately slows its own clock rather than dropping frames --
 * which records as slow motion. Stepping it by hand produces smooth, real-speed
 * output regardless of how long each frame actually took to draw.
 */
function renderFrame(dt: number): void {
  if (match && mode !== 'paused') {
    match.update(dt, input);
    const view = match.view();
    renderer.syncPlayers(view.players, dt);
    // Always, even when the array is empty: it is what puts the last room's
    // creatures away when the next room, or the menu, has none.
    renderer.syncEnemies(view.enemies);
    // Driven by the simulation's tick, not the render clock: a decal that
    // pulsed on wall time would drift out of step with the thing that hurts.
    renderer.syncHazards(match.state.tick);
    renderer.syncBombs(view.bombs);
    renderer.syncItems(match.state.items);
    renderer.syncGrid(match.state.grid);
    const events = match.drainEvents();
    handleEvents(events);
    if (expedition && (savedRoomTick < 0 || match.state.tick - savedRoomTick >= 30 || events.some(e => e.t === 'pickup' || e.t === 'phase'))) checkpointExpedition();
    if (training && mode === 'playing') {
      training.update(events, dt);
      hud.setObjective(training.view());
      renderer.setMarkers(training.markers);
    }

    if (mode === 'playing') {
      hud.update(match.state, match.localIds[0] ?? null, dt);
      hud.setPing(matchKind === 'online' ? match.ping : null);
      const me = view.players.find((p) => p.local && p.alive);
      renderer.setFollowTarget(me ? me.x : null, me ? me.y : 0);
      // Let the final explosion play out before the scoreboard slides in.
      if (!resultsShown && endTimer === null && !training && match.state.phase === 'over' && matchKind !== 'online') {
        endTimer = window.setTimeout(() => {
          endTimer = null;
          if (!match) return;
          /*
           * A cleared room is not the end of anything, so it does not get a
           * scoreboard. `advanceExpedition` starts the next room and says so;
           * only a death or the last room falls through to the results below.
           */
          if (matchKind === 'adventure' && advanceExpedition(match.state.winners)) return;
          showResults(match.state.winners, localStandings());
        }, 1400);
      }
    } else if (attract) {
      renderer.orbit += dt * 0.08;
      renderer.setFollowTarget(null);
      if (attract.state.phase === 'over') {
        attractRestart += dt * 1000;
        if (attractRestart >= ATTRACT_RESTART_MS) startAttract();
      }
    }
  }

  renderer.render(dt);
  if (preview && !perf?.active && (screens?.screen === 'menu' || screens?.screen === 'bombers')) {
    preview.render(dt);
  }
  watchdog(dt);
}

/**
 * Drops a quality tier if the device cannot hold a playable frame rate.
 *
 * Runs on the menu as well as in a match, which it did not before. The menu is
 * not idle -- there is a full attract match rendering behind the panels, at the
 * same tier a real one would use -- so it is the earliest honest measurement
 * available, and a guess that lands too high used to survive all the way to the
 * first match before anything questioned it. On a device where that guess is
 * bad enough to be fatal, the menu is where the damage is done.
 *
 * The first sampling window is thrown away wherever it happens. It contains
 * shader compilation, procedural texture generation and the first upload of
 * every buffer, none of which say anything about the frame rate that follows.
 * Judging a device on that window would drop tiers on hardware that is fine.
 */
function watchdog(dt: number): void {
  fpsAccum += dt;
  fpsFrames++;
  if (fpsAccum < 4) return;
  const fps = fpsFrames / fpsAccum;
  fpsAccum = 0;
  fpsFrames = 0;
  if (!fpsWarmedUp) {
    fpsWarmedUp = true;
    return;
  }
  const measurable = mode === 'playing' || (mode === 'menu' && attract !== null);
  if (prefs.quality !== 'auto' || !measurable) return;
  // A measurement whose renderer changes tier halfway through has measured
  // two different renderers and can name neither. `?perf=` is asking what
  // this tier costs; moving out from under it answers a different question.
  if (perf?.active) return;

  auto ??= newAutoState(resolveQuality(), prefs.autoBuild === __BUILD_ID__ ? prefs.autoCeiling : null);
  // Read fresh each window: the observer updates whenever the device changes
  // its mind, and this is the only place that acts on it.
  auto.pressure = pressure.pressure();
  const { state, action } = judgeQuality(fps, auto);
  auto = state;

  if (action.kind === 'settle') {
    // Held for twenty seconds. Start here next time rather than climbing to
    // it again, and only for this build -- see `autoBuild`.
    if (prefs.autoTier !== action.tier) {
      patchPrefs({ autoTier: action.tier, autoBuild: __BUILD_ID__ });
    }
    return;
  }
  if (action.kind === 'warn') {
    // Informative, and once per escalation. Nothing is changed on its
    // account: the frame-rate rules below still decide what runs.
    toast(
      action.pressure === 'critical'
        ? 'This device is running hot — it may start to stutter.'
        : 'This device is heating up.',
      { tone: 'info', timeout: 5000, key: 'pressure' },
    );
    return;
  }
  if (action.kind !== 'set') return;

  autoTier = action.to;
  // The window after a change is thrown away: a new shadow map size and a
  // rebuilt backdrop compile shaders, and judging a tier on the frames that
  // build it is how a device ends up oscillating.
  fpsWarmedUp = false;
  renderer.setQuality(action.to);
  /*
   * A climb is not written down until it has been held -- `settle` does that,
   * five windows later. Persisting it here would mean a tab closed during the
   * twelve seconds of probation left the device starting next time on a tier
   * nothing had yet shown it could run.
   *
   */
  if (action.why !== 'raise') {
    patchPrefs({
      autoTier: action.to,
      autoBuild: __BUILD_ID__,
      ...(action.why === 'revert' ? { autoCeiling: state.ceiling } : {}),
    });
  }
  toast(action.message, { timeout: 3600, key: 'quality' });
}

/* -------------------------------------------------------------------------- */
/* Events -> audio + VFX                                                       */
/* -------------------------------------------------------------------------- */

function handleEvents(events: GameEvent[]): void {
  if (!events.length) return;
  const state = match!.state;
  const localId = match!.localIds[0] ?? null;
  const panOf = (x: number) => (x / state.width - 0.5) * 1.5;

  for (const e of events) {
    switch (e.t) {
      case 'bomb':
        audio.play('bomb-place', { pan: panOf(e.x) });
        break;
      case 'explode': {
        renderer.explosion(e.cells, e.kinds, e.axes, e.power);
        audio.play(e.power >= 5 || e.cells.length > 14 ? 'explosion-big' : 'explosion', { pan: panOf(e.x) });
        if (prefs.haptics && mode === 'playing') haptics.buzz(18);
        break;
      }
      case 'break':
        renderer.breakBlock(e.x, e.y);
        audio.play('block-break', { pan: panOf(e.x), gain: 0.7 });
        break;
      case 'pickup': {
        renderer.pickupBurst(e.x, e.y, e.item);
        audio.play(e.item === Item.SKULL ? 'pickup-bad' : 'pickup', { pan: panOf(e.x) });
        if (mode === 'playing' && e.player === localId && e.item !== Item.SKULL) {
          hud.feed(`Picked up ${itemName(e.item)}`, SLOT_COLORS[0]);
        }
        break;
      }
      case 'death': {
        const victim = state.players.find((p) => p.id === e.player);
        renderer.deathBurst(e.x, e.y, victim?.slot ?? 0);
        audio.play('death', { pan: panOf(e.x) });
        const killer = e.killer ? state.players.find((p) => p.id === e.killer) : null;
        if (mode === 'playing') {
          if (killer && killer.id !== e.player) hud.feed(`${killer.name} → ${victim?.name ?? '?'}`, SLOT_COLORS[killer.slot % 8]);
          else hud.feed(`${victim?.name ?? '?'} blew themselves up`, SLOT_COLORS[(victim?.slot ?? 0) % 8]);
        }
        if (e.player === localId && prefs.haptics) haptics.buzz([30, 40, 30]);
        break;
      }
      case 'zap':
        audio.play('curse', { pan: panOf(e.x) });
        renderer.deathBurst(e.x, e.y, 0);
        if (e.player === localId && prefs.haptics) haptics.buzz([18, 24, 18]);
        break;
      case 'launch':
        audio.play('punch', { pan: panOf(e.x) });
        if (e.player === localId && prefs.haptics) haptics.buzz(26);
        break;
      case 'enemy-hurt':
        // No dedicated sound: a creature surviving a blast is a solid thing
        // being struck, which is what `block-break` already is.
        audio.play('block-break', { pan: panOf(e.x) });
        break;
      case 'enemy-death': {
        renderer.enemyBurst(e.x, e.y, e.kind);
        audio.play('death', { pan: panOf(e.x) });
        /*
         * How many are left, in the kill feed.
         *
         * A solo room has no scoreboard to read -- one player card and no
         * opponents -- so without this the only way to know whether the room
         * is nearly done is to sweep it. Hunting the last drifter around a
         * board you have already cleared is the oldest frustration in the
         * genre, and a number is the whole fix.
         */
        const left = state.enemies.filter((x) => x.alive).length;
        if (mode === 'playing' && left > 0) hud.feed(`${left} left`, ENEMY_FEED_COLOR);
        break;
      }
      case 'room-clear':
        // No banner: `advanceExpedition` is already about to name the next
        // room, and two announcements over one event read as a stutter.
        audio.play('victory-sting');
        break;
      case 'kick':
        audio.play('kick', { pan: panOf(e.x) });
        break;
      case 'punch':
        audio.play('punch', { pan: panOf(e.x) });
        break;
      case 'curse':
        audio.play('curse');
        if (mode === 'playing' && e.player === localId) hud.banner('CURSED', 1.2);
        break;
      /*
       * Both halves of the comeback are announced, not just the one that
       * helps you.
       *
       * A player who is handed an Overload has to know they have it or they
       * will spend it on a wall, and everyone else has to know it is coming or
       * the mechanic is a surprise rather than something to play around. That
       * is the whole reason the armed state is on the wire.
       */
      case 'hurt':
        // Distinct from a death: a hit that was survived has to read as a hit,
        // or a player who lost a point will think they got away with it.
        audio.play('pickup-bad', { pan: panOf(e.x), gain: 0.75 });
        if (mode === 'playing' && e.player === localId) {
          hud.banner(`${Math.round(e.hp)}%`, 0.9);
          if (prefs.haptics) haptics.buzz([25]);
        }
        break;
      case 'charge':
        if (mode === 'playing' && e.player === localId) {
          audio.play('shield', { gain: 0.9 });
          hud.banner('OVERLOAD READY', 1.6);
        }
        break;
      case 'overload':
        renderer.crushBurst(e.x, e.y);
        audio.play('detonate', { pan: panOf(e.x), gain: 0.8 });
        break;
      case 'crush':
        renderer.crushBurst(e.x, e.y);
        audio.play('block-drop', { pan: panOf(e.x), gain: 0.7 });
        break;
      /*
       * A supply drop announces itself twice, because an event nobody sees
       * coming is a surprise rather than something to play around -- the same
       * reason the Overload's armed state is on the wire.
       */
      case 'supply-soon':
        if (mode === 'playing') {
          audio.play('shield', { gain: 0.7 });
          hud.banner('SUPPLY CRATES', 1.4);
        }
        break;
      case 'supply':
        // No burst over the cells: what is inside a crate is the part worth
        // not spoiling. The blocks appearing is the announcement -- `syncGrid`
        // rebuilds in both directions, so they simply arrive.
        audio.play('pickup-good', { gain: 0.9 });
        break;
      case 'sudden-death':
        audio.play('sudden-death');
        if (mode === 'playing') {
          audio.playMusic('sudden-death', 0.6);
          hud.banner('SUDDEN DEATH', 2.2);
        }
        break;
      case 'wave':
        audio.play('round-start');
        if (mode === 'playing') hud.banner(`WAVE ${e.wave}`, 1.8);
        break;
      case 'phase':
        if (e.phase === 'playing' && mode === 'playing') {
          audio.play('round-start');
          hud.banner('GO!', 1.0);
        }
        break;
    }
  }
}

function itemName(item: number): string {
  const names: Record<number, string> = {
    [Item.BOMB]: 'an extra bomb',
    [Item.FIRE]: 'more fire',
    [Item.SPEED]: 'speed',
    [Item.KICK]: 'kick',
    [Item.PUNCH]: 'the power glove',
    [Item.REMOTE]: 'the detonator',
    [Item.PIERCE]: 'pierce bombs',
    [Item.SHIELD]: 'a shield',
    [Item.FULLFIRE]: 'full fire',
    [Item.HEART]: 'an extra life',
    [Item.GHOST]: 'phase boots',
  };
  return names[item] ?? 'something';
}

/* -------------------------------------------------------------------------- */
/* Media config                                                                */
/* -------------------------------------------------------------------------- */

/**
 * Fetches the media base URL. Bounded by a short timeout and never fatal: the
 * default `/media` is already correct, so a slow or absent network must not
 * hold the player on the loading screen.
 */
async function loadMediaConfig(): Promise<void> {
  try {
    const controller = new AbortController();
    const timer = window.setTimeout(() => controller.abort(), 2500);
    const res = await fetch('/api/config', { cache: 'no-cache', signal: controller.signal });
    window.clearTimeout(timer);
    if (!res.ok) return;
    const data = (await res.json()) as { mediaBase?: string };
    if (data.mediaBase) audio.setBase(data.mediaBase);
  } catch {
    /* offline, slow, or aborted: the default /media base is correct anyway */
  }
}

/* -------------------------------------------------------------------------- */

void boot();

// Expose a tiny surface for the capture tool and for debugging in the console.
Object.assign(window as unknown as Record<string, unknown>, {
  dtn: {
    get renderer() {
      return renderer;
    },
    get preview() {
      return preview;
    },
    get match() {
      return match;
    },
    get screens() {
      return screens;
    },
    get hud() {
      return hud;
    },
    get audio() {
      return audio;
    },
    get net() {
      return net;
    },
    get input() {
      return input;
    },
    startAttract,
    startLocalMatch,
    /** Freezes real-time stepping; `stepFrame` then advances the world by hand. */
    beginCapture(fps = 24) {
      fixedStep = 1 / fps;
      /*
       * Fixing the step is only half of reproducibility. The animation clocks
       * have been running in real time since boot, and boot does not take the
       * same time twice -- so two capture runs that step the same number of
       * frames still photograph different moments. Rewind them here, where
       * every capture tool already passes.
       */
      // `renderer` is only assigned once the async boot resolves, and `dtn` is
      // reachable before that -- a tool that starts capturing the moment the
      // page appears would otherwise take the whole surface down with a
      // TypeError. There is nothing to rewind before the first frame anyway.
      renderer?.resetClocks();
    },
    endCapture() {
      fixedStep = null;
      lastFrame = performance.now();
    },
    stepFrame(times = 1) {
      const dt = fixedStep ?? 1 / 24;
      for (let i = 0; i < times; i++) renderFrame(dt);
    },
    /**
     * Frame cost and where it goes, for judging a change to the scene rather
     * than guessing at it. Read after a frame has been drawn.
     */
    stats() {
      return renderer.stats();
    },
    /**
     * What the scene costs in memory, unique uploads counted once.
     *
     * Beside `stats` because the pair is the point: an instanced ring is
     * counted every time it is drawn there and once here, and a candidate can
     * be dear in one and cheap in the other.
     */
    memory() {
      return renderer.memory();
    },
    setUiHidden(hidden: boolean) {
      uiRoot.style.display = hidden ? 'none' : '';
      hudRoot.style.display = hidden ? 'none' : '';
    },
    /**
     * The run in progress, for the capture tools and for testing that a room
     * transition actually happened rather than inferring it from a stat that
     * a test could have changed itself.
     */
    expedition() {
      return expedition
        ? { index: expedition.index, rooms: expedition.rooms.map((r) => r.map), gear: expedition.gear }
        : null;
    },
    /** What GPU this build thinks it is on, and what it switched off for it. */
    gpu() {
      return { renderer: gpuRenderer, quirks: gpuQuirks, shadows: renderer.shadowsEnabled };
    },
    setArena(id: string, look = -1) {
      pinnedArena = id;
      patchPrefs({ arena: id, arenaLook: look });
      startAttract(id);
    },
    /** A seed that makes an arena wear a particular look, for capture tools. */
    seedForLook(id: string, look: number) {
      return seedForLook(getArena(id), look);
    },
    version: __APP_VERSION__,
    buildId: __BUILD_ID__,
  },
});

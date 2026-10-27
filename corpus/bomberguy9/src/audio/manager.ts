import { MEDIA_BASE, MUSIC, SFX, type MusicTrack } from './manifest';

/** Longest handover between a loop and its repeat. */
const LOOP_CROSSFADE_SECONDS = 3;

/**
 * 50ms of silence, used to bless a deck inside a user gesture. See
 * `AudioManager.activateDecks`.
 */
const SILENT_CLIP =
  'data:audio/wav;base64,UklGRmYDAABXQVZFZm10IBAAAAABAAEAQB8AAIA+AAACABAATElTVBoAAABJTkZPSVNGVA4AAABMYXZmNjAuMTYuMTAwAGRhdGEgAwAA' +
  'AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA' +
  'AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA' +
  'AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA' +
  'AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA' +
  'AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA' +
  'AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA' +
  'AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA' +
  'AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA' +
  'AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA' +
  'AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA' +
  'AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA' +
  'AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA' +
  'AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA';

export interface AudioSettings {
  master: number;
  music: number;
  sfx: number;
  muted: boolean;
}

/**
 * Web Audio front-end.
 *
 * Sound effects are decoded into buffers and fired through a shared bus with
 * per-source pitch variation and stereo placement. Music streams through two
 * `<audio>` elements so we can crossfade between arenas without preloading
 * multi-megabyte files — and so a looping track can crossfade into *itself*
 * rather than falling off the end of its own fade-out.
 *
 * Starting up is two steps, not one. `prime()` builds the graph and decodes the
 * effects; it needs no user gesture, and on a browser that allows autoplay it
 * also starts the music. `resume()` is what a gesture calls, and only matters
 * where the browser held playback back.
 */
export class AudioManager {
  private ctx: AudioContext | null = null;
  private masterGain: GainNode | null = null;
  private sfxGain: GainNode | null = null;
  private musicGain: GainNode | null = null;
  private compressor: DynamicsCompressorNode | null = null;

  private buffers = new Map<string, AudioBuffer>();
  private lastPlayed = new Map<string, number>();
  private pending = new Map<string, Promise<AudioBuffer | null>>();

  private decks: HTMLAudioElement[] = [];
  private deckSources: Array<MediaElementAudioSourceNode | null> = [null, null];
  private deckGains: GainNode[] = [];
  private activeDeck = 0;
  private currentTrack: string | null = null;
  private fadeTimer: number | null = null;
  /** Set when the browser refused a `play()`; retried on the next gesture. */
  private blockedTrack: string | null = null;
  /** Guards the loop handover against firing twice on the same pass. */
  private looping = false;
  /** Per-deck: has a person ever started this element? See `activateDecks`. */
  private activated = [false, false];
  /** Per-deck: is the end-of-track safety net wired up? */
  private endGuard = [false, false];
  private loopTimer: number | null = null;

  private base = MEDIA_BASE;
  private started = false;
  private failed = new Set<string>();

  settings: AudioSettings = { master: 0.8, music: 0.55, sfx: 0.9, muted: false };

  /**
   * Scales every sound effect. The menu runs a live bot match behind it, and
   * its explosions should sit under the music rather than compete with it.
   */
  sceneGain = 1;

  setBase(url: string): void {
    this.base = url.replace(/\/$/, '');
  }

  /**
   * Builds the audio graph. Safe to call before any user interaction: a fresh
   * `AudioContext` is allowed everywhere, it simply starts out suspended where
   * the browser wants a gesture first. Doing this at boot rather than on the
   * first click means the effects are already decoded and the music is already
   * buffered by the time anybody presses anything — and on desktop Chrome,
   * installed PWAs and anywhere the user has played before, `resume()` succeeds
   * straight away and the game has sound from the title screen.
   */
  async prime(): Promise<void> {
    if (this.started) return;
    const Ctx = window.AudioContext || (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext;
    if (!Ctx) return;
    this.ctx = new Ctx();
    this.masterGain = this.ctx.createGain();
    this.compressor = this.ctx.createDynamicsCompressor();
    this.compressor.threshold.value = -12;
    this.compressor.knee.value = 24;
    this.compressor.ratio.value = 6;
    this.compressor.attack.value = 0.003;
    this.compressor.release.value = 0.22;
    this.sfxGain = this.ctx.createGain();
    this.musicGain = this.ctx.createGain();
    this.sfxGain.connect(this.compressor);
    this.musicGain.connect(this.compressor);
    this.compressor.connect(this.masterGain);
    this.masterGain.connect(this.ctx.destination);

    for (let i = 0; i < 2; i++) {
      const el = new Audio();
      el.crossOrigin = 'anonymous';
      el.preload = 'none';
      el.loop = true;
      this.decks.push(el);
      const g = this.ctx.createGain();
      g.gain.value = 0;
      g.connect(this.musicGain);
      this.deckGains.push(g);
    }

    this.started = true;
    this.applyVolumes();
    // Driven here rather than only from the render loop. `requestAnimationFrame`
    // does not run in a backgrounded tab and is throttled hard on a phone, and
    // missing the handover window costs the whole loop; a timer is throttled to
    // about a second in the worst case, which still lands inside it.
    this.loopTimer = window.setInterval(() => this.tick(), 250);
    await this.resume();
  }

  /**
   * Tries to get sound actually playing. Called once at boot on the off-chance
   * the browser allows it, and again on every early gesture until it does.
   */
  async resume(): Promise<boolean> {
    if (!this.ctx) return false;
    // Before the first `await`, so these `play()` calls are still inside the
    // gesture that reached us.
    this.activateDecks();
    if (this.ctx.state === 'suspended') {
      try {
        await this.ctx.resume();
      } catch {
        return false;
      }
    }
    if (this.ctx.state !== 'running') return false;

    // A track whose `play()` was refused is still the current track, so asking
    // for it again would be a no-op. Clear it first, then ask.
    if (this.blockedTrack) {
      const key = this.blockedTrack;
      this.blockedTrack = null;
      this.currentTrack = null;
      this.playMusic(key, 0.8);
    }
    return true;
  }

  /**
   * Blesses both music decks inside a user gesture.
   *
   * iOS refuses `play()` on a media element that a person has never started,
   * and *both* decks get started by script rather than by a tap: the standby
   * deck is what a loop hands over to, and an online match starts its arena
   * track from a WebSocket message. So the first time a gesture reaches us,
   * each deck plays fifty milliseconds of silence and stops. After that the
   * element is blessed for the rest of the session, whatever `src` it is given.
   *
   * Without this the menu music plays once and stops — the handover's `play()`
   * is refused, the outgoing deck finishes its fade, and nothing replaces it.
   */
  private activateDecks(): void {
    for (let i = 0; i < this.decks.length; i++) {
      if (this.activated[i]) continue;
      const el = this.decks[i];
      // Already sounding: a person, or an allowed autoplay, got there first.
      if (el.src && !el.paused) {
        this.activated[i] = true;
        continue;
      }
      // A standby deck cued with a real track is held at gain 0, so waking it
      // for a moment is silent. An empty deck needs something to play at all.
      if (!el.src) el.src = SILENT_CLIP;
      const started = el.play();
      if (!started) {
        this.activated[i] = true;
        continue;
      }
      started
        .then(() => {
          // Unless the loop handed over to this deck while we were waiting.
          if (i !== this.activeDeck) el.pause();
          this.activated[i] = true;
        })
        .catch(() => {
          /* still blocked; the next gesture tries again */
        });
    }
  }

  /** True while a track is waiting on a gesture the browser has not seen yet. */
  get blocked(): boolean {
    return this.blockedTrack !== null || this.activated.some((a) => !a);
  }

  applyVolumes(): void {
    if (!this.masterGain) return;
    const m = this.settings.muted ? 0 : this.settings.master;
    this.masterGain.gain.value = m;
    if (this.sfxGain) this.sfxGain.gain.value = this.settings.sfx;
    if (this.musicGain) this.musicGain.gain.value = this.settings.music;
  }

  /** Warms the cache for the effects used every round. */
  async preloadCore(onProgress?: (done: number, total: number) => void): Promise<void> {
    const core = [
      'bomb-place',
      'explosion',
      'explosion-big',
      'block-break',
      'pickup',
      'death',
      'ui-click',
      'countdown',
      'round-start',
    ];
    let done = 0;
    await Promise.all(
      core.map(async (key) => {
        await this.load(key);
        onProgress?.(++done, core.length);
      }),
    );
    // The rest can trickle in behind the first frame.
    void Promise.all(SFX.filter((s) => !core.includes(s.key)).map((s) => this.load(s.key)));
  }

  private async load(key: string): Promise<AudioBuffer | null> {
    if (!this.ctx) return null;
    const cached = this.buffers.get(key);
    if (cached) return cached;
    if (this.failed.has(key)) return null;
    const inflight = this.pending.get(key);
    if (inflight) return inflight;

    const def = SFX.find((s) => s.key === key);
    if (!def) return null;

    const job = (async () => {
      try {
        const res = await fetch(`${this.base}/${def.file}`, { cache: 'force-cache' });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const bytes = await res.arrayBuffer();
        const buf = await this.ctx!.decodeAudioData(bytes);
        this.buffers.set(key, buf);
        return buf;
      } catch {
        // Missing audio must never break the game; it just goes quiet.
        this.failed.add(key);
        return null;
      } finally {
        this.pending.delete(key);
      }
    })();
    this.pending.set(key, job);
    return job;
  }

  /**
   * Fires a one-shot. `pan` is -1..1 and is normally derived from where the
   * event happened relative to the centre of the arena.
   */
  play(key: string, opts: { pan?: number; gain?: number; rate?: number } = {}): void {
    if (!this.ctx || !this.sfxGain || this.settings.muted) return;
    const def = SFX.find((s) => s.key === key);
    if (!def) return;

    const now = performance.now();
    const last = this.lastPlayed.get(key) ?? -1e9;
    if (now - last < def.throttle) return;
    this.lastPlayed.set(key, now);

    const buf = this.buffers.get(key);
    if (!buf) {
      void this.load(key);
      return;
    }

    const src = this.ctx.createBufferSource();
    src.buffer = buf;
    src.playbackRate.value = (opts.rate ?? 1) * (1 + (Math.random() - 0.5) * 2 * def.vary);

    const gain = this.ctx.createGain();
    gain.gain.value = def.gain * (opts.gain ?? 1) * this.sceneGain;
    if (gain.gain.value < 0.002) return;

    if (opts.pan !== undefined && this.ctx.createStereoPanner) {
      const panner = this.ctx.createStereoPanner();
      panner.pan.value = Math.max(-1, Math.min(1, opts.pan));
      src.connect(gain).connect(panner).connect(this.sfxGain);
    } else {
      src.connect(gain).connect(this.sfxGain);
    }
    src.start();
  }

  /** Crossfades to a music track. Passing null fades everything out. */
  playMusic(key: string | null, fadeSeconds = 1.2): void {
    if (!this.ctx || !this.decks.length) return;
    if (key === this.currentTrack) return;
    this.currentTrack = key;
    this.looping = false;

    const from = this.activeDeck;
    const to = 1 - from;

    let incoming: MusicTrack | null = null;
    if (key) {
      const track = MUSIC.find((m) => m.key === key);
      if (!track) return;
      incoming = track;
      const el = this.deck(to, track);
      // From the top the first time round: the quiet intro a loop skips is
      // still the composer's opening.
      el.currentTime = 0;
      this.start(el, key);
      this.rampGain(this.deckGains[to], track.gain, fadeSeconds);
      this.activeDeck = to;
    }

    this.rampGain(this.deckGains[from], 0, fadeSeconds);
    if (this.fadeTimer) window.clearTimeout(this.fadeTimer);
    this.fadeTimer = window.setTimeout(() => {
      if (!incoming) {
        // Fading out to nothing: stop both, or the elements keep streaming
        // silently for the rest of the session.
        for (const d of this.decks) d.pause();
        return;
      }
      if (this.activeDeck === from) return;
      this.decks[from].pause();
      // Only now is the old deck free to be re-pointed. Cueing it at the same
      // moment the new track starts would assign a new `src` to an element that
      // is still playing the outgoing one, which stops it dead — the crossfade
      // out of an arena would be a hard cut.
      if (incoming.loop) this.cue(from, incoming);
    }, fadeSeconds * 1000 + 60);
  }

  /**
   * Hands a looping track over to the other deck before it reaches its own
   * fade-out.
   *
   * Called every frame. `<audio loop>` would restart the file at the sample
   * after the last one, which for these tracks means a two-second fade to
   * silence followed by a hard cut back to the top — the gap the player hears.
   * Instead the second deck is already cued at the loop point, and the two
   * crossfade over the bar or so before the fade-out would have begun. The
   * outgoing deck never plays its tail at all.
   */
  tick(): void {
    if (!this.ctx || this.ctx.state !== 'running' || !this.currentTrack) return;
    const track = MUSIC.find((m) => m.key === this.currentTrack);
    if (!track?.loop) return;

    const el = this.decks[this.activeDeck];
    if (el.paused || !Number.isFinite(el.duration) || el.duration <= 0) return;
    const end = this.loopEnd(track, el.duration);
    const fade = this.loopFade(track, el.duration);
    // A loop shorter than the handover would hand over on every frame. Only
    // reachable if a regenerated file is far shorter than its manifest entry,
    // but a spinning handover is a much worse failure than not looping.
    if (end - (track.loopStart ?? 0) < fade + 1) return;
    if (el.currentTime < end - fade) {
      this.looping = false;
      return;
    }
    if (this.looping) return;
    this.looping = true;

    const from = this.activeDeck;
    const to = 1 - from;
    const next = this.deck(to, track);
    next.currentTime = track.loopStart ?? 0;
    this.start(next, track.key);
    this.rampGain(this.deckGains[to], track.gain, fade);
    this.rampGain(this.deckGains[from], 0, fade);
    this.activeDeck = to;

    window.setTimeout(
      () => {
        if (this.activeDeck === from) return;
        // Park it back at the loop point rather than stopping dead, so the
        // next handover has nothing left to prepare.
        this.decks[from].pause();
        try {
          this.decks[from].currentTime = track.loopStart ?? 0;
        } catch {
          /* seeking a deck the browser has unloaded is not worth reporting */
        }
      },
      fade * 1000 + 120,
    );
  }

  /** Where a repeat hands over: before the track's own fade-out, if it has one. */
  private loopEnd(track: MusicTrack, duration: number): number {
    const end = track.loopEnd ?? duration - 0.15;
    return Math.max(0.5, Math.min(duration, end));
  }

  private loopFade(track: MusicTrack, duration: number): number {
    const span = this.loopEnd(track, duration) - (track.loopStart ?? 0);
    // Long enough to hide the seam, never more than a quarter of the loop.
    return Math.max(0.4, Math.min(LOOP_CROSSFADE_SECONDS, span / 4));
  }

  /**
   * Last line of defence for a loop.
   *
   * `tick` hands a track over during a three-second window before its fade-out.
   * If nothing ran during that window — a locked phone, a backgrounded tab, a
   * frame that took too long — the deck plays out to the end and pauses, and
   * every later `tick` bails on `el.paused` with the music already silent. That
   * is the "menu music does not loop" report, and no amount of tuning the
   * window fixes it, because the window can always be missed. Reaching the end
   * at all is the bug; restarting from the loop point is the recovery.
   */
  private wireEndGuard(index: number): void {
    if (this.endGuard[index]) return;
    this.endGuard[index] = true;
    this.decks[index].addEventListener('ended', () => {
      const track = MUSIC.find((m) => m.key === this.currentTrack);
      if (!track?.loop || this.activeDeck !== index) return;
      const el = this.decks[index];
      try {
        el.currentTime = track.loopStart ?? 0;
      } catch {
        /* not seekable; `play()` from wherever it is beats silence */
      }
      this.looping = false;
      this.start(el, track.key);
      this.rampGain(this.deckGains[index], track.gain, 0.3);
    });
  }

  /** Points a deck at a track, wiring its source node the first time. */
  private deck(index: number, track: MusicTrack): HTMLAudioElement {
    const el = this.decks[index];
    const url = `${this.base}/${track.file}`;
    if (!this.deckSources[index]) {
      this.deckSources[index] = this.ctx!.createMediaElementSource(el);
      this.deckSources[index]!.connect(this.deckGains[index]);
    }
    this.wireEndGuard(index);
    // Never native-loop: the handover is ours to make.
    el.loop = false;
    el.preload = 'auto';
    // `endsWith` rather than a plain compare: a deck may still be holding the
    // silent activation clip, and `el.src` is resolved to an absolute URL.
    if (!el.src.endsWith(track.file)) el.src = url;
    return el;
  }

  /** Loads the other deck with the same track, cued at the loop point. */
  private cue(index: number, track: MusicTrack): void {
    const el = this.deck(index, track);
    this.deckGains[index].gain.cancelScheduledValues(this.ctx!.currentTime);
    this.deckGains[index].gain.value = 0;
    const seek = () => {
      try {
        el.currentTime = track.loopStart ?? 0;
      } catch {
        /* not seekable yet; `tick` sets it again at handover time */
      }
    };
    // `load()` resets the element, including `currentTime`, so the seek has to
    // come after it rather than before.
    el.load();
    if (el.readyState >= 1) seek();
    else el.addEventListener('loadedmetadata', seek, { once: true });
  }

  private start(el: HTMLAudioElement, key: string): void {
    void el.play().catch(() => {
      // Autoplay refused. Remember which track we owe the player so the first
      // gesture can start it, instead of leaving the game silent forever
      // because `currentTrack` says it is already playing.
      this.blockedTrack = key;
    });
  }

  /**
   * Fades a deck on an equal-power curve.
   *
   * Two decks crossfading on straight lines sum to about 3 dB below either of
   * them at the halfway point — an audible sag in the middle of every handover,
   * which on a loop is the very thing the crossfade exists to hide. A sine/
   * cosine pair sums to a constant instead.
   */
  private rampGain(node: GainNode, target: number, seconds: number): void {
    if (!this.ctx) return;
    const t = this.ctx.currentTime;
    const start = node.gain.value;
    const dur = Math.max(0.01, seconds);
    node.gain.cancelScheduledValues(t);
    if (Math.abs(target - start) < 1e-4) {
      node.gain.setValueAtTime(target, t);
      return;
    }
    const steps = 64;
    const curve = new Float32Array(steps);
    const rising = target > start;
    for (let i = 0; i < steps; i++) {
      const x = (i / (steps - 1)) * (Math.PI / 2);
      curve[i] = rising
        ? start + (target - start) * Math.sin(x)
        : target + (start - target) * Math.cos(x);
    }
    try {
      node.gain.setValueCurveAtTime(curve, t, dur);
    } catch {
      // An overlapping curve is still scheduled. A straight line is worse but
      // never silent, which is the property that matters.
      node.gain.setValueAtTime(start, t);
      node.gain.linearRampToValueAtTime(target, t + dur);
    }
  }

  /** Temporarily lowers music, e.g. during a victory sting. */
  duck(amount = 0.35, seconds = 1.4): void {
    if (!this.musicGain || !this.ctx) return;
    const t = this.ctx.currentTime;
    const full = this.settings.music;
    this.musicGain.gain.cancelScheduledValues(t);
    this.musicGain.gain.setValueAtTime(this.musicGain.gain.value, t);
    this.musicGain.gain.linearRampToValueAtTime(full * amount, t + 0.12);
    this.musicGain.gain.linearRampToValueAtTime(full, t + seconds);
  }

  stopAll(): void {
    this.playMusic(null, 0.4);
  }

  /** Tears down the timer that drives the loop handover. */
  dispose(): void {
    if (this.loopTimer !== null) window.clearInterval(this.loopTimer);
    this.loopTimer = null;
  }

  /** True once the graph exists, whether or not the browser has let it sound. */
  get ready(): boolean {
    return this.started;
  }

  /** True when audio is actually audible rather than waiting on a gesture. */
  get running(): boolean {
    return this.ctx?.state === 'running';
  }

  /** What the two music decks are doing. Used by `tools/audio-test.mjs`. */
  get musicState(): {
    track: string | null;
    active: number;
    blocked: string | null;
    loopStart: number;
    loopEnd: number;
    fade: number;
    decks: Array<{ src: string; time: number; duration: number; paused: boolean; gain: number }>;
  } {
    const track = MUSIC.find((m) => m.key === this.currentTrack);
    const duration = this.decks[this.activeDeck]?.duration ?? 0;
    const known = !!track && Number.isFinite(duration) && duration > 0;
    return {
      track: this.currentTrack,
      active: this.activeDeck,
      blocked: this.blockedTrack,
      loopStart: track?.loopStart ?? 0,
      loopEnd: known ? this.loopEnd(track!, duration) : 0,
      fade: known ? this.loopFade(track!, duration) : 0,
      decks: this.decks.map((el, i) => ({
        src: el.src.split('/').pop() ?? '',
        time: el.currentTime,
        duration: el.duration,
        paused: el.paused,
        gain: this.deckGains[i]?.gain.value ?? 0,
      })),
    };
  }
}

export const audio = new AudioManager();

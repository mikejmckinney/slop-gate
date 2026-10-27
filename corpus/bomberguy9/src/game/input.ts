import { buzz } from '../ui/haptics';
import type { Input } from '../shared/types';

/**
 * Unified controller layer.
 *
 * Every control scheme funnels into the same four-field `Input` the simulation
 * consumes, so a bot, a thumbstick, a keyboard and a gamepad are all
 * interchangeable.
 */

export type ControllerId = 0 | 1;

const KEYMAPS: Array<Record<string, keyof RawState>> = [
  {
    KeyW: 'up',
    KeyA: 'left',
    KeyS: 'down',
    KeyD: 'right',
    ArrowUp: 'up',
    ArrowLeft: 'left',
    ArrowDown: 'down',
    ArrowRight: 'right',
    Space: 'bomb',
    ShiftLeft: 'action',
    KeyE: 'action',
    Enter: 'bomb',
  },
  // Second local player: arrows + numpad, so two people share one keyboard.
  {
    ArrowUp: 'up',
    ArrowLeft: 'left',
    ArrowDown: 'down',
    ArrowRight: 'right',
    Numpad0: 'bomb',
    NumpadEnter: 'bomb',
    Enter: 'bomb',
    ControlRight: 'action',
    ShiftRight: 'action',
    Slash: 'action',
  },
];

interface RawState {
  up: boolean;
  down: boolean;
  left: boolean;
  right: boolean;
  bomb: boolean;
  action: boolean;
}

const blank = (): RawState => ({ up: false, down: false, left: false, right: false, bomb: false, action: false });

export interface TouchState {
  active: boolean;
  dx: number;
  dy: number;
  bomb: boolean;
  action: boolean;
}

export class InputManager {
  private keys: RawState[] = [blank(), blank()];
  /** Most recently pressed axis, so opposite keys resolve predictably. */
  private lastAxis: Array<'x' | 'y'> = ['x', 'x'];
  private touch: TouchState = { active: false, dx: 0, dy: 0, bomb: false, action: false };
  private gamepadIndex: number[] = [];
  private deadzone = 0.32;
  /** True when the player has used a touch control at least once. */
  touchUsed = false;
  keyboardUsed = false;
  gamepadUsed = false;

  onPause: (() => void) | null = null;
  onAnyInput: (() => void) | null = null;

  private handlers: Array<[string, EventListener]> = [];

  attach(target: Window = window): void {
    const down = ((e: KeyboardEvent) => {
      if (e.repeat) return;
      if (e.code === 'Escape') {
        this.onPause?.();
        return;
      }
      let hit = false;
      for (let i = 0; i < KEYMAPS.length; i++) {
        const key = KEYMAPS[i][e.code];
        if (!key) continue;
        hit = true;
        this.keys[i][key] = true;
        if (key === 'left' || key === 'right') this.lastAxis[i] = 'x';
        if (key === 'up' || key === 'down') this.lastAxis[i] = 'y';
      }
      if (hit) {
        this.keyboardUsed = true;
        this.onAnyInput?.();
        // Stop the page scrolling under the arena.
        if (e.code.startsWith('Arrow') || e.code === 'Space') e.preventDefault();
      }
    }) as EventListener;

    const up = ((e: KeyboardEvent) => {
      for (let i = 0; i < KEYMAPS.length; i++) {
        const key = KEYMAPS[i][e.code];
        if (key) this.keys[i][key] = false;
      }
    }) as EventListener;

    const blur = (() => {
      this.keys = [blank(), blank()];
    }) as EventListener;

    target.addEventListener('keydown', down, { passive: false });
    target.addEventListener('keyup', up);
    target.addEventListener('blur', blur);
    this.handlers.push(['keydown', down], ['keyup', up], ['blur', blur]);

    const connect = ((e: GamepadEvent) => {
      this.gamepadUsed = true;
      if (!this.gamepadIndex.includes(e.gamepad.index)) this.gamepadIndex.push(e.gamepad.index);
    }) as EventListener;
    const disconnect = ((e: GamepadEvent) => {
      this.gamepadIndex = this.gamepadIndex.filter((i) => i !== e.gamepad.index);
    }) as EventListener;
    target.addEventListener('gamepadconnected', connect);
    target.addEventListener('gamepaddisconnected', disconnect);
    this.handlers.push(['gamepadconnected', connect], ['gamepaddisconnected', disconnect]);
  }

  detach(target: Window = window): void {
    for (const [name, fn] of this.handlers) target.removeEventListener(name, fn);
    this.handlers = [];
  }

  setTouch(state: Partial<TouchState>): void {
    Object.assign(this.touch, state);
    if (state.active || state.bomb || state.action) {
      this.touchUsed = true;
      this.onAnyInput?.();
    }
  }

  /** Samples the current state for one local controller slot. */
  read(controller: ControllerId = 0): Input {
    const k = this.keys[controller];
    let mx = (k.right ? 1 : 0) - (k.left ? 1 : 0);
    let my = (k.down ? 1 : 0) - (k.up ? 1 : 0);
    let bomb = k.bomb;
    let action = k.action;

    if (controller === 0) {
      if (this.touch.active) {
        const mag = Math.hypot(this.touch.dx, this.touch.dy);
        if (mag > this.deadzone) {
          // Snap the stick to the dominant axis; diagonals are resolved by the
          // simulation but a clean axis feels far more precise on glass.
          if (Math.abs(this.touch.dx) > Math.abs(this.touch.dy) * 1.25) {
            mx = Math.sign(this.touch.dx);
            my = 0;
          } else if (Math.abs(this.touch.dy) > Math.abs(this.touch.dx) * 1.25) {
            my = Math.sign(this.touch.dy);
            mx = 0;
          } else {
            mx = Math.sign(this.touch.dx);
            my = Math.sign(this.touch.dy);
          }
        }
      }
      if (this.touch.bomb) bomb = true;
      if (this.touch.action) action = true;
    }

    const pad = this.readGamepad(controller);
    if (pad) {
      if (pad.mx !== 0) mx = pad.mx;
      if (pad.my !== 0) my = pad.my;
      if (pad.bomb) bomb = true;
      if (pad.action) action = true;
    }

    // Keyboard players holding two perpendicular keys keep their last axis.
    if (mx !== 0 && my !== 0 && !this.touch.active) {
      if (this.lastAxis[controller] === 'x') my = 0;
      else mx = 0;
    }

    return { mx, my, bomb, action };
  }

  private readGamepad(controller: number): Input | null {
    const pads = navigator.getGamepads?.() ?? [];
    const list = Array.from(pads).filter(Boolean) as Gamepad[];
    const pad = list[controller];
    if (!pad) return null;

    const ax = pad.axes[0] ?? 0;
    const ay = pad.axes[1] ?? 0;
    let mx = Math.abs(ax) > this.deadzone ? Math.sign(ax) : 0;
    let my = Math.abs(ay) > this.deadzone ? Math.sign(ay) : 0;
    if (pad.buttons[14]?.pressed) mx = -1;
    if (pad.buttons[15]?.pressed) mx = 1;
    if (pad.buttons[12]?.pressed) my = -1;
    if (pad.buttons[13]?.pressed) my = 1;

    const bomb = !!(pad.buttons[0]?.pressed || pad.buttons[7]?.pressed);
    const action = !!(pad.buttons[1]?.pressed || pad.buttons[2]?.pressed || pad.buttons[6]?.pressed);
    if (mx || my || bomb || action) {
      this.gamepadUsed = true;
      this.onAnyInput?.();
    }
    if (pad.buttons[9]?.pressed) this.onPause?.();
    return { mx, my, bomb, action };
  }

  get gamepadCount(): number {
    const pads = navigator.getGamepads?.() ?? [];
    return Array.from(pads).filter(Boolean).length;
  }
}

/**
 * Binds the on-screen thumbstick and action buttons. The stick is "floating":
 * it re-centres wherever the thumb lands, which matters a lot on small screens.
 */
/**
 * Binds the on-screen thumbstick and buttons.
 *
 * Returns a `reset` alongside the usual unbind. It is needed because the touch
 * layer gets hidden out from under a live gesture: the scoreboard hides it the
 * instant a match ends, and a player holding the stick at that moment never
 * gets the `pointerup` — so `stickId` stays set, `onStickStart` refuses every
 * later press as a duplicate, and the stick is dead for the rest of the
 * session. On a phone that means the next match cannot be played at all, which
 * is exactly what happens after a rematch.
 */
export function bindTouchControls(
  root: HTMLElement,
  input: InputManager,
): { reset: () => void; dispose: () => void } {
  const zone = root.querySelector<HTMLElement>('#stick-zone')!;
  const base = root.querySelector<HTMLElement>('#stick-base')!;
  const knob = root.querySelector<HTMLElement>('#stick-knob')!;
  const bombBtn = root.querySelector<HTMLElement>('#btn-bomb')!;
  const actionBtn = root.querySelector<HTMLElement>('#btn-action')!;

  const RADIUS = 52;
  const releases: Array<() => void> = [];
  let stickId: number | null = null;
  let originX = 0;
  let originY = 0;

  const setKnob = (dx: number, dy: number) => {
    knob.style.transform = `translate(${dx}px, ${dy}px)`;
  };

  const onStickStart = (e: PointerEvent) => {
    // A second finger while the first is genuinely still down is ignored; a
    // stale id left behind by a pointer that never lifted is not.
    if (stickId !== null && zone.hasPointerCapture(stickId)) return;
    stickId = e.pointerId;
    zone.setPointerCapture(e.pointerId);
    const rect = zone.getBoundingClientRect();
    originX = e.clientX;
    originY = e.clientY;
    base.style.left = `${e.clientX - rect.left - 64}px`;
    base.style.bottom = `${rect.bottom - e.clientY - 64}px`;
    base.classList.add('active');
    setKnob(0, 0);
    input.setTouch({ active: true, dx: 0, dy: 0 });
    e.preventDefault();
  };

  const onStickMove = (e: PointerEvent) => {
    if (e.pointerId !== stickId) return;
    let dx = e.clientX - originX;
    let dy = e.clientY - originY;
    const mag = Math.hypot(dx, dy);
    if (mag > RADIUS) {
      dx = (dx / mag) * RADIUS;
      dy = (dy / mag) * RADIUS;
    }
    setKnob(dx, dy);
    input.setTouch({ active: true, dx: dx / RADIUS, dy: dy / RADIUS });
    e.preventDefault();
  };

  const clearStick = () => {
    stickId = null;
    base.classList.remove('active');
    base.style.left = '';
    base.style.bottom = '';
    setKnob(0, 0);
    input.setTouch({ active: false, dx: 0, dy: 0 });
  };

  const onStickEnd = (e: PointerEvent) => {
    if (e.pointerId !== stickId) return;
    clearStick();
  };

  zone.addEventListener('pointerdown', onStickStart);
  zone.addEventListener('pointermove', onStickMove);
  zone.addEventListener('pointerup', onStickEnd);
  zone.addEventListener('pointercancel', onStickEnd);
  // Hiding the element mid-drag drops the capture without a `pointerup` on
  // some browsers. This is the event that always arrives.
  zone.addEventListener('lostpointercapture', onStickEnd);

  const bindButton = (el: HTMLElement, key: 'bomb' | 'action') => {
    const down = (e: PointerEvent) => {
      el.classList.add('pressed');
      el.setPointerCapture(e.pointerId);
      input.setTouch({ [key]: true } as Partial<TouchState>);
      buzz(key === 'bomb' ? 14 : 8);
      e.preventDefault();
    };
    const up = () => {
      el.classList.remove('pressed');
      input.setTouch({ [key]: false } as Partial<TouchState>);
    };
    releases.push(up);
    el.addEventListener('pointerdown', down);
    el.addEventListener('pointerup', up);
    el.addEventListener('pointercancel', up);
    el.addEventListener('pointerleave', up);
    return () => {
      el.removeEventListener('pointerdown', down);
      el.removeEventListener('pointerup', up);
      el.removeEventListener('pointercancel', up);
      el.removeEventListener('pointerleave', up);
    };
  };

  const unbindBomb = bindButton(bombBtn, 'bomb');
  const unbindAction = bindButton(actionBtn, 'action');

  return {
    /** Drops every held control. Call whenever the touch layer is shown. */
    reset() {
      clearStick();
      for (const release of releases) release();
    },
    dispose() {
      zone.removeEventListener('pointerdown', onStickStart);
      zone.removeEventListener('pointermove', onStickMove);
      zone.removeEventListener('pointerup', onStickEnd);
      zone.removeEventListener('pointercancel', onStickEnd);
      zone.removeEventListener('lostpointercapture', onStickEnd);
      unbindBomb();
      unbindAction();
    },
  };
}

import { describe, expect, it } from 'vitest';
import { clamp, difficulty, overlaps, validName, validScore } from './game-core.js';

describe('gameplay rules', () => {
  it('detects collisions without counting separated objects', () => {
    expect(overlaps({ x: 0, y: 0, w: 10, h: 10 }, { x: 9, y: 9, w: 4, h: 4 })).toBe(true);
    expect(overlaps({ x: 0, y: 0, w: 10, h: 10 }, { x: 10, y: 10, w: 4, h: 4 })).toBe(false);
  });

  it('clamps movement and escalates difficulty', () => {
    expect(clamp(-3, 0, 10)).toBe(0);
    expect(clamp(13, 0, 10)).toBe(10);
    expect(difficulty(4000, 90000)).toBeGreaterThan(difficulty(0, 0));
    expect(difficulty(999999, 999999)).toBe(3.2);
  });

  it('mirrors leaderboard validation', () => {
    expect(validName('ACE-7')).toBe(true);
    expect(validName('')).toBe(false);
    expect(validName('<script>')).toBe(false);
    expect(validName('abcdefghijklmnopq')).toBe(false);
    expect(validScore(0)).toBe(true);
    expect(validScore(10000001)).toBe(false);
    expect(validScore(1.5)).toBe(false);
  });
});

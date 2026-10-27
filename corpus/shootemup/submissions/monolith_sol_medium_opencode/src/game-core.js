export const WIDTH = 720;
export const HEIGHT = 900;

export function clamp(value, min, max) {
  return Math.max(min, Math.min(max, value));
}

export function overlaps(a, b) {
  return a.x < b.x + b.w && a.x + a.w > b.x && a.y < b.y + b.h && a.y + a.h > b.y;
}

export function difficulty(score, elapsed) {
  return Math.min(3.2, 1 + score / 3500 + elapsed / 80000);
}

export function validName(name) {
  return typeof name === 'string' && /^[A-Za-z0-9 _-]{1,16}$/.test(name.trim());
}

export function validScore(score) {
  return Number.isInteger(score) && score >= 0 && score <= 10000000;
}

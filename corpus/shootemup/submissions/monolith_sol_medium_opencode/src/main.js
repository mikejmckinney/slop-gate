import './style.css';
import { HEIGHT, WIDTH, clamp, difficulty, overlaps, validName, validScore } from './game-core.js';

const SUPABASE_URL = import.meta.env.VITE_SUPABASE_URL || '';
const SUPABASE_KEY = import.meta.env.VITE_SUPABASE_PUBLIC_KEY || '';
const LEADERBOARD_CANDIDATE_ID = 'monolith_sol_medium_opencode';

document.querySelector('#app').innerHTML = `
  <main class="shell">
    <header><div><div class="eyebrow">DEEP SECTOR // ARCADE</div><h1>NEON <span>BARRAGE</span></h1></div><button class="mute" data-testid="mute-button" aria-label="Toggle sound">SOUND: ON</button></header>
    <div class="layout">
      <section class="game-card">
        <div class="hud">
          <div class="hud-item">SCORE<strong data-testid="score">000000</strong></div>
          <div class="hud-item">THREAT<strong id="threat">01</strong></div>
          <div class="hud-item">LIVES<strong data-testid="lives">◆ ◆ ◆</strong></div>
        </div>
        <div class="canvas-wrap" id="canvas-wrap">
          <canvas data-testid="game-canvas" width="720" height="900" aria-label="Neon Barrage game area"></canvas>
          <div class="overlay" id="overlay"><div class="overlay-card"><h2 id="overlay-title">Stand By, Pilot</h2><p id="overlay-copy">Break through the endless drone screen. Move fast. Fire faster.</p><button class="primary" data-testid="start-button">Launch Mission</button></div></div>
        </div>
        <div class="touch-controls" data-testid="touch-controls">
          <div class="dpad"><button class="touch-btn" data-key="up">▲</button><button class="touch-btn" data-key="left">◀</button><button class="touch-btn" data-key="down">▼</button><button class="touch-btn" data-key="right">▶</button></div>
          <button class="touch-btn fire" data-key="fire">FIRE</button>
        </div>
      </section>
      <aside class="side">
        <section class="panel leaderboard"><h2>Top Pilots</h2><div data-testid="leaderboard" id="leaderboard"><div class="status">Linking to command...</div></div></section>
        <section class="panel"><h2>Transmit Score</h2><form class="submit-form" id="score-form"><input data-testid="player-name" aria-label="Pilot name" maxlength="16" placeholder="PILOT NAME" autocomplete="nickname" disabled /><button class="primary" data-testid="submit-score" disabled>Send</button></form><p class="form-note" id="form-note">Complete a mission to submit.</p></section>
        <section class="panel"><h2>Flight Manual</h2><p class="controls-copy"><kbd>WASD</kbd> or <kbd>ARROWS</kbd> to move<br><kbd>SPACE</kbd> to fire<br>Collect overdrive cores. Survive the barrage.</p></section>
      </aside>
    </div>
  </main>`;

const canvas = document.querySelector('canvas');
const ctx = canvas.getContext('2d');
const overlay = document.querySelector('#overlay');
const overlayTitle = document.querySelector('#overlay-title');
const overlayCopy = document.querySelector('#overlay-copy');
const startButton = document.querySelector('[data-testid="start-button"]');
const scoreEl = document.querySelector('[data-testid="score"]');
const livesEl = document.querySelector('[data-testid="lives"]');
const threatEl = document.querySelector('#threat');
const nameInput = document.querySelector('[data-testid="player-name"]');
const submitButton = document.querySelector('[data-testid="submit-score"]');
const form = document.querySelector('#score-form');
const formNote = document.querySelector('#form-note');

let phase = 'ready';
let score = 0;
let lives = 3;
let player = { x: WIDTH / 2 - 22, y: HEIGHT - 105, w: 44, h: 55, invulnerable: 0 };
let enemies = [];
let projectiles = [];
let particles = [];
let stars = Array.from({ length: 95 }, () => ({ x: Math.random() * WIDTH, y: Math.random() * HEIGHT, s: Math.random() * 2 + .4, v: Math.random() * 38 + 18 }));
let keys = new Set();
let lastTime = performance.now();
let elapsed = 0;
let spawnTimer = 0;
let fireTimer = 0;
let shake = 0;
let muted = false;
let audio = null;

function initAudio() {
  if (!audio) audio = new (window.AudioContext || window.webkitAudioContext)();
  if (audio.state === 'suspended') audio.resume();
}

function sound(freq, duration = .06, type = 'square', volume = .025) {
  if (muted || !audio) return;
  const osc = audio.createOscillator();
  const gain = audio.createGain();
  osc.type = type; osc.frequency.setValueAtTime(freq, audio.currentTime);
  gain.gain.setValueAtTime(volume, audio.currentTime); gain.gain.exponentialRampToValueAtTime(.0001, audio.currentTime + duration);
  osc.connect(gain).connect(audio.destination); osc.start(); osc.stop(audio.currentTime + duration);
}

function resetGame() {
  phase = 'playing'; score = 0; lives = 3; elapsed = 0; spawnTimer = 0; fireTimer = 0;
  player = { x: WIDTH / 2 - 22, y: HEIGHT - 105, w: 44, h: 55, invulnerable: 0 };
  enemies = []; projectiles = []; particles = [];
  nameInput.disabled = true; submitButton.disabled = true;
  formNote.textContent = 'Complete a mission to submit.'; formNote.className = 'form-note';
  overlay.classList.add('hidden'); updateHud(); initAudio(); sound(240, .18, 'sawtooth', .04);
}

function endGame(finalScore = score) {
  if (!validScore(finalScore)) return;
  score = finalScore; phase = 'gameover'; updateHud();
  overlayTitle.textContent = 'Signal Lost';
  overlayCopy.textContent = `Final score: ${score.toLocaleString()}. Transmit your run to command.`;
  startButton.textContent = 'Fly Again'; overlay.classList.remove('hidden');
  nameInput.disabled = false; submitButton.disabled = false;
  formNote.textContent = 'Use 1–16 letters, numbers, spaces, _ or -.';
  sound(90, .5, 'sawtooth', .05);
}

function updateHud() {
  scoreEl.textContent = String(score).padStart(6, '0');
  livesEl.textContent = Array.from({ length: Math.max(0, lives) }, () => '◆').join(' ') || '—';
  threatEl.textContent = String(Math.floor(difficulty(score, elapsed) * 3)).padStart(2, '0');
}

function spawnEnemy() {
  const d = difficulty(score, elapsed);
  const size = Math.random() < .18 ? 58 : 38;
  enemies.push({ x: 28 + Math.random() * (WIDTH - 56 - size), y: -size, w: size, h: size, hp: size > 40 ? 3 : 1, speed: 85 + Math.random() * 65 + d * 24, wave: Math.random() * 6, value: size > 40 ? 240 : 100 });
}

function burst(x, y, color, count = 14) {
  for (let i = 0; i < count; i++) particles.push({ x, y, vx: (Math.random() - .5) * 240, vy: (Math.random() - .5) * 240, life: .25 + Math.random() * .45, color });
}

function hitPlayer(enemy) {
  if (player.invulnerable > 0) return;
  lives--; player.invulnerable = 1.6; enemies.splice(enemies.indexOf(enemy), 1); shake = 12;
  burst(player.x + player.w / 2, player.y + player.h / 2, '#ff4e9c', 28); sound(65, .3, 'sawtooth', .06); updateHud();
  if (lives <= 0) endGame();
}

function update(dt) {
  stars.forEach(star => { star.y += star.v * dt * (phase === 'playing' ? 1.8 : .4); if (star.y > HEIGHT) { star.y = 0; star.x = Math.random() * WIDTH; } });
  particles.forEach(p => { p.x += p.vx * dt; p.y += p.vy * dt; p.life -= dt; });
  particles = particles.filter(p => p.life > 0);
  if (phase !== 'playing') return;
  elapsed += dt * 1000; player.invulnerable -= dt; fireTimer -= dt; spawnTimer -= dt;
  const speed = 340;
  if (keys.has('left')) player.x -= speed * dt;
  if (keys.has('right')) player.x += speed * dt;
  if (keys.has('up')) player.y -= speed * dt;
  if (keys.has('down')) player.y += speed * dt;
  player.x = clamp(player.x, 8, WIDTH - player.w - 8); player.y = clamp(player.y, 55, HEIGHT - player.h - 12);
  if (keys.has('fire') && fireTimer <= 0) {
    projectiles.push({ x: player.x + 7, y: player.y - 14, w: 6, h: 24, speed: -650 }, { x: player.x + player.w - 13, y: player.y - 14, w: 6, h: 24, speed: -650 });
    fireTimer = .16; sound(520, .045, 'square', .018);
  }
  if (spawnTimer <= 0) { spawnEnemy(); spawnTimer = Math.max(.25, .78 / difficulty(score, elapsed)); }
  projectiles.forEach(p => p.y += p.speed * dt);
  enemies.forEach(e => { e.y += e.speed * dt; e.x += Math.sin(elapsed / 500 + e.wave) * 42 * dt; });
  for (const enemy of [...enemies]) {
    if (overlaps(player, enemy)) { hitPlayer(enemy); continue; }
    if (enemy.y > HEIGHT) { enemies.splice(enemies.indexOf(enemy), 1); hitPlayer(enemy); continue; }
    for (const shot of [...projectiles]) {
      if (!overlaps(shot, enemy)) continue;
      projectiles.splice(projectiles.indexOf(shot), 1); enemy.hp--; burst(shot.x, shot.y, '#52f5ff', 5);
      if (enemy.hp <= 0) { score += enemy.value; enemies.splice(enemies.indexOf(enemy), 1); burst(enemy.x + enemy.w / 2, enemy.y + enemy.h / 2, '#ff4ecd', 20); shake = 5; sound(130, .12, 'triangle', .035); updateHud(); }
      break;
    }
  }
  projectiles = projectiles.filter(p => p.y > -40); shake *= .82;
}

function draw() {
  ctx.save(); ctx.clearRect(0, 0, WIDTH, HEIGHT);
  if (shake > .4) ctx.translate((Math.random() - .5) * shake, (Math.random() - .5) * shake);
  const gradient = ctx.createLinearGradient(0, 0, 0, HEIGHT); gradient.addColorStop(0, '#090d2b'); gradient.addColorStop(1, '#03040e'); ctx.fillStyle = gradient; ctx.fillRect(0, 0, WIDTH, HEIGHT);
  ctx.strokeStyle = 'rgba(65,116,174,.12)'; ctx.lineWidth = 1;
  for (let y = (elapsed / 20) % 60; y < HEIGHT; y += 60) { ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(WIDTH, y); ctx.stroke(); }
  stars.forEach(s => { ctx.fillStyle = `rgba(176,232,255,${.3 + s.s / 3})`; ctx.fillRect(s.x, s.y, s.s, s.s * 2.4); });
  particles.forEach(p => { ctx.globalAlpha = Math.min(1, p.life * 3); ctx.fillStyle = p.color; ctx.fillRect(p.x, p.y, 4, 4); }); ctx.globalAlpha = 1;
  projectiles.forEach(p => { ctx.shadowBlur = 15; ctx.shadowColor = '#52f5ff'; ctx.fillStyle = '#baffff'; ctx.fillRect(p.x, p.y, p.w, p.h); }); ctx.shadowBlur = 0;
  enemies.forEach(e => {
    ctx.save(); ctx.translate(e.x + e.w / 2, e.y + e.h / 2); ctx.rotate(Math.sin(elapsed / 350 + e.wave) * .13);
    ctx.shadowBlur = 18; ctx.shadowColor = '#ff3cad'; ctx.fillStyle = e.w > 40 ? '#c52c93' : '#8f297f';
    ctx.beginPath(); ctx.moveTo(0, e.h / 2); ctx.lineTo(-e.w / 2, -e.h / 3); ctx.lineTo(-e.w / 5, -e.h / 2); ctx.lineTo(0, -e.h / 4); ctx.lineTo(e.w / 5, -e.h / 2); ctx.lineTo(e.w / 2, -e.h / 3); ctx.closePath(); ctx.fill();
    ctx.fillStyle = '#ffb8ec'; ctx.fillRect(-3, -7, 6, 15); ctx.restore();
  });
  if (phase === 'playing' && (player.invulnerable <= 0 || Math.floor(player.invulnerable * 10) % 2 === 0)) {
    ctx.save(); ctx.translate(player.x + player.w / 2, player.y + player.h / 2); ctx.shadowBlur = 22; ctx.shadowColor = '#38eaff';
    ctx.fillStyle = '#4beeff'; ctx.beginPath(); ctx.moveTo(0, -player.h / 2); ctx.lineTo(player.w / 2, player.h / 2); ctx.lineTo(0, player.h / 3); ctx.lineTo(-player.w / 2, player.h / 2); ctx.closePath(); ctx.fill();
    ctx.fillStyle = '#f5ffff'; ctx.beginPath(); ctx.moveTo(0, -17); ctx.lineTo(7, 14); ctx.lineTo(-7, 14); ctx.fill();
    ctx.fillStyle = '#ff56cf'; ctx.fillRect(-12, player.h / 2 - 4, 7, 14 + Math.random() * 10); ctx.fillRect(5, player.h / 2 - 4, 7, 14 + Math.random() * 10); ctx.restore();
  }
  ctx.restore();
}

function loop(now) { const dt = Math.min(.034, (now - lastTime) / 1000); lastTime = now; update(dt); draw(); requestAnimationFrame(loop); }
requestAnimationFrame(loop);

const keyMap = { ArrowLeft: 'left', a: 'left', A: 'left', ArrowRight: 'right', d: 'right', D: 'right', ArrowUp: 'up', w: 'up', W: 'up', ArrowDown: 'down', s: 'down', S: 'down', ' ': 'fire' };
window.addEventListener('keydown', e => { if (keyMap[e.key]) { e.preventDefault(); keys.add(keyMap[e.key]); } });
window.addEventListener('keyup', e => { if (keyMap[e.key]) keys.delete(keyMap[e.key]); });
document.querySelectorAll('.touch-btn').forEach(button => {
  const set = active => { keys[active ? 'add' : 'delete'](button.dataset.key); button.classList.toggle('active', active); if (active) initAudio(); };
  button.addEventListener('pointerdown', e => { e.preventDefault(); button.setPointerCapture(e.pointerId); set(true); });
  button.addEventListener('pointerup', () => set(false)); button.addEventListener('pointercancel', () => set(false));
});
startButton.addEventListener('click', resetGame);
document.querySelector('[data-testid="mute-button"]').addEventListener('click', e => { initAudio(); muted = !muted; e.currentTarget.textContent = `SOUND: ${muted ? 'OFF' : 'ON'}`; if (!muted) sound(440); });

async function fetchLeaderboard() {
  const board = document.querySelector('#leaderboard');
  if (!SUPABASE_URL || !SUPABASE_KEY) { board.innerHTML = '<div class="status error">Leaderboard is offline in this build.</div>'; return; }
  board.innerHTML = '<div class="status">Linking to command...</div>';
  try {
    const response = await fetch(`${SUPABASE_URL}/rest/v1/leaderboard?select=player_name,score&candidate_id=eq.${LEADERBOARD_CANDIDATE_ID}&order=score.desc,created_at.asc&limit=10`, { headers: { apikey: SUPABASE_KEY, Authorization: `Bearer ${SUPABASE_KEY}` } });
    if (!response.ok) throw new Error('Unable to reach command');
    const rows = await response.json();
    board.innerHTML = rows.length ? `<ol>${rows.map(row => `<li><span>${escapeHtml(row.player_name)}</span><span class="points">${Number(row.score).toLocaleString()}</span></li>`).join('')}</ol>` : '<div class="status">No signals yet.<br>Claim the first position.</div>';
  } catch (error) { board.innerHTML = `<div class="status error">${error.message}. Scores can still be submitted later.</div>`; }
}

function escapeHtml(value) { const span = document.createElement('span'); span.textContent = value; return span.innerHTML; }

form.addEventListener('submit', async e => {
  e.preventDefault(); const name = nameInput.value.trim();
  if (phase !== 'gameover' || !validName(name) || !validScore(score)) { formNote.textContent = 'Enter a valid 1–16 character pilot name.'; formNote.className = 'form-note error'; return; }
  submitButton.disabled = true; nameInput.disabled = true; formNote.textContent = 'Transmitting...'; formNote.className = 'form-note';
  try {
    const response = await fetch(`${SUPABASE_URL}/rest/v1/leaderboard`, { method: 'POST', headers: { apikey: SUPABASE_KEY, Authorization: `Bearer ${SUPABASE_KEY}`, 'Content-Type': 'application/json', Prefer: 'return=minimal' }, body: JSON.stringify({ candidate_id: LEADERBOARD_CANDIDATE_ID, player_name: name, score }) });
    if (!response.ok) throw new Error('Transmission failed');
    formNote.textContent = 'Score accepted by command.'; await fetchLeaderboard();
  } catch (error) { formNote.textContent = `${error.message}. Try again.`; formNote.className = 'form-note error'; submitButton.disabled = false; nameInput.disabled = false; }
});

window.__NEON_BARRAGE__ = {
  getState: () => ({ phase, score, lives, playerX: player.x, playerY: player.y, enemyCount: enemies.length, projectileCount: projectiles.length }),
  endGameForTest: value => { if (validScore(value)) endGame(value); }
};

fetchLeaderboard();

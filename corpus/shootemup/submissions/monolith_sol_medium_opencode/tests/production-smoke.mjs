import { chromium } from 'playwright';

const url = process.env.PRODUCTION_URL || 'http://localhost:5173';
const browser = await chromium.launch({ headless: true });

try {
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
  const response = await page.goto(url, { waitUntil: 'networkidle', timeout: 30000 });
  if (!response?.ok()) throw new Error(`Production returned HTTP ${response?.status()}`);

  const selectors = [
    '[data-testid="game-canvas"]', '[data-testid="start-button"]', '[data-testid="score"]',
    '[data-testid="lives"]', '[data-testid="mute-button"]', '[data-testid="leaderboard"]',
    '[data-testid="player-name"]', '[data-testid="submit-score"]', '[data-testid="touch-controls"]'
  ];
  for (const selector of selectors) await page.locator(selector).waitFor({ state: 'attached' });

  await page.getByTestId('start-button').click();
  await page.keyboard.down('Space');
  await page.waitForTimeout(250);
  await page.keyboard.up('Space');
  const playing = await page.evaluate(() => window.__NEON_BARRAGE__.getState());
  if (playing.phase !== 'playing' || playing.projectileCount < 1) throw new Error('Gameplay controls did not update state');

  await page.evaluate(() => window.__NEON_BARRAGE__.endGameForTest(515151));
  await page.getByTestId('player-name').fill('LIVE SMOKE');
  await page.getByTestId('submit-score').click();
  await page.getByText('Score accepted by command.').waitFor({ timeout: 15000 });
  await page.getByTestId('leaderboard').getByText('LIVE SMOKE').waitFor();

  const mobile = await browser.newPage({ viewport: { width: 390, height: 844 } });
  await mobile.goto(url, { waitUntil: 'domcontentloaded' });
  if (!(await mobile.getByTestId('touch-controls').isVisible())) throw new Error('Touch controls are not visible on mobile');
  await mobile.close();

  console.log(JSON.stringify({ httpStatus: response.status(), selectors: selectors.length, gameplay: true, submission: true, mobile: true }));
} finally {
  await browser.close();
}

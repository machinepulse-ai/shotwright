const { chromium } = require('playwright');
const path = require('path');
(async () => {
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 1728, height: 1117 }, deviceScaleFactor: 1 });
  await page.goto('http://127.0.0.1:3000', { waitUntil: 'networkidle' });
  await page.screenshot({ path: path.resolve('validation-data/output/current-ui.png'), fullPage: true });
  await browser.close();
})();

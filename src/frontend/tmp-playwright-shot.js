const { chromium } = require('playwright');
const path = require('path');
(async () => {
  const browser = await chromium.launch({
    headless: true,
    executablePath: 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'
  });
  const page = await browser.newPage({ viewport: { width: 1728, height: 1117 }, deviceScaleFactor: 1 });
  await page.goto('http://127.0.0.1:3100', { waitUntil: 'networkidle' });
  await page.screenshot({ path: path.resolve('..', '..', 'validation-data', 'output', 'current-ui-vscode-light.png'), fullPage: true });
  await browser.close();
  console.log('screenshot saved');
})();

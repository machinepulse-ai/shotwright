const path = require("node:path");

const { captureScreenshot, openWorkbench } = require("./playwright_shared");

(async () => {
  let browser;

  try {
    const targetPath = process.env.SHOTWRIGHT_CAPTURE_PATH || path.join("tests", "artifacts", "ui-capture.png");
    const session = await openWorkbench();
    browser = session.browser;

    const savedPath = await captureScreenshot(session.page, targetPath);
    console.log(`Captured ${session.baseUrl} -> ${savedPath}`);
  } catch (error) {
    console.error(error?.stack || error);
    process.exitCode = 1;
  } finally {
    if (browser) {
      await browser.close();
    }
  }
})();
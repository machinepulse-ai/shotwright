const path = require("node:path");

const { captureScreenshot, openWorkbench } = require("./playwright_shared");

function getSessionId() {
  const fromArg = process.argv[2];
  const fromEnv = process.env.SHOTWRIGHT_SESSION_ID;
  const value = fromArg || fromEnv;
  if (!value) {
    throw new Error("Missing session id. Pass it as the first argument or set SHOTWRIGHT_SESSION_ID.");
  }

  return value;
}

async function collectLayoutReport(page) {
  return page.evaluate(() => {
    const rect = (element) => {
      if (!element) {
        return null;
      }

      const box = element.getBoundingClientRect();
      return {
        x: box.x,
        y: box.y,
        width: box.width,
        height: box.height,
        top: box.top,
        right: box.right,
        bottom: box.bottom,
        left: box.left,
      };
    };

    const sidebar = document.querySelector('[data-testid="session-context-sidebar"]');
    const transcript = document.querySelector('.chat-transcript');
    const panels = sidebar ? Array.from(sidebar.querySelectorAll('.context-panel, .container-manager, .video-player')) : [];
    const overflowSelectors = [
      '.context-panel',
      '.session-settings-block',
      '.inline-alert',
      '.session-runtime-meta',
      '.container-item',
      '.container-image',
      '.container-fact',
      '.video-player',
      '.video-player-meta',
      '.project-item',
      '.project-meta',
      '.project-submeta',
      '.timeline-detail-grid',
      '.timeline-detail-block',
      '.timeline-event-data',
    ].join(', ');

    const overflowOffenders = sidebar
      ? Array.from(sidebar.querySelectorAll(overflowSelectors))
          .map((element, index) => ({
            index,
            className: element.className || element.tagName.toLowerCase(),
            clientWidth: element.clientWidth,
            scrollWidth: element.scrollWidth,
            text: (element.textContent || '').trim().replace(/\s+/g, ' ').slice(0, 180),
          }))
          .filter((entry) => entry.scrollWidth - entry.clientWidth > 4)
      : [];

    return {
      title: document.title,
      location: window.location.href,
      workbenchRect: rect(document.querySelector('.workbench')),
      transcriptRect: rect(transcript),
      contextSidebarRect: rect(sidebar),
      contextPanelTitles: panels
        .map((panel) => panel.querySelector('.panel-heading h3')?.textContent?.trim() || null)
        .filter(Boolean),
      timelinePanelExists: Boolean(sidebar?.querySelector('.timeline-panel')),
      timelineEntries: document.querySelectorAll('[data-testid="timeline-entry"]').length,
      inlineExecutionBlocks: document.querySelectorAll('[data-testid="conversation-execution-block"]').length,
      containerCards: sidebar?.querySelectorAll('.container-item').length || 0,
      videoPlayerPresent: Boolean(sidebar?.querySelector('.video-player')),
      overflowOffenders,
    };
  });
}

async function main() {
  const sessionId = getSessionId();
  const screenshotRelativePath =
    process.env.SHOTWRIGHT_LAYOUT_SCREENSHOT || path.join('tests', 'artifacts', `live-session-${sessionId}.png`);
  const waitMs = Number(process.env.SHOTWRIGHT_LAYOUT_WAIT_MS || 2500);

  const { browser, page, baseUrl } = await openWorkbench({
    path: `/sessions/${sessionId}`,
    waitUntil: 'domcontentloaded',
    gotoTimeout: 45000,
    readySelector: '.workbench',
    readyTimeout: 45000,
  });

  try {
    await page.waitForTimeout(waitMs);
    await page.waitForSelector('[data-testid="chat-stage"]', { timeout: 45000 });

    const report = await collectLayoutReport(page);
    const screenshotPath = await captureScreenshot(page, screenshotRelativePath, { fullPage: true });

    console.log(
      JSON.stringify(
        {
          baseUrl,
          sessionId,
          screenshotPath,
          report,
        },
        null,
        2
      )
    );
  } finally {
    await browser.close();
  }
}

main().catch((error) => {
  console.error(error?.stack || error);
  process.exit(1);
});
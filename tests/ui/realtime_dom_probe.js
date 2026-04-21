const fs = require("node:fs");
const { chromium } = require(require.resolve("playwright", { paths: ["C:/code/shotwright/src/frontend"] }));
const { captureScreenshot, resolveBaseUrl } = require("./playwright_shared");

const EDGE_CANDIDATES = [
  "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
  "C:/Program Files/Microsoft/Edge/Application/msedge.exe",
];

function requiredEnv(name) {
  const value = process.env[name];
  if (!value) {
    throw new Error(`Missing required environment variable: ${name}`);
  }
  return value;
}

async function collectSnapshot(page, tag) {
  return page.evaluate((label) => ({
    tag: label,
    timestamp: new Date().toISOString(),
    transcriptText: document.querySelector(".chat-transcript")?.textContent || "",
    timelineCount: document.querySelectorAll('[data-testid="timeline-entry"]').length,
    timelineFirstText: document.querySelector('[data-testid="timeline-entry"]')?.textContent || "",
    headerMeta: document.querySelector(".chat-stage-meta")?.textContent || "",
    sessionCards: Array.from(document.querySelectorAll('[data-testid="session-list-item"]')).map((item) => ({
      active: item.classList.contains("active"),
      text: item.textContent || "",
    })),
    eventSourceLogs: window.__shotwrightEventSourceLogs || [],
  }), tag);
}

async function main() {
  const sessionId = requiredEnv("SHOTWRIGHT_REALTIME_SESSION_ID");
  const prompt = process.env.SHOTWRIGHT_REALTIME_PROMPT || "Reply with LIVE-OK only.";
  const expectedReply = process.env.SHOTWRIGHT_REALTIME_EXPECTED_REPLY || "LIVE-OK";
  const apiBaseUrl = process.env.SHOTWRIGHT_API_BASE_URL || "http://127.0.0.1:8000";

  const baseUrl = process.env.SHOTWRIGHT_BASE_URL || (await resolveBaseUrl());
  const edgePath = EDGE_CANDIDATES.find((candidate) => fs.existsSync(candidate));
  const browser = await chromium.launch(edgePath ? { headless: true, executablePath: edgePath } : { headless: true });
  const page = await browser.newPage({ viewport: { width: 1728, height: 1117 }, deviceScaleFactor: 1 });

  await page.addInitScript(() => {
    window.__shotwrightEventSourceLogs = [];

    const NativeEventSource = window.EventSource;
    if (!NativeEventSource) {
      return;
    }

    window.EventSource = class WrappedEventSource extends NativeEventSource {
      constructor(url, configuration) {
        super(url, configuration);
        this.__shotwrightUrl = typeof url === "string" ? url : String(url);
        window.__shotwrightEventSourceLogs.push({
          kind: "create",
          url: this.__shotwrightUrl,
          readyState: this.readyState,
          timestamp: Date.now(),
        });
        super.addEventListener("open", () => {
          window.__shotwrightEventSourceLogs.push({
            kind: "open",
            url: this.__shotwrightUrl,
            readyState: this.readyState,
            timestamp: Date.now(),
          });
        });
        super.addEventListener("error", () => {
          window.__shotwrightEventSourceLogs.push({
            kind: "error",
            url: this.__shotwrightUrl,
            readyState: this.readyState,
            timestamp: Date.now(),
          });
        });
      }

      addEventListener(type, listener, options) {
        const wrappedListener = (event) => {
          if (type !== "open" && type !== "error") {
            window.__shotwrightEventSourceLogs.push({
              kind: "event",
              type,
              url: this.__shotwrightUrl,
              data: typeof event.data === "string" ? event.data.slice(0, 240) : null,
              timestamp: Date.now(),
            });
          }
          return listener.call(this, event);
        };

        return super.addEventListener(type, wrappedListener, options);
      }
    };
  });

  try {
    await page.goto(`${baseUrl}/sessions/${sessionId}`, { waitUntil: "domcontentloaded", timeout: 45000 });
    await page.waitForSelector(".workbench", { timeout: 45000 });
    const initial = await collectSnapshot(page, "initial");

    const postPromise = fetch(`${apiBaseUrl}/api/agent/sessions/${sessionId}/messages`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ content: prompt }),
    }).then(async (response) => ({
      status: response.status,
      body: await response.text(),
    }));

    let sawUserPrompt = true;
    try {
      await page.waitForFunction((text) => document.body.textContent.includes(text), prompt, { timeout: 15000 });
    } catch {
      sawUserPrompt = false;
    }

    const afterUser = await collectSnapshot(page, "after-user");

    let sawAssistantReply = true;
    try {
      await page.waitForFunction((text) => document.body.textContent.includes(text), expectedReply, { timeout: 90000 });
    } catch {
      sawAssistantReply = false;
    }

    const final = await collectSnapshot(page, "final");
    const postResult = await postPromise;

    const screenshotPath = await captureScreenshot(page, "tests/artifacts/realtime-dom-probe.png", { fullPage: true });

    const report = {
      baseUrl,
      apiBaseUrl,
      sessionId,
      prompt,
      expectedReply,
      sawUserPrompt,
      sawAssistantReply,
      initial,
      afterUser,
      final,
      postResult,
      screenshotPath,
    };

    console.log(JSON.stringify(report, null, 2));
  } finally {
    await browser.close();
  }
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
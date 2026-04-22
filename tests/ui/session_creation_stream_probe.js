const fs = require("node:fs");
const path = require("node:path");

const { captureScreenshot, openWorkbench } = require("./playwright_shared");

function extractSessionIdFromUrl(url) {
  const match = /\/sessions\/([^/?#]+)/.exec(url);
  return match ? match[1] : null;
}

async function patchSessionName(apiBaseUrl, sessionId, name) {
  const response = await fetch(`${apiBaseUrl}/api/sessions/${sessionId}`, {
    method: "PATCH",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ name }),
  });

  return {
    ok: response.ok,
    status: response.status,
    body: await response.text(),
  };
}

async function collectProbeState(page, label) {
  return page.evaluate((tag) => ({
    tag,
    timestamp: new Date().toISOString(),
    href: window.location.href,
    title: document.querySelector(".chat-stage h1")?.textContent || null,
    activeSessionText:
      document.querySelector('[data-testid="session-list-item"].active')?.textContent || null,
    fetchLogs: window.__shotwrightFetchLogs || [],
    eventSourceLogs: window.__shotwrightEventSourceLogs || [],
  }), label);
}

async function main() {
  const apiBaseUrl = process.env.SHOTWRIGHT_API_BASE_URL || "http://127.0.0.1:8000";
  const renamePrefix = process.env.SHOTWRIGHT_STREAM_PROBE_RENAME_PREFIX || "PW Stream Probe";

  const { browser, page, baseUrl } = await openWorkbench({
    path: "/",
    waitUntil: "domcontentloaded",
    readySelector: ".workbench",
    gotoTimeout: 45000,
    readyTimeout: 45000,
    beforeGoto: async (playwrightPage) => {
      await playwrightPage.addInitScript(() => {
        window.__shotwrightFetchLogs = [];
        window.__shotwrightEventSourceLogs = [];
        const nativeFetch = window.fetch.bind(window);
        const NativeEventSource = window.EventSource;

        window.fetch = async (...args) => {
          const [input, init] = args;
          const url = typeof input === "string" ? input : input instanceof Request ? input.url : String(input);
          const method = init?.method || (input instanceof Request ? input.method : "GET");
          const startedAt = Date.now();

          window.__shotwrightFetchLogs.push({
            phase: "request",
            url,
            method,
            timestamp: startedAt,
          });

          try {
            const response = await nativeFetch(...args);
            window.__shotwrightFetchLogs.push({
              phase: "response",
              url,
              method,
              status: response.status,
              ok: response.ok,
              timestamp: Date.now(),
              durationMs: Date.now() - startedAt,
            });
            return response;
          } catch (error) {
            window.__shotwrightFetchLogs.push({
              phase: "error",
              url,
              method,
              error: error instanceof Error ? error.message : String(error),
              timestamp: Date.now(),
              durationMs: Date.now() - startedAt,
            });
            throw error;
          }
        };

        if (NativeEventSource) {
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
        }
      });
    },
  });

  try {
    const createSessionResponsePromise = page.waitForResponse(
      (response) => response.request().method() === "POST" && response.url().includes("/api/sessions"),
      { timeout: 30000 },
    );

    const sidebarCreateButton = page.locator(".sidebar-new-button").first();
    const emptyStateCreateButton = page.locator(".chat-welcome.empty .btn-primary").first();
    if (await sidebarCreateButton.count()) {
      await sidebarCreateButton.click();
    } else {
      await emptyStateCreateButton.click();
    }
    const createSessionResponse = await createSessionResponsePromise;
    const createSessionPayload = await createSessionResponse.json();
    const createdSessionId = createSessionPayload?._id || null;
    if (!createdSessionId) {
      throw new Error(`Could not extract session id from create-session response: ${JSON.stringify(createSessionPayload)}`);
    }

    await page.waitForURL(new RegExp(`/sessions/${createdSessionId}$`), { timeout: 30000 });

    let streamConnected = true;
    try {
      await page.waitForFunction(
        (sessionId) => {
          const fetchConnected = (window.__shotwrightFetchLogs || []).some(
            (entry) =>
              entry.phase === "response" &&
              typeof entry.url === "string" &&
              entry.url.includes(`/api/agent/sessions/${sessionId}/stream`) &&
              entry.status === 200,
          );
          const eventSourceConnected = (window.__shotwrightEventSourceLogs || []).some(
            (entry) =>
              entry.kind === "open" &&
              typeof entry.url === "string" &&
              entry.url.includes(`/api/agent/sessions/${sessionId}/stream`),
          );
          return fetchConnected || eventSourceConnected;
        },
        createdSessionId,
        { timeout: 30000 },
      );
    } catch {
      streamConnected = false;
    }

    const beforeRename = await collectProbeState(page, "before-rename");
    const renamedSession = `${renamePrefix} ${Date.now()}`;
    const patchResult = await patchSessionName(apiBaseUrl, createdSessionId, renamedSession);

    let updatedWithoutReload = true;
    try {
      await page.waitForFunction(
        (expectedName) => document.querySelector(".chat-stage h1")?.textContent?.includes(expectedName),
        renamedSession,
        { timeout: 15000 },
      );
    } catch {
      updatedWithoutReload = false;
    }

    const afterRename = await collectProbeState(page, "after-rename");

    let updatedAfterReload = updatedWithoutReload;
    let updatedViaStreamAfterReload = updatedWithoutReload;
    if (!updatedWithoutReload) {
      await page.reload({ waitUntil: "domcontentloaded", timeout: 45000 });
      await page.waitForSelector(".workbench", { timeout: 45000 });
      try {
        await page.waitForFunction(
          (expectedName) => document.querySelector(".chat-stage h1")?.textContent?.includes(expectedName),
          renamedSession,
          { timeout: 15000 },
        );
        updatedAfterReload = true;
      } catch {
        updatedAfterReload = false;
      }

      if (updatedAfterReload) {
        const renamedAfterReload = `${renamePrefix} reload ${Date.now()}`;
        const secondPatchResult = await patchSessionName(apiBaseUrl, createdSessionId, renamedAfterReload);
        try {
          await page.waitForFunction(
            (expectedName) => document.querySelector(".chat-stage h1")?.textContent?.includes(expectedName),
            renamedAfterReload,
            { timeout: 15000 },
          );
          updatedViaStreamAfterReload = true;
        } catch {
          updatedViaStreamAfterReload = false;
        }

        beforeRename.secondPatchResult = secondPatchResult;
        beforeRename.renamedAfterReload = renamedAfterReload;
      }
    }

    const finalState = await collectProbeState(page, "final");
    const screenshotPath = await captureScreenshot(page, "tests/artifacts/session-creation-stream-probe.png", {
      fullPage: true,
    });

    const report = {
      baseUrl,
      apiBaseUrl,
      createdSessionId,
      createSessionStatus: createSessionResponse.status(),
      streamConnected,
      patchResult,
      updatedWithoutReload,
      updatedAfterReload,
      updatedViaStreamAfterReload,
      beforeRename,
      afterRename,
      finalState,
      screenshotPath,
    };

    console.log(JSON.stringify(report, null, 2));

    if (!updatedWithoutReload) {
      process.exitCode = 2;
    }
  } finally {
    await browser.close();
  }
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
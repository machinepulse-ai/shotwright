const assert = require("node:assert/strict");
const path = require("node:path");

const { captureRequested, captureScreenshot, openWorkbench } = require("./playwright_shared");

const sessionId = "session-ui-regression";
const now = new Date().toISOString();
const modelOptions = [
  {
    id: "gpt-5.4",
    name: "GPT 5.4",
    supports_reasoning_effort: true,
    supported_reasoning_efforts: ["low", "medium", "high", "xhigh"],
    default_reasoning_effort: "high",
  },
  {
    id: "gpt-4.1",
    name: "GPT-4.1",
    supports_reasoning_effort: false,
    supported_reasoning_efforts: [],
    default_reasoning_effort: null,
  },
];

let sessionState = {
  _id: sessionId,
  name: "Session Sidebar Regression",
  status: "error",
  copilot_model: "gpt-5.4",
  copilot_reasoning_effort: "high",
  copilot_session_id: "shotwright-session-ui-regression-with-a-very-long-runtime-identifier-for-layout-checks",
  container_id: null,
  active_project_id: null,
  latest_render_path: "C:/data/output/render/session-ui-regression/result-with-a-deliberately-long-file-name.mp4",
  latest_stream_url: null,
  last_error:
    "Copilot runtime retry failed while reconnecting to a long-running render session, so the sidebar must wrap this message instead of overflowing horizontally.",
  created_at: now,
  updated_at: now,
};

const messages = [
  {
    _id: "msg-user-1",
    session_id: sessionId,
    role: "user",
    content: "Inspect the current project and prepare a preview render with a 1920x1080 export.",
    created_at: now,
    metadata: {},
  },
  {
    _id: "msg-assistant-1",
    session_id: sessionId,
    role: "assistant",
    content: [
      "**Render ready** once the runtime reconnects.",
      "",
      "- Output: `result.mp4`",
      "- Next step: verify the timing on the title card",
      "",
      "```jsx",
      "app.project.activeItem.name;",
      "```",
    ].join("\n"),
    created_at: now,
    metadata: {},
  },
];

const events = [
  {
    _id: "evt-1",
    session_id: sessionId,
    type: "session.bootstrap.completed",
    summary:
      "Initialized a session with a long event summary to verify that the right sidebar timeline wraps within the available column width.",
    created_at: now,
    data: {},
  },
  {
    _id: "evt-2",
    session_id: sessionId,
    type: "session.model.updated",
    summary:
      "Applied a session-specific model preference and kept the reasoning effort selector aligned with the selected model capabilities.",
    created_at: now,
    data: {},
  },
];

function json(route, body, status = 200) {
  return route.fulfill({
    status,
    contentType: "application/json",
    body: JSON.stringify(body),
  });
}

async function installMockRoutes(page) {
  await page.addInitScript(() => {
    window.localStorage.setItem("shotwright_locale", "en-US");
  });

  await page.route("**/api/sessions/model-options", (route) => json(route, modelOptions));
  await page.route("**/api/sessions", async (route) => {
    const method = route.request().method();
    if (method === "GET") {
      return json(route, [sessionState]);
    }

    return route.continue();
  });
  await page.route(`**/api/sessions/${sessionId}`, async (route) => {
    const method = route.request().method();
    if (method !== "PATCH") {
      return route.continue();
    }

    const payload = JSON.parse(route.request().postData() || "{}");
    sessionState = {
      ...sessionState,
      ...payload,
      updated_at: new Date().toISOString(),
    };

    return json(route, sessionState);
  });
  await page.route(`**/api/agent/sessions/${sessionId}/context`, (route) =>
    json(route, {
      session: sessionState,
      container: null,
      projects: [],
      latest_render_path: sessionState.latest_render_path,
      latest_render_url: `/api/streams/renders/${sessionId}`,
      latest_stream_url: null,
    })
  );
  await page.route(`**/api/agent/sessions/${sessionId}/messages`, (route) => json(route, messages));
  await page.route(`**/api/agent/sessions/${sessionId}/events`, (route) => json(route, events));
  await page.route(`**/api/streams/renders/${sessionId}`, (route) =>
    route.fulfill({
      status: 200,
      contentType: "video/mp4",
      body: "mock-mp4",
    })
  );
}

async function collectOverflowMetrics(page) {
  return page.locator('[data-testid="session-context-sidebar"]').evaluate((sidebar) => {
    const threshold = 4;
    const candidates = [
      { name: "sidebar", element: sidebar },
      ...Array.from(
        sidebar.querySelectorAll(
          ".context-panel, .session-settings-block, .inline-alert, .session-runtime-meta, .timeline-entry, .timeline-summary-preview, .timeline-time"
        )
      ).map((element, index) => ({
        name: `${element.className || element.tagName.toLowerCase()}-${index}`,
        element,
      })),
    ];

    return {
      sidebarClientWidth: sidebar.clientWidth,
      sidebarScrollWidth: sidebar.scrollWidth,
      offenders: candidates
        .map(({ name, element }) => ({
          name,
          clientWidth: element.clientWidth,
          scrollWidth: element.scrollWidth,
          text: (element.textContent || "").trim().slice(0, 120),
        }))
        .filter((entry) => entry.scrollWidth - entry.clientWidth > threshold),
    };
  });
}

async function collectTitlebarMetrics(page) {
  return page.evaluate(() => {
    const titlebar = document.querySelector(".titlebar");
    const center = document.querySelector(".titlebar-center");
    if (!titlebar || !center) {
      return null;
    }

    const titlebarRect = titlebar.getBoundingClientRect();
    const centerRect = center.getBoundingClientRect();
    const titlebarMiddle = titlebarRect.left + titlebarRect.width / 2;
    const centerMiddle = centerRect.left + centerRect.width / 2;

    return {
      titlebarMiddle,
      centerMiddle,
      delta: Math.abs(titlebarMiddle - centerMiddle),
    };
  });
}

(async () => {
  let browser;
  let page;

  try {
    const session = await openWorkbench({ beforeGoto: installMockRoutes });
    browser = session.browser;
    page = session.page;

    await page.waitForSelector('[data-testid="session-settings-card"]');
    await page.waitForSelector('[data-testid="timeline-entry"]');

    const modelSelect = page.locator('[data-testid="session-model-select"]');
    const reasoningSelect = page.locator('[data-testid="session-reasoning-select"]');
    const saveButton = page.locator('[data-testid="session-settings-save"]');
    const timelineEntries = page.locator('[data-testid="timeline-entry"]');
    const assistantMessage = page.locator(".chat-message.role-assistant .markdown-content");
    const previewBadge = page.locator(".video-source-badge");
    const previewVideo = page.locator(".video-element");

    assert.equal(await modelSelect.inputValue(), "gpt-5.4");
    assert.equal(await reasoningSelect.inputValue(), "high");

    const titlebarMetrics = await collectTitlebarMetrics(page);
    assert.ok(titlebarMetrics, "Titlebar center metrics should be available");
    assert.ok(titlebarMetrics.delta < 8, `Workspace title is not visually centered: ${JSON.stringify(titlebarMetrics)}`);

    assert.equal(await timelineEntries.count(), 2, "Timeline entries should render as separate accordion panels");
    assert.equal(await timelineEntries.nth(0).evaluate((entry) => entry.hasAttribute("open")), false, "Timeline entries should be collapsed by default");

    await timelineEntries.nth(0).locator("summary").click();
    assert.equal(await timelineEntries.nth(0).evaluate((entry) => entry.hasAttribute("open")), true, "Clicking a timeline summary should expand the entry");

    assert.ok(await assistantMessage.locator("strong").count(), "Assistant Markdown should render strong text");
    assert.ok(await assistantMessage.locator("code").count(), "Assistant Markdown should render code spans or blocks");

    assert.equal((await previewBadge.textContent())?.trim(), "MP4");
    assert.ok((await previewVideo.getAttribute("src"))?.includes(`/api/streams/renders/${sessionId}`), "Video preview should point at the direct mp4 route");

    let overflowMetrics = await collectOverflowMetrics(page);
    assert.equal(
      overflowMetrics.offenders.length,
      0,
      `Initial session sidebar overflow detected: ${JSON.stringify(overflowMetrics, null, 2)}`
    );

    await modelSelect.selectOption("gpt-4.1");
    assert.equal(await reasoningSelect.isDisabled(), true, "Reasoning selector should disable for models without reasoning support");
    assert.equal(await saveButton.isDisabled(), false, "Save button should enable when the model changes");

    await saveButton.click();
    await page.waitForFunction(() => document.body.textContent.includes("GPT-4.1"));

    const metaAfterMini = (await page.locator(".chat-stage-meta").textContent()) || "";
    assert.ok(metaAfterMini.includes("GPT-4.1"), "Chat header should reflect the session model after saving");

    await modelSelect.selectOption("gpt-5.4");
    await reasoningSelect.selectOption("medium");
    await saveButton.click();
    await page.waitForFunction(() => document.body.textContent.includes("Medium reasoning"));

    assert.equal(await reasoningSelect.isDisabled(), false, "Reasoning selector should re-enable for models that support it");

    const metaAfterReasoning = (await page.locator(".chat-stage-meta").textContent()) || "";
    assert.ok(metaAfterReasoning.includes("GPT 5.4"), "Chat header should switch back to GPT 5.4");
    assert.ok(metaAfterReasoning.includes("Medium reasoning"), "Chat header should reflect the saved reasoning effort");

    overflowMetrics = await collectOverflowMetrics(page);
    assert.equal(
      overflowMetrics.offenders.length,
      0,
      `Session sidebar overflow detected after model changes: ${JSON.stringify(overflowMetrics, null, 2)}`
    );

    if (captureRequested()) {
      const screenshotPath = await captureScreenshot(
        page,
        path.join("tests", "artifacts", "session-page-regression.png")
      );
      console.log(`Saved screenshot to ${screenshotPath}`);
    }

    console.log("Session page regression passed.");
  } catch (error) {
    if (page) {
      try {
        const failurePath = await captureScreenshot(
          page,
          path.join("tests", "artifacts", "session-page-regression.failure.png")
        );
        console.error(`Saved failure screenshot to ${failurePath}`);
      } catch {
        // Ignore screenshot failures while surfacing the original error.
      }
    }

    console.error(error?.stack || error);
    process.exitCode = 1;
  } finally {
    if (browser) {
      await browser.close();
    }
  }
})();
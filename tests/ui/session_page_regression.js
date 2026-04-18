const assert = require("node:assert/strict");
const path = require("node:path");

const { captureRequested, captureScreenshot, openWorkbench } = require("./playwright_shared");

const sessionId = "session-ui-regression";
const now = new Date().toISOString();
const modelOptions = [
  {
    id: "gpt-5.4-mini",
    name: "GPT-5.4 mini",
    supports_reasoning_effort: true,
    supported_reasoning_efforts: ["low", "medium", "high", "xhigh"],
    default_reasoning_effort: "high",
  },
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
  copilot_model: "gpt-5.4-mini",
  copilot_reasoning_effort: "xhigh",
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

async function collectSidebarPanelMetrics(page) {
  return page.locator('[data-testid="session-context-sidebar"]').evaluate((sidebar) => {
    const overview = sidebar.querySelector('.session-overview-panel');
    const resources = sidebar.querySelector('.resources-panel');
    const timeline = sidebar.querySelector('.timeline-panel');
    const modelSelect = sidebar.querySelector('[data-testid="session-model-select"]');
    const reasoningSelect = sidebar.querySelector('[data-testid="session-reasoning-select"]');
    const runtimeValue = sidebar.querySelector('[data-testid="session-runtime-id"]');
    const emptyState = sidebar.querySelector('.resources-panel .empty-side');
    const timelineSummary = sidebar.querySelector('.timeline-entry-summary');
    const timelineEntry = sidebar.querySelector('.timeline-entry');
    const rect = (element) => element ? element.getBoundingClientRect() : null;

    return {
      overviewRect: rect(overview),
      resourcesRect: rect(resources),
      timelineRect: rect(timeline),
      timelineEntryRect: rect(timelineEntry),
      timelineSummaryRect: rect(timelineSummary),
      modelSelectRect: rect(modelSelect),
      modelSelectText: modelSelect?.selectedOptions?.[0]?.textContent?.trim() || null,
      reasoningSelectRect: rect(reasoningSelect),
      reasoningSelectText: reasoningSelect?.selectedOptions?.[0]?.textContent?.trim() || null,
      runtimeValueRect: rect(runtimeValue),
      runtimeValueText: runtimeValue?.textContent?.trim() || null,
      resourcesEmptyText: emptyState?.textContent?.trim() || null,
    };
  });
}

async function collectTimelineEntryMetrics(page, index = 0) {
  return page.locator('[data-testid="timeline-entry"]').nth(index).evaluate((entry) => {
    const summary = entry.querySelector('.timeline-entry-summary');
    const body = entry.querySelector('.timeline-entry-body');
    const chevron = entry.querySelector('.timeline-chevron');
    const type = entry.querySelector('.timeline-type');
    const time = entry.querySelector('.timeline-time');
    const preview = entry.querySelector('.timeline-summary-preview');
    const rect = (element) => element ? element.getBoundingClientRect() : null;

    return {
      isOpen: entry.classList.contains('expanded'),
      entryRect: rect(entry),
      summaryRect: rect(summary),
      bodyRect: rect(body),
      chevronRect: rect(chevron),
      typeRect: rect(type),
      timeRect: rect(time),
      previewRect: rect(preview),
      labelInset: type ? type.getBoundingClientRect().left - entry.getBoundingClientRect().left : null,
      chevronInset: chevron ? chevron.getBoundingClientRect().left - entry.getBoundingClientRect().left : null,
      bodyDisplay: body ? getComputedStyle(body).display : null,
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

    assert.equal(await modelSelect.inputValue(), "gpt-5.4-mini");
    assert.equal(await reasoningSelect.inputValue(), "xhigh");

    const titlebarMetrics = await collectTitlebarMetrics(page);
    assert.ok(titlebarMetrics, "Titlebar center metrics should be available");
    assert.ok(titlebarMetrics.delta < 8, `Workspace title is not visually centered: ${JSON.stringify(titlebarMetrics)}`);

    assert.equal(await timelineEntries.count(), 2, "Timeline entries should render as separate accordion panels");
    assert.equal(await timelineEntries.nth(0).evaluate((entry) => entry.classList.contains("expanded")), false, "Timeline entries should be collapsed by default");

    const collapsedTimelineMetrics = await collectTimelineEntryMetrics(page, 0);
    assert.equal(collapsedTimelineMetrics.isOpen, false, `Timeline entry should start collapsed: ${JSON.stringify(collapsedTimelineMetrics, null, 2)}`);
    assert.equal(collapsedTimelineMetrics.bodyDisplay, null, `Collapsed timeline body should stay unmounted: ${JSON.stringify(collapsedTimelineMetrics, null, 2)}`);
    assert.ok(
      collapsedTimelineMetrics.entryRect && collapsedTimelineMetrics.entryRect.height >= 72,
      `Collapsed timeline entry should keep a readable closed height: ${JSON.stringify(collapsedTimelineMetrics, null, 2)}`
    );
    assert.ok(
      collapsedTimelineMetrics.entryRect && collapsedTimelineMetrics.entryRect.height <= 116,
      `Collapsed timeline entry should stay visually compact: ${JSON.stringify(collapsedTimelineMetrics, null, 2)}`
    );
    assert.ok(
      collapsedTimelineMetrics.typeRect &&
        collapsedTimelineMetrics.timeRect &&
        Math.abs(collapsedTimelineMetrics.typeRect.top - collapsedTimelineMetrics.timeRect.top) <= 6,
      `Timeline title row should keep event label and time aligned on the same row: ${JSON.stringify(collapsedTimelineMetrics, null, 2)}`
    );
    assert.ok(
      collapsedTimelineMetrics.labelInset !== null && collapsedTimelineMetrics.labelInset <= 28,
      `Timeline label inset should stay visually aligned with the card edge: ${JSON.stringify(collapsedTimelineMetrics, null, 2)}`
    );
    assert.ok(
      collapsedTimelineMetrics.chevronInset !== null && collapsedTimelineMetrics.chevronInset <= 18,
      `Timeline chevron should stay close to the left card edge: ${JSON.stringify(collapsedTimelineMetrics, null, 2)}`
    );

    await timelineEntries.nth(0).locator('[data-testid="timeline-entry-toggle"]').click();
    assert.equal(await timelineEntries.nth(0).evaluate((entry) => entry.classList.contains("expanded")), true, "Clicking a timeline summary should expand the entry");

    const expandedTimelineMetrics = await collectTimelineEntryMetrics(page, 0);
    assert.equal(expandedTimelineMetrics.isOpen, true, `Timeline entry should expand after clicking summary: ${JSON.stringify(expandedTimelineMetrics, null, 2)}`);
    assert.equal(expandedTimelineMetrics.bodyDisplay, "flex", `Expanded timeline body should be visible: ${JSON.stringify(expandedTimelineMetrics, null, 2)}`);
    assert.ok(
      expandedTimelineMetrics.entryRect &&
        collapsedTimelineMetrics.entryRect &&
        expandedTimelineMetrics.entryRect.height > collapsedTimelineMetrics.entryRect.height + 40,
      `Expanded timeline entry should clearly grow beyond its collapsed height: ${JSON.stringify({ collapsedTimelineMetrics, expandedTimelineMetrics }, null, 2)}`
    );

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

    const initialPanelMetrics = await collectSidebarPanelMetrics(page);
    assert.equal(initialPanelMetrics.modelSelectText, "GPT-5.4 mini", "Model selector should show the full GPT-5.4 mini label");
    assert.equal(initialPanelMetrics.reasoningSelectText, "Extreme", `Reasoning selector should use compact option labels in the session settings card: ${JSON.stringify(initialPanelMetrics, null, 2)}`);
    assert.ok(initialPanelMetrics.modelSelectRect && initialPanelMetrics.modelSelectRect.width >= 120, `Model selector is still too narrow: ${JSON.stringify(initialPanelMetrics, null, 2)}`);
    assert.ok(
      initialPanelMetrics.modelSelectRect &&
        initialPanelMetrics.reasoningSelectRect &&
        Math.abs(initialPanelMetrics.modelSelectRect.top - initialPanelMetrics.reasoningSelectRect.top) <= 2,
      `Session settings selects should align on the same top edge: ${JSON.stringify(initialPanelMetrics, null, 2)}`
    );
    assert.ok(
      initialPanelMetrics.modelSelectRect &&
        initialPanelMetrics.reasoningSelectRect &&
        initialPanelMetrics.reasoningSelectRect.left - initialPanelMetrics.modelSelectRect.right >= 12,
      `Session settings selects should keep enough horizontal gap instead of crowding together: ${JSON.stringify(initialPanelMetrics, null, 2)}`
    );
    assert.equal(initialPanelMetrics.runtimeValueText, sessionState.copilot_session_id, `Runtime id should stay fully visible in the sidebar: ${JSON.stringify(initialPanelMetrics, null, 2)}`);
    assert.ok(initialPanelMetrics.runtimeValueRect && initialPanelMetrics.runtimeValueRect.height >= 30, `Runtime id row should have enough height to wrap long ids instead of truncating them: ${JSON.stringify(initialPanelMetrics, null, 2)}`);
    assert.equal(initialPanelMetrics.resourcesEmptyText, "No project files have been uploaded yet.", "Resources empty state should stay visible");
    assert.ok(initialPanelMetrics.overviewRect && initialPanelMetrics.overviewRect.height <= 520, `Overview panel should not consume the whole sidebar: ${JSON.stringify(initialPanelMetrics, null, 2)}`);
    assert.ok(initialPanelMetrics.timelineRect && initialPanelMetrics.timelineRect.height >= 220, `Timeline panel collapsed unexpectedly: ${JSON.stringify(initialPanelMetrics, null, 2)}`);
    assert.ok(initialPanelMetrics.timelineEntryRect && initialPanelMetrics.timelineEntryRect.height >= 72, `Timeline entry container collapsed unexpectedly: ${JSON.stringify(initialPanelMetrics, null, 2)}`);
    assert.ok(initialPanelMetrics.timelineSummaryRect && initialPanelMetrics.timelineSummaryRect.height >= 48, `Timeline summaries should remain readable: ${JSON.stringify(initialPanelMetrics, null, 2)}`);

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

    assert.equal((await reasoningSelect.locator('option:checked').textContent())?.trim(), "Medium");

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
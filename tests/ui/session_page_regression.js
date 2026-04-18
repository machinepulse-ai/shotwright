const assert = require("node:assert/strict");
const path = require("node:path");

const { captureRequested, captureScreenshot, openWorkbench } = require("./playwright_shared");

const primarySessionId = "session-ui-regression";
const secondarySessionId = "session-ui-secondary";
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

const sessionStates = {
  [primarySessionId]: {
    _id: primarySessionId,
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
  },
  [secondarySessionId]: {
    _id: secondarySessionId,
    name: "Direct Link Target",
    status: "idle",
    copilot_model: "gpt-5.4",
    copilot_reasoning_effort: "medium",
    copilot_session_id: "shotwright-secondary-session-route-check",
    container_id: null,
    active_project_id: null,
    latest_render_path: null,
    latest_stream_url: null,
    last_error: null,
    created_at: now,
    updated_at: now,
  },
};

const messagesBySession = {
  [primarySessionId]: [
    {
      _id: "msg-user-1",
      session_id: primarySessionId,
      role: "user",
      content: "Inspect the current project and prepare a preview render with a 1920x1080 export.",
      created_at: now,
      metadata: {},
    },
    {
      _id: "msg-assistant-1",
      session_id: primarySessionId,
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
  ],
  [secondarySessionId]: [],
};

const eventsBySession = {
  [primarySessionId]: [
    {
      _id: "evt-1",
      session_id: primarySessionId,
      type: "session.bootstrap.completed",
      summary:
        "Initialized a session with a long event summary to verify that the right sidebar timeline wraps within the available column width.",
      created_at: now,
      data: {},
    },
    {
      _id: "evt-2",
      session_id: primarySessionId,
      type: "session.model.updated",
      summary:
        "Applied a session-specific model preference and kept the reasoning effort selector aligned with the selected model capabilities.",
      created_at: now,
      data: {},
    },
  ],
  [secondarySessionId]: [],
};

const streamedReplyCounts = {
  [primarySessionId]: 0,
  [secondarySessionId]: 0,
};

function json(route, body, status = 200) {
  return route.fulfill({
    status,
    contentType: "application/json",
    body: JSON.stringify(body),
  });
}

function getSessionIdFromRoute(route, collectionName = "sessions") {
  const url = new URL(route.request().url());
  const parts = url.pathname.split("/").filter(Boolean);
  const collectionIndex = parts.indexOf(collectionName);
  return collectionIndex >= 0 ? parts[collectionIndex + 1] : null;
}

function delay(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function emitSessionStreamEvent(page, sessionId, eventName, payload) {
  await page.evaluate(
    ({ sessionId: targetSessionId, eventName: targetEventName, payload: targetPayload }) => {
      window.__shotwrightEmitEventSource(
        `/api/agent/sessions/${targetSessionId}/stream`,
        targetEventName,
        targetPayload
      );
    },
    { sessionId, eventName, payload }
  );
}

async function installMockRoutes(page) {
  await page.addInitScript(() => {
    window.localStorage.setItem("shotwright_locale", "en-US");

    const sources = new Set();

    class MockEventSource {
      constructor(url) {
        this.url = typeof url === "string" ? url : String(url);
        this.readyState = 1;
        this.withCredentials = false;
        this.onopen = null;
        this.onmessage = null;
        this.onerror = null;
        this.listeners = new Map();
        sources.add(this);
        queueMicrotask(() => this.dispatch("open"));
      }

      addEventListener(type, listener) {
        const registered = this.listeners.get(type) || [];
        registered.push(listener);
        this.listeners.set(type, registered);
      }

      removeEventListener(type, listener) {
        const registered = this.listeners.get(type) || [];
        this.listeners.set(
          type,
          registered.filter((candidate) => candidate !== listener)
        );
      }

      close() {
        this.readyState = 2;
        sources.delete(this);
        this.listeners.clear();
      }

      dispatch(type, payload) {
        if (this.readyState === 2) {
          return;
        }

        const event =
          type === "open" || type === "error"
            ? new Event(type)
            : new MessageEvent(type, { data: JSON.stringify(payload) });
        const registered = this.listeners.get(type) || [];
        for (const listener of registered) {
          listener.call(this, event);
        }

        const handler = this[`on${type}`];
        if (typeof handler === "function") {
          handler.call(this, event);
        }
      }
    }

    window.EventSource = MockEventSource;
    window.__shotwrightEmitEventSource = (urlFragment, eventName, payload) => {
      for (const source of sources) {
        if (source.url.includes(urlFragment)) {
          source.dispatch(eventName, payload);
        }
      }
    };
  });

  await page.route("**/api/sessions/model-options", (route) => json(route, modelOptions));
  await page.route("**/api/sessions", async (route) => {
    const method = route.request().method();
    if (method === "GET") {
      return json(route, Object.values(sessionStates));
    }

    return route.continue();
  });
  await page.route("**/api/sessions/*", async (route) => {
    if (getSessionIdFromRoute(route) === "model-options") {
      return json(route, modelOptions);
    }

    const method = route.request().method();
    if (method !== "PATCH") {
      return route.continue();
    }

    const sessionId = getSessionIdFromRoute(route);
    if (!sessionId || !sessionStates[sessionId]) {
      return json(route, { detail: "Session not found" }, 404);
    }

    const payload = JSON.parse(route.request().postData() || "{}");
    sessionStates[sessionId] = {
      ...sessionStates[sessionId],
      ...payload,
      updated_at: new Date().toISOString(),
    };

    return json(route, sessionStates[sessionId]);
  });
  await page.route("**/api/agent/sessions/*/context", (route) => {
    const sessionId = getSessionIdFromRoute(route);
    const sessionState = sessionId ? sessionStates[sessionId] : null;
    if (!sessionState) {
      return json(route, { detail: "Session not found" }, 404);
    }

    return json(route, {
      session: sessionState,
      container: null,
      projects: [],
      latest_render_path: sessionState.latest_render_path,
      latest_render_url: sessionState.latest_render_path ? `/api/streams/renders/${sessionId}` : null,
      latest_stream_url: sessionState.latest_stream_url,
    });
  });
  await page.route("**/api/agent/sessions/*/messages", async (route) => {
    const sessionId = getSessionIdFromRoute(route);
    if (!sessionId || !sessionStates[sessionId]) {
      return json(route, { detail: "Session not found" }, 404);
    }

    if (route.request().method() === "GET") {
      return json(route, messagesBySession[sessionId] || []);
    }

    if (route.request().method() !== "POST") {
      return route.continue();
    }

    const payload = JSON.parse(route.request().postData() || "{}");
    const startedAt = new Date().toISOString();
    streamedReplyCounts[sessionId] += 1;
    const replyNumber = streamedReplyCounts[sessionId];
    const finalReplyText = `Streaming reply ${replyNumber} ready.`;
    const userMessage = {
      _id: `msg-user-${Date.now()}`,
      session_id: sessionId,
      role: "user",
      content: payload.content,
      created_at: startedAt,
      metadata: {},
    };
    const assistantMessage = {
      _id: `msg-assistant-${Date.now()}`,
      session_id: sessionId,
      role: "assistant",
      content: "",
      created_at: startedAt,
      metadata: {
        streaming: true,
        state: "pending",
        version: 0,
      },
    };

    sessionStates[sessionId] = {
      ...sessionStates[sessionId],
      status: "running",
      last_error: null,
      updated_at: startedAt,
    };
    messagesBySession[sessionId] = [...(messagesBySession[sessionId] || []), userMessage, assistantMessage];

    await emitSessionStreamEvent(page, sessionId, "session.updated", sessionStates[sessionId]);
    await emitSessionStreamEvent(page, sessionId, "message.upsert", userMessage);
    await emitSessionStreamEvent(page, sessionId, "message.upsert", assistantMessage);

    setTimeout(() => {
      assistantMessage.content = `Streaming reply ${replyNumber}`;
      assistantMessage.metadata = {
        streaming: true,
        state: "streaming",
        version: 1,
      };
      void emitSessionStreamEvent(page, sessionId, "message.upsert", assistantMessage);
    }, 120);

    setTimeout(() => {
      assistantMessage.content = finalReplyText;
      assistantMessage.metadata = {
        streaming: false,
        state: "completed",
        version: 2,
      };
      sessionStates[sessionId] = {
        ...sessionStates[sessionId],
        status: "idle",
        updated_at: new Date().toISOString(),
      };
      void emitSessionStreamEvent(page, sessionId, "message.upsert", assistantMessage);
      void emitSessionStreamEvent(page, sessionId, "session.updated", sessionStates[sessionId]);
    }, 350);

    await delay(700);
    return json(route, {
      assistant_message: assistantMessage,
      session_status: "idle",
    });
  });
  await page.route("**/api/agent/sessions/*/events", (route) => {
    const sessionId = getSessionIdFromRoute(route);
    if (!sessionId || !sessionStates[sessionId]) {
      return json(route, { detail: "Session not found" }, 404);
    }

    return json(route, eventsBySession[sessionId] || []);
  });
  await page.route(`**/api/streams/renders/${primarySessionId}`, (route) =>
    route.fulfill({
      status: 200,
      contentType: "video/mp4",
      body: "mock-mp4",
    })
  );
}

async function collectChatAlignmentMetrics(page) {
  return page.evaluate(() => {
    const userMessage = document.querySelector(".chat-message.role-user");
    const assistantMessage = document.querySelector(".chat-message.role-assistant");
    const rect = (element) => element ? element.getBoundingClientRect() : null;

    return {
      userRect: rect(userMessage),
      assistantRect: rect(assistantMessage),
    };
  });
}

async function collectSessionListMetrics(page) {
  return page.evaluate(() => {
    return Array.from(document.querySelectorAll('[data-testid="session-list-item"]')).map((item) => {
      const title = item.querySelector('.session-name');
      const footline = item.querySelector('.session-footline');
      const badge = item.querySelector('.status-badge');
      const rect = item.getBoundingClientRect();
      return {
        height: rect.height,
        width: rect.width,
        titleTop: title ? title.getBoundingClientRect().top - rect.top : null,
        footlineBottom: footline ? rect.bottom - footline.getBoundingClientRect().bottom : null,
        badgeTop: badge ? badge.getBoundingClientRect().top - rect.top : null,
      };
    });
  });
}

async function collectStarterCardMetrics(page) {
  return page.evaluate(() => {
    const cards = Array.from(document.querySelectorAll('[data-testid="starter-card"]'));
    return cards.map((card) => {
      const rect = card.getBoundingClientRect();
      return {
        right: rect.right,
        bottom: rect.bottom,
        width: rect.width,
        height: rect.height,
      };
    });
  });
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
    const session = await openWorkbench({ beforeGoto: installMockRoutes, path: `/sessions/${primarySessionId}` });
    browser = session.browser;
    page = session.page;

    await page.waitForSelector('[data-testid="session-settings-card"]');
    await page.waitForSelector('[data-testid="timeline-entry"]');

    assert.equal(await page.evaluate(() => window.location.pathname), `/sessions/${primarySessionId}`);

    const modelSelect = page.locator('[data-testid="session-model-select"]');
    const reasoningSelect = page.locator('[data-testid="session-reasoning-select"]');
    const saveButton = page.locator('[data-testid="session-settings-save"]');
    const timelineEntries = page.locator('[data-testid="timeline-entry"]');
    const assistantMessage = page.locator(".chat-message.role-assistant .markdown-content");
    const previewBadge = page.locator(".video-source-badge");
    const previewVideo = page.locator(".video-element");

    const sessionListMetrics = await collectSessionListMetrics(page);
    assert.ok(sessionListMetrics.length >= 2, "Session list should render multiple cards for alignment checks");
    const baselineCardHeight = sessionListMetrics[0].height;
    for (const metrics of sessionListMetrics) {
      assert.ok(
        Math.abs(metrics.height - baselineCardHeight) <= 2,
        `Session cards should keep a consistent height: ${JSON.stringify(sessionListMetrics, null, 2)}`
      );
      assert.ok(
        metrics.titleTop !== null && metrics.badgeTop !== null && Math.abs(metrics.titleTop - metrics.badgeTop) <= 10,
        `Session card title and badge should align consistently: ${JSON.stringify(sessionListMetrics, null, 2)}`
      );
    }

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

    const chatAlignmentMetrics = await collectChatAlignmentMetrics(page);
    assert.ok(chatAlignmentMetrics.userRect && chatAlignmentMetrics.assistantRect, "Chat bubbles should be measurable");
    assert.ok(
      chatAlignmentMetrics.userRect.left > chatAlignmentMetrics.assistantRect.left + 80,
      `User bubbles should sit to the right of assistant bubbles: ${JSON.stringify(chatAlignmentMetrics, null, 2)}`
    );

    assert.equal((await previewBadge.textContent())?.trim(), "MP4");
    assert.ok((await previewVideo.getAttribute("src"))?.includes(`/api/streams/renders/${primarySessionId}`), "Video preview should point at the direct mp4 route");

    let overflowMetrics = await collectOverflowMetrics(page);
    assert.equal(
      overflowMetrics.offenders.length,
      0,
      `Initial session sidebar overflow detected: ${JSON.stringify(overflowMetrics, null, 2)}`
    );

    const initialPanelMetrics = await collectSidebarPanelMetrics(page);
    assert.equal(initialPanelMetrics.modelSelectText, "GPT-5.4 mini", "Model selector should show the full GPT-5.4 mini label");
    assert.equal(initialPanelMetrics.reasoningSelectText, "Extreme", `Reasoning selector should use compact option labels in the session settings card: ${JSON.stringify(initialPanelMetrics, null, 2)}`);
    assert.ok(initialPanelMetrics.modelSelectRect && initialPanelMetrics.modelSelectRect.width >= 150, `Model selector is still too narrow: ${JSON.stringify(initialPanelMetrics, null, 2)}`);
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
    assert.equal(initialPanelMetrics.runtimeValueText, sessionStates[primarySessionId].copilot_session_id, `Runtime id should stay fully visible in the sidebar: ${JSON.stringify(initialPanelMetrics, null, 2)}`);
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

    await page.locator('[data-testid="session-rename-trigger"]').click();
    const renameInput = page.locator('[data-testid="session-rename-input"]');
    await renameInput.fill('Session Sidebar Renamed');
    await renameInput.press('Enter');
    await page.waitForFunction(() => document.body.textContent.includes('Session Sidebar Renamed'));
    assert.equal((await page.locator('.chat-stage-header h1').textContent())?.trim(), 'Session Sidebar Renamed');
    assert.equal((await page.locator('[data-testid="session-list-item"] .session-name').first().textContent())?.trim(), 'Session Sidebar Renamed');

    overflowMetrics = await collectOverflowMetrics(page);
    assert.equal(
      overflowMetrics.offenders.length,
      0,
      `Session sidebar overflow detected after model changes: ${JSON.stringify(overflowMetrics, null, 2)}`
    );

    const promptInput = page.locator("#agent-prompt");
    const sendButton = page.locator(".send-button");
    await promptInput.fill("Give me a streamed confirmation message.");
    await sendButton.click();

    await page.waitForSelector('[data-testid="composer-status"]');
    assert.equal(await page.evaluate(() => window.location.pathname), `/sessions/${primarySessionId}`);
    assert.ok(
      ((await page.locator(".chat-message-placeholder").last().textContent()) || "").includes("Generating response"),
      "Assistant placeholder should appear immediately while the response is pending"
    );

    await page.waitForFunction(() => document.body.textContent.includes("Streaming reply 1 ready."));
    await page.waitForFunction(() => !document.querySelector('[data-testid="composer-status"]'));

    const postSendMeta = (await page.locator(".chat-stage-meta").textContent()) || "";
    assert.ok(postSendMeta.includes("Idle"), "Header status chip should return to idle after the streamed response completes");

    await promptInput.fill("Send a second streamed confirmation.");
    await sendButton.click();
    await page.waitForSelector('[data-testid="composer-status"]');
    await page.waitForFunction(() => document.body.textContent.includes("Streaming reply 2 ready."));
    await page.waitForFunction(() => !document.querySelector('[data-testid="composer-status"]'));

    const transcriptText = (await page.locator(".chat-transcript").textContent()) || "";
    assert.ok(transcriptText.includes("Streaming reply 1 ready."), "First streamed response should remain in the transcript");
    assert.ok(transcriptText.includes("Streaming reply 2 ready."), "Second streamed response should render without requiring a page refresh");

    await page.getByRole("button", { name: /Direct Link Target/i }).click();
    await page.waitForFunction((sessionId) => window.location.pathname === `/sessions/${sessionId}`, secondarySessionId);
    assert.equal(await page.evaluate(() => window.location.pathname), `/sessions/${secondarySessionId}`);
    assert.equal((await page.locator(".chat-stage-header h1").textContent())?.trim(), "Direct Link Target");

    const starterCardMetrics = await collectStarterCardMetrics(page);
    assert.equal(starterCardMetrics.length, 3, "Starter prompt cards should render in the empty-session welcome state");
    for (const metrics of starterCardMetrics) {
      assert.ok(metrics.width > 0 && metrics.height >= 100, `Starter cards should stay readable: ${JSON.stringify(starterCardMetrics, null, 2)}`);
    }

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
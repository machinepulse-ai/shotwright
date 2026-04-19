const assert = require("node:assert/strict");
const path = require("node:path");

const { captureRequested, captureScreenshot, openWorkbench } = require("./playwright_shared");

const leadingSessionId = "session-ui-leading";
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
  [leadingSessionId]: {
    _id: leadingSessionId,
    name: "Newest Session First",
    status: "idle",
    copilot_model: "gpt-5.4",
    copilot_reasoning_effort: "high",
    copilot_session_id: "shotwright-leading-session-direct-route-check",
    container_id: null,
    active_project_id: null,
    latest_render_path: null,
    latest_stream_url: null,
    last_error: null,
    created_at: now,
    updated_at: now,
  },
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
      metadata: {
        turn_id: "turn-initial-1",
        kind: "user_prompt",
      },
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
      metadata: {
        turn_id: "turn-initial-1",
        kind: "assistant_reply",
      },
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
      data: {
        status: "ready",
        path: "C:/workspace/src/frontend/src/components/AgentPanel/AgentPanel.tsx",
        message:
          "Hydrated session context, attached the session stream, and restored the existing transcript before the operator resumed work.",
      },
      turn_id: "turn-initial-1",
      sequence: 1,
    },
    {
      _id: "evt-2",
      session_id: primarySessionId,
      type: "session.model.updated",
      summary:
        "Applied a session-specific model preference and kept the reasoning effort selector aligned with the selected model capabilities.",
      created_at: now,
      data: {
        previous_model: "gpt-5.4",
        new_model: "gpt-5.4-mini",
        reasoning_effort: "xhigh",
        reason: "Session-specific Copilot override saved from the right sidebar.",
      },
      turn_id: "turn-initial-1",
      sequence: 2,
    },
  ],
  [secondarySessionId]: [],
};

const streamedReplyCounts = {
  [primarySessionId]: 0,
  [secondarySessionId]: 0,
};

let lastTurnPayload = null;

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
    lastTurnPayload = payload;
    const startedAt = new Date().toISOString();
    streamedReplyCounts[sessionId] += 1;
    const replyNumber = streamedReplyCounts[sessionId];
    const turnId = `turn-stream-${replyNumber}`;
    const nextSequenceBase = ((eventsBySession[sessionId] || []).at(-1)?.sequence || 0) + 1;
    const finalReplyText = `Streaming reply ${replyNumber} ready.`;
    const userMessage = {
      _id: `msg-user-${Date.now()}`,
      session_id: sessionId,
      role: "user",
      content: payload.content,
      created_at: startedAt,
      metadata: {
        turn_id: turnId,
        kind: "user_prompt",
        attachments: payload.attachments || [],
      },
    };
    const assistantMessage = {
      _id: `msg-assistant-${Date.now()}`,
      session_id: sessionId,
      role: "assistant",
      content: "",
      created_at: startedAt,
      metadata: {
        turn_id: turnId,
        kind: "assistant_reply",
        streaming: true,
        state: "pending",
        version: 0,
      },
    };
    const streamedEvents = [
      {
        _id: `evt-turn-start-${replyNumber}`,
        session_id: sessionId,
        type: "session.turn.started",
        summary: "Turn submitted to Copilot runtime",
        created_at: startedAt,
        turn_id: turnId,
        sequence: nextSequenceBase,
        data: {
          copilot_message_id: `copilot-message-${replyNumber}`,
          timeout_seconds: 900,
        },
      },
      {
        _id: `evt-tool-complete-${replyNumber}`,
        session_id: sessionId,
        type: "tool.execution_complete",
        summary: `Tool complete: inspect_workspace (${replyNumber})`,
        created_at: startedAt,
        turn_id: turnId,
        sequence: nextSequenceBase + 1,
        data: {
          tool_name: "inspect_workspace",
          success: true,
          output: {
            summary: `Collected composition details for streamed reply ${replyNumber}.`,
          },
        },
      },
    ];

    sessionStates[sessionId] = {
      ...sessionStates[sessionId],
      status: "running",
      last_error: null,
      updated_at: startedAt,
    };
    messagesBySession[sessionId] = [...(messagesBySession[sessionId] || []), userMessage, assistantMessage];
    eventsBySession[sessionId] = [...(eventsBySession[sessionId] || []), ...streamedEvents];

    await emitSessionStreamEvent(page, sessionId, "session.updated", sessionStates[sessionId]);
    await emitSessionStreamEvent(page, sessionId, "message.upsert", userMessage);
    await emitSessionStreamEvent(page, sessionId, "message.upsert", assistantMessage);
    await emitSessionStreamEvent(page, sessionId, "timeline.event", streamedEvents[0]);

    setTimeout(() => {
      assistantMessage.content = `Streaming reply ${replyNumber}`;
      assistantMessage.metadata = {
        turn_id: turnId,
        kind: "assistant_reply",
        streaming: true,
        state: "streaming",
        version: 1,
      };
      void emitSessionStreamEvent(page, sessionId, "message.upsert", assistantMessage);
    }, 120);

    setTimeout(() => {
      void emitSessionStreamEvent(page, sessionId, "timeline.event", streamedEvents[1]);
    }, 160);

    setTimeout(() => {
      assistantMessage.content = finalReplyText;
      assistantMessage.metadata = {
        turn_id: turnId,
        kind: "assistant_reply",
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
      const modelChip = item.querySelector('.session-model-chip');
      const footline = item.querySelector('.session-footline');
      const projectChip = item.querySelector('.session-info-chip');
      const timeChip = item.querySelector('.session-time-chip');
      const badge = item.querySelector('.status-badge');
      const rect = item.getBoundingClientRect();
      return {
        height: rect.height,
        width: rect.width,
        titleTop: title ? title.getBoundingClientRect().top - rect.top : null,
        modelChipTop: modelChip ? modelChip.getBoundingClientRect().top - rect.top : null,
        footlineBottom: footline ? rect.bottom - footline.getBoundingClientRect().bottom : null,
        projectChipBottom: projectChip ? rect.bottom - projectChip.getBoundingClientRect().bottom : null,
        timeChipBottom: timeChip ? rect.bottom - timeChip.getBoundingClientRect().bottom : null,
        timeChipRight: timeChip ? rect.right - timeChip.getBoundingClientRect().right : null,
        badgeTop: badge ? badge.getBoundingClientRect().top - rect.top : null,
        modelChipColor: modelChip ? getComputedStyle(modelChip).color : null,
        modelChipClass: modelChip?.className || null,
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
          ".context-panel, .session-settings-block, .inline-alert, .session-runtime-meta, .timeline-entry, .timeline-summary-preview, .timeline-time, .timeline-detail-grid, .timeline-detail-block, .timeline-raw-details"
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
      selectWidthDelta:
        modelSelect && reasoningSelect
          ? Math.abs(modelSelect.getBoundingClientRect().width - reasoningSelect.getBoundingClientRect().width)
          : null,
    };
  });
}

async function collectWorkbenchLayoutMetrics(page) {
  return page.evaluate(() => {
    const sessionSidebar = document.querySelector('[data-testid="session-list-sidebar"]');
    const contextSidebar = document.querySelector('[data-testid="session-context-sidebar"]');
    const chatStage = document.querySelector('[data-testid="chat-stage"]');
    const rect = (element) => element ? element.getBoundingClientRect() : null;
    const isVisible = (element) => {
      if (!element) {
        return false;
      }

      if (element instanceof HTMLElement && element.hidden) {
        return false;
      }

      const style = getComputedStyle(element);
      return style.display !== 'none' && rect(element)?.width > 0;
    };

    return {
      sessionSidebarVisible: isVisible(sessionSidebar),
      contextSidebarVisible: isVisible(contextSidebar),
      sessionSidebarRect: rect(sessionSidebar),
      contextSidebarRect: rect(contextSidebar),
      chatStageRect: rect(chatStage),
    };
  });
}

async function collectTimelineEntryMetrics(page, index = 0) {
  return page.locator('[data-testid="timeline-entry"]').nth(index).evaluate((entry) => {
    const summary = entry.querySelector('.timeline-entry-summary');
    const body = entry.querySelector('.timeline-entry-body');
    const chevron = entry.querySelector('.timeline-chevron');
    const heading = entry.querySelector('.timeline-summary-heading');
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
      headingRect: rect(heading),
      typeRect: rect(type),
      timeRect: rect(time),
      previewRect: rect(preview),
      labelInset: heading ? heading.getBoundingClientRect().left - entry.getBoundingClientRect().left : null,
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

async function collectComposerMetrics(page) {
  return page.evaluate(() => {
    const composerShell = document.querySelector('.composer-shell');
    const prompt = document.querySelector('#agent-prompt');
    return {
      composerHeight: composerShell ? composerShell.getBoundingClientRect().height : null,
      placeholder: prompt?.getAttribute('placeholder') || null,
      suggestionCount: document.querySelectorAll('.composer-suggestion').length,
      textareaOverflowY: prompt ? getComputedStyle(prompt).overflowY : null,
      shellHasOverflow: composerShell ? composerShell.scrollHeight - composerShell.clientHeight > 1 : null,
    };
  });
}

async function collectTimelineListMetrics(page) {
  return page.locator('[data-testid="session-timeline"]').evaluate((timeline) => {
    const entries = Array.from(timeline.querySelectorAll('[data-testid="timeline-entry"]'));
    const firstEntry = entries[0] || null;
    const timelineRect = timeline.getBoundingClientRect();
    const firstEntryRect = firstEntry ? firstEntry.getBoundingClientRect() : null;

    return {
      count: entries.length,
      scrollTop: timeline.scrollTop,
      clientHeight: timeline.clientHeight,
      scrollHeight: timeline.scrollHeight,
      firstEntryText: firstEntry?.textContent?.trim() || null,
      firstEntryVisible: Boolean(
        firstEntryRect &&
        firstEntryRect.top >= timelineRect.top - 4 &&
        firstEntryRect.bottom <= timelineRect.bottom + 4
      ),
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
        metrics.height <= 96,
        `Session cards should stay compact instead of reverting to tall blank layouts: ${JSON.stringify(sessionListMetrics, null, 2)}`
      );
      assert.ok(
        metrics.titleTop !== null && metrics.badgeTop !== null && Math.abs(metrics.titleTop - metrics.badgeTop) <= 10,
        `Session card title and badge should align consistently: ${JSON.stringify(sessionListMetrics, null, 2)}`
      );
      assert.ok(
        metrics.modelChipTop !== null && metrics.titleTop !== null && metrics.modelChipTop > metrics.titleTop,
        `Session model chip should sit beneath the title instead of drifting into empty card space: ${JSON.stringify(sessionListMetrics, null, 2)}`
      );
      assert.ok(
        metrics.projectChipBottom !== null && metrics.timeChipBottom !== null && Math.abs(metrics.projectChipBottom - metrics.timeChipBottom) <= 4,
        `Session bottom chips should align on the same visual baseline: ${JSON.stringify(sessionListMetrics, null, 2)}`
      );
      assert.ok(
        metrics.timeChipRight !== null && metrics.timeChipRight <= 10,
        `Session timestamp chip should stay tucked into the bottom-right corner: ${JSON.stringify(sessionListMetrics, null, 2)}`
      );
    }
    assert.notEqual(
      sessionListMetrics[0].modelChipColor,
      sessionListMetrics[1].modelChipColor,
      `Different model families and variants should map to different chip colors: ${JSON.stringify(sessionListMetrics, null, 2)}`
    );
    assert.notEqual(
      sessionListMetrics[0].modelChipClass,
      sessionListMetrics[1].modelChipClass,
      `Model chip classes should encode different visual tones: ${JSON.stringify(sessionListMetrics, null, 2)}`
    );

    assert.equal(await modelSelect.inputValue(), "gpt-5.4-mini");
    assert.equal(await reasoningSelect.inputValue(), "xhigh");

    assert.equal(await page.locator('head link[rel="icon"]').getAttribute('href'), '/sw-icon.svg');

    const composerMetrics = await collectComposerMetrics(page);
    assert.equal(composerMetrics.suggestionCount, 0, 'Composer quick action pills should be removed in favor of inline prompt guidance');
    assert.ok(
      composerMetrics.placeholder?.includes('inspect the project structure') && composerMetrics.placeholder?.includes('paste or drop an image'),
      `Composer placeholder should carry the missing prompt guidance: ${JSON.stringify(composerMetrics, null, 2)}`
    );
    assert.equal(
      composerMetrics.textareaOverflowY,
      'hidden',
      `Composer textarea should not render its own vertical scrollbar: ${JSON.stringify(composerMetrics, null, 2)}`
    );
    assert.equal(
      composerMetrics.shellHasOverflow,
      false,
      `Composer shell should not start in an overflowing state: ${JSON.stringify(composerMetrics, null, 2)}`
    );
    assert.ok(await page.locator('.chat-avatar-assistant').count(), 'Assistant messages should render a Shotwright avatar');
    assert.ok(await page.locator('.chat-avatar-user').count(), 'User messages should render a user avatar');

    const resizerHandle = page.locator('[data-testid="composer-resizer"]');
    const composerShell = page.locator('.composer-shell');
    const initialComposerHeight = await composerShell.evaluate((element) => element.getBoundingClientRect().height);
    const resizerBox = await resizerHandle.boundingBox();
    assert.ok(resizerBox, 'Composer resizer should be measurable');
    await page.mouse.move(resizerBox.x + resizerBox.width / 2, resizerBox.y + resizerBox.height / 2);
    await page.mouse.down();
    await page.mouse.move(resizerBox.x + resizerBox.width / 2, resizerBox.y - 80, { steps: 8 });
    await page.mouse.up();
    const resizedComposerHeight = await composerShell.evaluate((element) => element.getBoundingClientRect().height);
    assert.ok(
      resizedComposerHeight > initialComposerHeight + 36,
      `Dragging the composer divider should visibly increase composer height: ${JSON.stringify({ initialComposerHeight, resizedComposerHeight }, null, 2)}`
    );

    const titlebarMetrics = await collectTitlebarMetrics(page);
    assert.ok(titlebarMetrics, "Titlebar center metrics should be available");
    assert.ok(titlebarMetrics.delta < 8, `Workspace title is not visually centered: ${JSON.stringify(titlebarMetrics)}`);
    assert.equal(await page.locator('.titlebar-brand-mark .titlebar-brand-icon').count(), 1, 'Titlebar should render the Shotwright SVG mark instead of text glyphs');
    assert.equal(await page.locator('.titlebar [data-testid="toggle-session-sidebar"]').count(), 1, 'Sidebar toggles should live in the top titlebar');
    assert.equal(await page.locator('.chat-stage-header [data-testid="toggle-session-sidebar"]').count(), 0, 'Sidebar toggles should no longer sit in the session header');

    assert.equal(await timelineEntries.count(), 2, "Timeline entries should render as separate accordion panels");
    assert.equal(await timelineEntries.nth(0).evaluate((entry) => entry.classList.contains("expanded")), false, "Timeline entries should be collapsed by default");
    assert.ok(
      ((await timelineEntries.nth(0).textContent()) || '').includes('Applied a session-specific model preference'),
      'Timeline should render newest events first in the right sidebar'
    );

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
    assert.ok(await timelineEntries.nth(0).locator('.timeline-detail-grid').count(), 'Expanded timeline entries should render structured detail rows');
    assert.ok(await timelineEntries.nth(0).locator('.timeline-raw-details').count(), 'Expanded timeline entries should keep the raw event payload available');

    assert.ok(await assistantMessage.locator("strong").count(), "Assistant Markdown should render strong text");
    assert.ok(await assistantMessage.locator("code").count(), "Assistant Markdown should render code spans or blocks");

    const inlineExecutionBlocks = page.locator('[data-testid="conversation-execution-block"]');
    assert.equal(await inlineExecutionBlocks.count(), 1, "Existing turn-scoped execution steps should render inline in the transcript");
    assert.equal(
      await inlineExecutionBlocks.first().locator('[data-testid="conversation-execution-step"]').count(),
      2,
      "Inline execution trace should list each persisted step for the turn"
    );
    assert.ok(
      ((await inlineExecutionBlocks.first().textContent()) || "").includes("Activity"),
      "Inline execution trace card should show its transcript label"
    );
    assert.equal(
      await inlineExecutionBlocks.first().locator('[data-testid="conversation-execution-step-details"]').count(),
      2,
      'Inline execution steps should expose collapsible detail toggles for dense transcript rendering'
    );
    assert.ok(
      ((await inlineExecutionBlocks.first().textContent()) || "").includes("Hydrated session context"),
      "Inline execution trace should expose step details, not just the final assistant reply"
    );

    const chatAlignmentMetrics = await collectChatAlignmentMetrics(page);
    assert.ok(chatAlignmentMetrics.userRect && chatAlignmentMetrics.assistantRect, "Chat bubbles should be measurable");
    assert.ok(
      chatAlignmentMetrics.userRect.left > chatAlignmentMetrics.assistantRect.left + 80,
      `User bubbles should sit to the right of assistant bubbles: ${JSON.stringify(chatAlignmentMetrics, null, 2)}`
    );

    const initialLayoutMetrics = await collectWorkbenchLayoutMetrics(page);
    assert.equal(initialLayoutMetrics.sessionSidebarVisible, true, 'Session sidebar should start visible');
    assert.equal(initialLayoutMetrics.contextSidebarVisible, true, 'Context sidebar should start visible');

    await page.locator('[data-testid="toggle-session-sidebar"]').click();
    await page.waitForFunction(() => {
      const sidebar = document.querySelector('[data-testid="session-list-sidebar"]');
      return sidebar instanceof HTMLElement && sidebar.hidden;
    });
    const collapsedSessionLayoutMetrics = await collectWorkbenchLayoutMetrics(page);
    assert.equal(collapsedSessionLayoutMetrics.sessionSidebarVisible, false, 'Session sidebar should collapse when toggled');
    assert.ok(
      collapsedSessionLayoutMetrics.chatStageRect &&
        initialLayoutMetrics.chatStageRect &&
        collapsedSessionLayoutMetrics.chatStageRect.width > initialLayoutMetrics.chatStageRect.width + 120,
      `Collapsing the session sidebar should noticeably widen the chat stage: ${JSON.stringify({ initialLayoutMetrics, collapsedSessionLayoutMetrics }, null, 2)}`
    );

    await page.locator('[data-testid="toggle-context-sidebar"]').click();
    await page.waitForFunction(() => {
      const sidebar = document.querySelector('[data-testid="session-context-sidebar"]');
      return sidebar instanceof HTMLElement && sidebar.hidden;
    });
    const collapsedBothLayoutMetrics = await collectWorkbenchLayoutMetrics(page);
    assert.equal(collapsedBothLayoutMetrics.contextSidebarVisible, false, 'Context sidebar should collapse when toggled');
    assert.ok(
      collapsedBothLayoutMetrics.chatStageRect &&
        collapsedSessionLayoutMetrics.chatStageRect &&
        collapsedBothLayoutMetrics.chatStageRect.width > collapsedSessionLayoutMetrics.chatStageRect.width + 220,
      `Collapsing both sidebars should free substantially more space for the center pane: ${JSON.stringify({ collapsedSessionLayoutMetrics, collapsedBothLayoutMetrics }, null, 2)}`
    );

    await page.locator('[data-testid="toggle-session-sidebar"]').click();
    await page.locator('[data-testid="toggle-context-sidebar"]').click();
    await page.waitForFunction(() => {
      const sessionSidebar = document.querySelector('[data-testid="session-list-sidebar"]');
      const contextSidebar = document.querySelector('[data-testid="session-context-sidebar"]');
      return sessionSidebar instanceof HTMLElement && !sessionSidebar.hidden && contextSidebar instanceof HTMLElement && !contextSidebar.hidden;
    });

    const restoredLayoutMetrics = await collectWorkbenchLayoutMetrics(page);
    assert.equal(restoredLayoutMetrics.sessionSidebarVisible, true, 'Session sidebar should reopen when toggled again');
    assert.equal(restoredLayoutMetrics.contextSidebarVisible, true, 'Context sidebar should reopen when toggled again');

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
    assert.ok(initialPanelMetrics.modelSelectRect && initialPanelMetrics.modelSelectRect.width >= 136, `Model selector is still too narrow: ${JSON.stringify(initialPanelMetrics, null, 2)}`);
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
    assert.ok(
      initialPanelMetrics.selectWidthDelta !== null && initialPanelMetrics.selectWidthDelta <= 2,
      `Session settings selects should have matching widths: ${JSON.stringify(initialPanelMetrics, null, 2)}`
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
    assert.equal(
      (await page.locator('[data-testid="session-list-item"].active .session-name').textContent())?.trim(),
      'Session Sidebar Renamed'
    );

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
    await page.waitForFunction(() => document.querySelectorAll('[data-testid="conversation-execution-block"]').length >= 2);

    let timelineListMetrics = await collectTimelineListMetrics(page);
    assert.equal(
      timelineListMetrics.count,
      4,
      `First follow-up should append two new timeline entries: ${JSON.stringify(timelineListMetrics, null, 2)}`
    );
    assert.ok(
      timelineListMetrics.firstEntryText?.includes('inspect_workspace (1)'),
      `Timeline should show the latest first follow-up event details: ${JSON.stringify(timelineListMetrics, null, 2)}`
    );

    const postSendMeta = (await page.locator(".chat-stage-meta").textContent()) || "";
    assert.ok(postSendMeta.includes("Idle"), "Header status chip should return to idle after the streamed response completes");

    await promptInput.fill("Send a second streamed confirmation.");
    await sendButton.click();
    await page.waitForSelector('[data-testid="composer-status"]');
    await page.waitForFunction(() => document.body.textContent.includes("Streaming reply 2 ready."));
    await page.waitForFunction(() => !document.querySelector('[data-testid="composer-status"]'));
    await page.waitForFunction(() => document.querySelectorAll('[data-testid="conversation-execution-block"]').length >= 3);

    timelineListMetrics = await collectTimelineListMetrics(page);
    assert.equal(
      timelineListMetrics.count,
      6,
      `Second follow-up should continue appending timeline entries without a manual refresh: ${JSON.stringify(timelineListMetrics, null, 2)}`
    );
    assert.ok(
      timelineListMetrics.firstEntryText?.includes('inspect_workspace (2)'),
      `Timeline should advance to the newest follow-up event: ${JSON.stringify(timelineListMetrics, null, 2)}`
    );
    if (timelineListMetrics.scrollHeight > timelineListMetrics.clientHeight + 12) {
      assert.ok(
        timelineListMetrics.scrollTop <= 4,
        `Latest-first timeline should stay pinned to the top after follow-up events: ${JSON.stringify(timelineListMetrics, null, 2)}`
      );
      assert.ok(
        timelineListMetrics.firstEntryVisible,
        `Latest timeline entry should remain visible after a follow-up: ${JSON.stringify(timelineListMetrics, null, 2)}`
      );
    }

    const transcriptText = (await page.locator(".chat-transcript").textContent()) || "";
    assert.ok(transcriptText.includes("Streaming reply 1 ready."), "First streamed response should remain in the transcript");
    assert.ok(transcriptText.includes("Streaming reply 2 ready."), "Second streamed response should render without requiring a page refresh");
    assert.ok(transcriptText.includes("Tool complete: inspect_workspace (1)"), "First streamed turn should surface its tool result inline in the transcript");
    assert.ok(transcriptText.includes("Tool complete: inspect_workspace (2)"), "Second streamed turn should also append its tool result inline");

    await promptInput.evaluate((element) => {
      const pngBase64 = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO7Z0ioAAAAASUVORK5CYII=';
      const bytes = Uint8Array.from(atob(pngBase64), (character) => character.charCodeAt(0));
      const file = new File([bytes], 'clipboard-sample.png', { type: 'image/png' });
      const event = new Event('paste', { bubbles: true, cancelable: true });
      Object.defineProperty(event, 'clipboardData', {
        value: {
          items: [
            {
              kind: 'file',
              type: 'image/png',
              getAsFile() {
                return file;
              },
            },
          ],
        },
      });
      element.dispatchEvent(event);
    });
    await page.waitForFunction(() => document.querySelectorAll('[data-testid="composer-attachment"]').length === 1);
    assert.equal(await sendButton.isDisabled(), false, 'Image attachments alone should make the turn sendable');
    assert.equal(
      await page.locator('[data-testid="composer-attachment"] .composer-attachment-remove').evaluate((button) => (button.textContent || '').trim()),
      '',
      'Attachment remove control should be icon-only instead of rendering a text pill'
    );

    await sendButton.click();
    await page.waitForSelector('[data-testid="composer-status"]');
    await page.waitForFunction(() => document.body.textContent.includes('Streaming reply 3 ready.'));
    await page.waitForFunction(() => !document.querySelector('[data-testid="composer-status"]'));
    assert.equal(lastTurnPayload?.attachments?.length, 1, `Clipboard image should be sent as a real attachment payload: ${JSON.stringify(lastTurnPayload, null, 2)}`);
    assert.equal(lastTurnPayload?.content, '', 'Attachment-only sends should not require extra placeholder text');
    assert.ok(await page.locator('.chat-attachment-image').count(), 'Sent image attachments should remain visible in the transcript');

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
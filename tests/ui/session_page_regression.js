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

const referenceVideosBySession = {
  [primarySessionId]: [
    {
      id: "reference-video-1",
      session_id: primarySessionId,
      filename: "lyrics.mp4",
      file_path: "C:/data/uploads/session-ui-regression/_reference-videos/lyrics.mp4",
      shared_relative_path: "session-ui-regression/_reference-videos/lyrics.mp4",
      mime_type: "video/mp4",
      size_bytes: 1698693,
      duration_seconds: 15.8,
      width: 1280,
      height: 720,
      created_at: now,
    },
  ],
  [secondarySessionId]: [],
};

const storyboardBase = {
  session_id: primarySessionId,
  mime_type: "image/jpeg",
  created_at: now,
  source_video_path: "C:/data/uploads/session-ui-regression/_reference-videos/lyrics.mp4",
  source_video_relative_path: "session-ui-regression/_reference-videos/lyrics.mp4",
  source_video_filename: "lyrics.mp4",
  source_video_duration_seconds: 15.8,
  clip_start_seconds: 0,
  clip_end_seconds: 6,
  clip_duration_seconds: 6,
  interval_seconds: 0.75,
  columns: 4,
  rows: 2,
  tile_width: 220,
  estimated_frames: 8,
  ffmpeg_filter: "fps=1/0.75,scale=220:-1,tile=4x2:margin=8:padding=8:color=white",
};

const storyboardsBySession = {
  [primarySessionId]: [
    {
      ...storyboardBase,
      id: "storyboard-1",
      filename: "lyrics_seq1_storyboard.jpg",
      file_path: "C:/data/uploads/session-ui-regression/_storyboards/lyrics_seq1_storyboard.jpg",
      shared_relative_path: "session-ui-regression/_storyboards/lyrics_seq1_storyboard.jpg",
    },
    {
      ...storyboardBase,
      id: "storyboard-2",
      filename: "lyrics_seq2_storyboard.jpg",
      file_path: "C:/data/uploads/session-ui-regression/_storyboards/lyrics_seq2_storyboard.jpg",
      shared_relative_path: "session-ui-regression/_storyboards/lyrics_seq2_storyboard.jpg",
    },
    {
      ...storyboardBase,
      id: "storyboard-3",
      filename: "lyrics_seq3_storyboard.jpg",
      file_path: "C:/data/uploads/session-ui-regression/_storyboards/lyrics_seq3_storyboard.jpg",
      shared_relative_path: "session-ui-regression/_storyboards/lyrics_seq3_storyboard.jpg",
    },
    {
      ...storyboardBase,
      id: "storyboard-4",
      filename: "lyrics_seq4_storyboard.jpg",
      file_path: "C:/data/uploads/session-ui-regression/_storyboards/lyrics_seq4_storyboard.jpg",
      shared_relative_path: "session-ui-regression/_storyboards/lyrics_seq4_storyboard.jpg",
    },
    {
      ...storyboardBase,
      id: "storyboard-5",
      filename: "lyrics_seq5_storyboard_really_long_file_name_that_should_wrap_inside_the_reference_media_card.jpg",
      file_path: "C:/data/uploads/session-ui-regression/_storyboards/lyrics_seq5_storyboard_really_long_file_name_that_should_wrap_inside_the_reference_media_card.jpg",
      shared_relative_path: "session-ui-regression/_storyboards/lyrics_seq5_storyboard_really_long_file_name_that_should_wrap_inside_the_reference_media_card.jpg",
    },
  ],
  [secondarySessionId]: [],
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
      type: "assistant.intent",
      summary: "Preparing AE project",
      created_at: now,
      data: {
        intent: "Preparing AE project",
      },
      turn_id: "turn-initial-1",
      sequence: 1,
    },
    {
      _id: "evt-2",
      session_id: primarySessionId,
      type: "tool.execution_start",
      summary: "Tool start: report_intent",
      created_at: now,
      data: {
        tool_name: "report_intent",
        arguments: {
          intent: "Preparing AE project",
        },
      },
      turn_id: "turn-initial-1",
      sequence: 2,
    },
    {
      _id: "evt-3",
      session_id: primarySessionId,
      type: "tool.execution_complete",
      summary: "Tool complete: unknown (ok)",
      created_at: now,
      data: {
        result: {
          content: "Intent logged",
          detailed_content: "Preparing AE project",
        },
      },
      turn_id: "turn-initial-1",
      sequence: 3,
    },
    {
      _id: "evt-4",
      session_id: primarySessionId,
      type: "tool.execution_start",
      summary: "Tool start: inspect_project_structure",
      created_at: now,
      data: {
        tool_name: "inspect_project_structure",
        tool_call_id: "tool-call-inspect-project-1",
        arguments: {
          path: "C:/workspace/validation-data",
        },
      },
      turn_id: "turn-initial-1",
      sequence: 4,
    },
    {
      _id: "evt-5",
      session_id: primarySessionId,
      type: "tool.execution_complete",
      summary: "Inspect project structure",
      created_at: now,
      data: {
        tool_name: "inspect_project_structure",
        success: true,
        output: {
          summary: "No uploaded projects · render-workflow.md · job-template.json",
        },
      },
      turn_id: "turn-initial-1",
      sequence: 5,
    },
    {
      _id: "evt-6",
      session_id: primarySessionId,
      type: "tool.execution_start",
      summary: "Tool start: glob_search",
      created_at: now,
      data: {
        tool_name: "glob_search",
        tool_call_id: "tool-call-glob-1",
        input: {
          pattern: "validation-data/**/*",
        },
      },
      turn_id: "turn-initial-1",
      sequence: 6,
    },
    {
      _id: "evt-7",
      session_id: primarySessionId,
      type: "tool.execution_complete",
      summary: "Ran Glob",
      created_at: now,
      data: {
        tool_name: "glob_search",
        success: true,
        output: {
          summary: "No files matched the pattern.",
        },
      },
      turn_id: "turn-initial-1",
      sequence: 7,
    },
    {
      _id: "evt-8",
      session_id: primarySessionId,
      type: "assistant.intent",
      summary: "Debugging JSX timeout",
      created_at: now,
      data: {
        intent: "Debugging JSX timeout",
      },
      turn_id: "turn-initial-1",
      sequence: 8,
    },
    {
      _id: "evt-9",
      session_id: primarySessionId,
      type: "tool.execution_start",
      summary: "Tool start: run_after_effects_jsx",
      created_at: now,
      data: {
        tool_name: "run_after_effects_jsx",
        tool_call_id: "tool-call-jsx-1",
        arguments: {
          script_path: "validation_patch.jsx",
        },
      },
      turn_id: "turn-initial-1",
      sequence: 9,
    },
    {
      _id: "evt-10",
      session_id: primarySessionId,
      type: "permission.requested",
      summary: "Permission requested to run JSX patch",
      created_at: now,
      data: {
        tool_name: "run_after_effects_jsx",
        reason: "Execute validation patch inside After Effects",
      },
      turn_id: "turn-initial-1",
      sequence: 10,
    },
    {
      _id: "evt-11",
      session_id: primarySessionId,
      type: "permission.completed",
      summary: "Permission granted",
      created_at: now,
      data: {
        tool_name: "run_after_effects_jsx",
        approved: true,
      },
      turn_id: "turn-initial-1",
      sequence: 11,
    },
    {
      _id: "evt-12",
      session_id: primarySessionId,
      type: "external_tool.requested",
      summary: "Sending JSX patch to After Effects",
      created_at: now,
      data: {
        tool_name: "run_after_effects_jsx",
        input: {
          script_path: "validation_patch.jsx",
        },
      },
      turn_id: "turn-initial-1",
      sequence: 12,
    },
    {
      _id: "evt-13",
      session_id: primarySessionId,
      type: "external_tool.completed",
      summary: "After Effects returned an error",
      created_at: now,
      data: {
        tool_name: "run_after_effects_jsx",
        success: false,
        error: "AfterFX JSX execution timed out.",
      },
      turn_id: "turn-initial-1",
      sequence: 13,
    },
    {
      _id: "evt-14",
      session_id: primarySessionId,
      type: "tool.execution_complete",
      summary: "Run After Effects JSX",
      created_at: now,
      data: {
        tool_name: "run_after_effects_jsx",
        success: false,
        error: "AfterFX JSX execution timed out.",
      },
      turn_id: "turn-initial-1",
      sequence: 14,
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
    window.fetch = undefined;

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
      reference_videos: referenceVideosBySession[sessionId] || [],
      storyboards: storyboardsBySession[sessionId] || [],
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
    const sidebar = document.querySelector('[data-testid="session-list-sidebar"]');
    const sidebarRect = sidebar?.getBoundingClientRect() || null;

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
        leftInset: sidebarRect ? rect.left - sidebarRect.left : null,
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
          ".context-panel, .session-settings-block, .inline-alert, .session-runtime-meta"
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

async function collectScrollbarVisibilityMetrics(page) {
  return page.evaluate(() => {
    const read = (selector) => {
      const element = document.querySelector(selector);
      if (!element) {
        return null;
      }

      const style = getComputedStyle(element);
      const scrollbarStyle = getComputedStyle(element, '::-webkit-scrollbar');

      return {
        selector,
        scrollbarWidth: style.scrollbarWidth || null,
        webkitDisplay: scrollbarStyle.display || null,
      };
    };

    return {
      transcript: read('.chat-transcript'),
      contextSidebar: read('[data-testid="session-context-sidebar"]'),
    };
  });
}

async function collectSessionWorkbenchPanelMetrics(page) {
  return page.evaluate(() => {
    const sidebar = document.querySelector('[data-testid="session-context-sidebar"]');
    const overview = sidebar?.querySelector('.session-overview-panel');
    const overviewGrid = sidebar?.querySelector('[data-testid="session-overview-grid"]');
    const containerPanel = sidebar?.querySelector('.container-manager');
    const resources = sidebar?.querySelector('.resources-panel');
    const previewPanel = document.querySelector('[data-testid="render-preview-panel"]');
    const previewTrigger = document.querySelector('[data-testid="render-preview-trigger"]');
    const runtimeValue = sidebar?.querySelector('[data-testid="session-runtime-id"]');
    const emptyState = sidebar?.querySelector('.resources-panel .empty-side');
    const composerShell = document.querySelector('.composer-shell');
    const composerCard = composerShell?.querySelector('.composer-card');
    const prompt = composerCard?.querySelector('#agent-prompt');
    const attachments = composerCard?.querySelector('.composer-attachments');
    const footer = composerCard?.querySelector('.composer-footer');
    const settingsCard = document.querySelector('[data-testid="session-settings-card"]');
    const modelSelect = document.querySelector('[data-testid="session-model-select"]');
    const reasoningSelect = document.querySelector('[data-testid="session-reasoning-select"]');
    const saveButton = document.querySelector('[data-testid="session-settings-save"]');
    const rect = (element) => element ? element.getBoundingClientRect() : null;
    const readPx = (element, property) => {
      if (!element) {
        return null;
      }

      const value = Number.parseFloat(getComputedStyle(element)[property]);
      return Number.isFinite(value) ? value : null;
    };

    return {
      overviewRect: rect(overview),
      overviewGridRect: rect(overviewGrid),
      containerPanelRect: rect(containerPanel),
      containerPanelFlexShrink: containerPanel ? getComputedStyle(containerPanel).flexShrink : null,
      resourcesRect: rect(resources),
      previewPanelRect: rect(previewPanel),
      previewPanelFlexShrink: previewPanel ? getComputedStyle(previewPanel).flexShrink : null,
      previewTriggerRect: rect(previewTrigger),
      composerShellRect: rect(composerShell),
      composerCardRect: rect(composerCard),
      promptRect: rect(prompt),
      promptPaddingLeft: readPx(prompt, 'paddingLeft'),
      promptPaddingRight: readPx(prompt, 'paddingRight'),
      attachmentsPaddingLeft: readPx(attachments, 'paddingLeft'),
      attachmentsPaddingRight: readPx(attachments, 'paddingRight'),
      footerPaddingLeft: readPx(footer, 'paddingLeft'),
      footerPaddingRight: readPx(footer, 'paddingRight'),
      settingsCardRect: rect(settingsCard),
      settingsInComposer: Boolean(composerShell && settingsCard && composerShell.contains(settingsCard)),
      settingsInSidebar: Boolean(sidebar && settingsCard && sidebar.contains(settingsCard)),
      settingsInComposerCard: Boolean(composerCard && settingsCard && composerCard.contains(settingsCard)),
      modelSelectRect: rect(modelSelect),
      modelSelectText: modelSelect?.selectedOptions?.[0]?.textContent?.trim() || null,
      reasoningSelectRect: rect(reasoningSelect),
      reasoningSelectText: reasoningSelect?.selectedOptions?.[0]?.textContent?.trim() || null,
      saveButtonRect: rect(saveButton),
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

async function collectPaneResizerMetrics(page, testId) {
  return page.locator(`[data-testid="${testId}"]`).evaluate((handle) => {
    const rect = handle.getBoundingClientRect();
    const style = getComputedStyle(handle);
    const beforeStyle = getComputedStyle(handle, '::before');
    const afterStyle = getComputedStyle(handle, '::after');

    return {
      width: rect.width,
      height: rect.height,
      cursor: style.cursor,
      gripBackgroundColor: beforeStyle.backgroundColor,
      gripBackgroundImage: beforeStyle.backgroundImage,
      gripBorderRadius: beforeStyle.borderRadius,
      afterContent: afterStyle.content,
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

async function collectExecutionBlockMetrics(page, index = 0) {
  return page.locator('[data-testid="conversation-execution-block"]').nth(index).evaluate((block) => {
    return {
      groupCount: block.querySelectorAll('[data-testid="conversation-execution-group"]').length,
      text: block.textContent?.trim() || null,
    };
  });
}

async function collectExecutionGroupMetrics(page, blockIndex = 0, groupIndex = 0) {
  return page
    .locator('[data-testid="conversation-execution-block"]')
    .nth(blockIndex)
    .locator('[data-testid="conversation-execution-group"]')
    .nth(groupIndex)
    .evaluate((group) => {
      const summary = group.querySelector('[data-testid="conversation-execution-toggle"]');
      const title = group.querySelector('.chat-execution-summary-text');
      const preview = group.querySelector('.chat-execution-summary-preview');
      const card = group.querySelector('.chat-execution-card');
      const pills = Array.from(group.querySelectorAll('.chat-execution-card .chat-execution-pill')).map(
        (pill) => pill.textContent?.trim() || ''
      );
      const steps = Array.from(group.querySelectorAll('[data-testid="conversation-execution-step"]'));
      const rect = (element) => element ? element.getBoundingClientRect() : null;

      return {
        open: group.hasAttribute('open'),
        title: title?.textContent?.trim() || null,
        preview: preview?.textContent?.trim() || null,
        pills,
        stepCount: steps.length,
        summaryRect: rect(summary),
        cardRect: rect(card),
        cardDisplay: card ? getComputedStyle(card).display : null,
      };
    });
}

async function collectExecutionStepMetrics(page, blockIndex = 0, groupIndex = 0, stepIndex = 0) {
  return page
    .locator('[data-testid="conversation-execution-block"]')
    .nth(blockIndex)
    .locator('[data-testid="conversation-execution-group"]')
    .nth(groupIndex)
    .locator('[data-testid="conversation-execution-step"]')
    .nth(stepIndex)
    .evaluate((step) => {
    const summary = step.querySelector('[data-testid="conversation-execution-step-toggle"]');
    const body = step.querySelector('[data-testid="conversation-execution-step-details"]');
    const title = step.querySelector('.chat-execution-step-title');
    const preview = step.querySelector('.chat-execution-step-summary');
    const stage = step.querySelector('.timeline-stage-badge');
    const markdownBlocks = step.querySelectorAll('.timeline-detail-block-markdown.markdown-content').length;
    const rawPayloadBlocks = step.querySelectorAll('.timeline-raw-details').length;
    const rect = (element) => element ? element.getBoundingClientRect() : null;

    return {
      open: step.hasAttribute('open'),
      title: title?.textContent?.trim() || null,
      preview: preview?.textContent?.trim() || null,
      stage: stage?.textContent?.trim() || null,
      markdownBlockCount: markdownBlocks,
      rawPayloadBlockCount: rawPayloadBlocks,
      summaryRect: rect(summary),
      bodyRect: rect(body),
      bodyDisplay: body ? getComputedStyle(body).display : null,
    };
  });
}

async function collectAssistantExecutionPlacementMetrics(page) {
  return page.evaluate(() => {
    const assistantMessage = document.querySelector('.chat-message.role-assistant');
    const assistantMeta = assistantMessage?.querySelector('.chat-message-meta');
    const assistantBody = assistantMessage?.querySelector('.chat-message-body');
    const executionBlock = assistantMessage?.querySelector('[data-testid="conversation-execution-block"]');
    const rect = (element) => element ? element.getBoundingClientRect() : null;

    return {
      insideAssistant: Boolean(executionBlock),
      assistantMetaRect: rect(assistantMeta),
      assistantBodyRect: rect(assistantBody),
      executionRect: rect(executionBlock),
    };
  });
}

(async () => {
  let browser;
  let page;

  try {
    const session = await openWorkbench({
      beforeGoto: installMockRoutes,
      path: `/sessions/${primarySessionId}`,
      waitUntil: 'domcontentloaded',
      gotoTimeout: 60000,
      readyTimeout: 60000,
    });
    browser = session.browser;
    page = session.page;

    await page.waitForSelector('[data-testid="session-settings-card"]');
    await page.waitForSelector('[data-testid="conversation-execution-block"]');

    assert.equal(await page.evaluate(() => window.location.pathname), `/sessions/${primarySessionId}`);

    const modelSelect = page.locator('[data-testid="session-model-select"]');
    const reasoningSelect = page.locator('[data-testid="session-reasoning-select"]');
    const saveButton = page.locator('[data-testid="session-settings-save"]');
    const assistantMessage = page.locator(".chat-message.role-assistant .markdown-content");
    const previewPanel = page.locator('[data-testid="render-preview-panel"]');
    const previewBadge = previewPanel.locator('.video-source-badge');
    const previewTrigger = page.locator('[data-testid="render-preview-trigger"]');
    const previewModal = page.locator('[data-testid="render-preview-modal"]');

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
      assert.ok(
        metrics.leftInset !== null && metrics.leftInset <= 2,
        `Session cards should hug the left edge instead of floating with a blank gutter: ${JSON.stringify(sessionListMetrics, null, 2)}`
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
    assert.ok(
      composerMetrics.composerHeight !== null && composerMetrics.composerHeight <= 196,
      `Composer should stay compact by default instead of consuming a tall footer block: ${JSON.stringify(composerMetrics, null, 2)}`
    );
    assert.equal(await page.locator('[data-testid="composer-mode-pill"]').count(), 0, 'Composer should not render the extra Agent mode pill');
    assert.ok(await page.locator('.chat-avatar-assistant').count(), 'Assistant messages should render a Shotwright avatar');
    assert.ok(await page.locator('.chat-avatar-user').count(), 'User messages should render a user avatar');

    const scrollbarMetrics = await collectScrollbarVisibilityMetrics(page);
    for (const [key, metrics] of Object.entries(scrollbarMetrics)) {
      assert.ok(
        metrics && (metrics.webkitDisplay === 'none' || metrics.scrollbarWidth === 'none'),
        `Scrollable workbench surfaces should hide native scrollbars: ${JSON.stringify(scrollbarMetrics, null, 2)}`
      );
    }

    const resizerHandle = page.locator('[data-testid="composer-resizer"]');
    const composerShell = page.locator('.composer-shell');
    const initialComposerHeight = await composerShell.evaluate((element) => element.getBoundingClientRect().height);
    const composerResizerMetrics = await collectPaneResizerMetrics(page, 'composer-resizer');
    const resizerBox = await resizerHandle.boundingBox();
    assert.ok(resizerBox, 'Composer resizer should be measurable');
    assert.ok(composerResizerMetrics.height >= 12 && composerResizerMetrics.cursor === 'row-resize', `Composer resizer should be visible and vertically draggable: ${JSON.stringify(composerResizerMetrics, null, 2)}`);
    await page.mouse.move(resizerBox.x + resizerBox.width / 2, resizerBox.y + resizerBox.height / 2);
    await page.mouse.down();
    await page.mouse.move(resizerBox.x + resizerBox.width / 2, resizerBox.y - 80, { steps: 8 });
    await page.mouse.up();
    const resizedComposerHeight = await composerShell.evaluate((element) => element.getBoundingClientRect().height);
    assert.ok(
      resizedComposerHeight > initialComposerHeight + 36,
      `Dragging the composer divider should visibly increase composer height: ${JSON.stringify({ initialComposerHeight, resizedComposerHeight }, null, 2)}`
    );

    const sessionPaneResizer = page.locator('[data-testid="session-sidebar-resizer"]');
    const contextPaneResizer = page.locator('[data-testid="context-sidebar-resizer"]');
    const sessionPaneResizerMetrics = await collectPaneResizerMetrics(page, 'session-sidebar-resizer');
    const contextPaneResizerMetrics = await collectPaneResizerMetrics(page, 'context-sidebar-resizer');
    assert.ok(sessionPaneResizerMetrics.width >= 12 && sessionPaneResizerMetrics.cursor === 'col-resize', `Session pane resizer should be visible and horizontally draggable: ${JSON.stringify(sessionPaneResizerMetrics, null, 2)}`);
    assert.ok(contextPaneResizerMetrics.width >= 12 && contextPaneResizerMetrics.cursor === 'col-resize', `Context pane resizer should be visible and horizontally draggable: ${JSON.stringify(contextPaneResizerMetrics, null, 2)}`);
    assert.ok(
      sessionPaneResizerMetrics.gripBackgroundImage === 'none' &&
        contextPaneResizerMetrics.gripBackgroundImage === 'none' &&
        composerResizerMetrics.gripBackgroundImage === 'none' &&
        sessionPaneResizerMetrics.gripBackgroundColor !== 'rgba(0, 0, 0, 0)' &&
        contextPaneResizerMetrics.gripBackgroundColor !== 'rgba(0, 0, 0, 0)' &&
        composerResizerMetrics.gripBackgroundColor !== 'rgba(0, 0, 0, 0)' &&
        sessionPaneResizerMetrics.gripBorderRadius === contextPaneResizerMetrics.gripBorderRadius &&
        contextPaneResizerMetrics.gripBorderRadius === composerResizerMetrics.gripBorderRadius &&
        sessionPaneResizerMetrics.afterContent === 'none' &&
        contextPaneResizerMetrics.afterContent === 'none' &&
        composerResizerMetrics.afterContent === 'none',
      `All three resize handles should use the same plain solid bar treatment: ${JSON.stringify({ sessionPaneResizerMetrics, contextPaneResizerMetrics, composerResizerMetrics }, null, 2)}`
    );

    const layoutBeforePaneResize = await collectWorkbenchLayoutMetrics(page);
    const sessionPaneResizerBox = await sessionPaneResizer.boundingBox();
    assert.ok(sessionPaneResizerBox, 'Session pane resizer should be measurable');
    await page.mouse.move(sessionPaneResizerBox.x + sessionPaneResizerBox.width / 2, sessionPaneResizerBox.y + sessionPaneResizerBox.height / 2);
    await page.mouse.down();
    await page.mouse.move(sessionPaneResizerBox.x + sessionPaneResizerBox.width / 2 + 52, sessionPaneResizerBox.y + sessionPaneResizerBox.height / 2, { steps: 8 });
    await page.mouse.up();
    const layoutAfterSessionPaneResize = await collectWorkbenchLayoutMetrics(page);
    assert.ok(
      layoutAfterSessionPaneResize.sessionSidebarRect &&
        layoutBeforePaneResize.sessionSidebarRect &&
        layoutAfterSessionPaneResize.sessionSidebarRect.width > layoutBeforePaneResize.sessionSidebarRect.width + 36,
      `Dragging the session pane handle should visibly widen the left sidebar: ${JSON.stringify({ layoutBeforePaneResize, layoutAfterSessionPaneResize }, null, 2)}`
    );

    const contextPaneResizerBox = await contextPaneResizer.boundingBox();
    assert.ok(contextPaneResizerBox, 'Context pane resizer should be measurable');
    await page.mouse.move(contextPaneResizerBox.x + contextPaneResizerBox.width / 2, contextPaneResizerBox.y + contextPaneResizerBox.height / 2);
    await page.mouse.down();
    await page.mouse.move(contextPaneResizerBox.x + contextPaneResizerBox.width / 2 - 52, contextPaneResizerBox.y + contextPaneResizerBox.height / 2, { steps: 8 });
    await page.mouse.up();
    const layoutAfterContextPaneResize = await collectWorkbenchLayoutMetrics(page);
    assert.ok(
      layoutAfterContextPaneResize.contextSidebarRect &&
        layoutAfterSessionPaneResize.contextSidebarRect &&
        layoutAfterContextPaneResize.contextSidebarRect.width > layoutAfterSessionPaneResize.contextSidebarRect.width + 36,
      `Dragging the context pane handle should visibly widen the right sidebar: ${JSON.stringify({ layoutAfterSessionPaneResize, layoutAfterContextPaneResize }, null, 2)}`
    );

    const titlebarMetrics = await collectTitlebarMetrics(page);
    assert.ok(titlebarMetrics, "Titlebar center metrics should be available");
    assert.ok(titlebarMetrics.delta < 8, `Workspace title is not visually centered: ${JSON.stringify(titlebarMetrics)}`);
    assert.equal(await page.locator('.titlebar-brand-mark .titlebar-brand-icon').count(), 1, 'Titlebar should render the Shotwright SVG mark instead of text glyphs');
    assert.equal(await page.locator('.titlebar [data-testid="toggle-session-sidebar"]').count(), 1, 'Sidebar toggles should live in the top titlebar');
    assert.equal(await page.locator('.chat-stage-header [data-testid="toggle-session-sidebar"]').count(), 0, 'Sidebar toggles should no longer sit in the session header');

    assert.equal(await page.locator('.timeline-panel').count(), 0, 'Right sidebar timeline should be removed entirely');

    assert.ok(await assistantMessage.locator("strong").count(), "Assistant Markdown should render strong text");
    assert.ok(await assistantMessage.locator("code").count(), "Assistant Markdown should render code spans or blocks");

    const inlineExecutionBlocks = page.locator('[data-testid="conversation-execution-block"]');
    assert.equal(await inlineExecutionBlocks.count(), 1, "Existing turn-scoped execution steps should render inline in the transcript");
    assert.ok(
      (await page.locator('.vscode-chat-tool-call').count()) >= 2,
      'Execution flow should restore the VS Code-style grouped tool-call rows in the transcript'
    );

    const initialExecutionMetrics = await collectExecutionBlockMetrics(page, 0);
    assert.ok(
      initialExecutionMetrics.groupCount === 2,
      `Inline execution block should rebuild multiple execution groups for a single turn: ${JSON.stringify(initialExecutionMetrics, null, 2)}`
    );

    const assistantExecutionPlacementMetrics = await collectAssistantExecutionPlacementMetrics(page);
    assert.equal(
      assistantExecutionPlacementMetrics.insideAssistant,
      true,
      `Execution flow should be anchored inside the assistant message shell instead of rendering above it: ${JSON.stringify(assistantExecutionPlacementMetrics, null, 2)}`
    );
    assert.ok(
      assistantExecutionPlacementMetrics.executionRect &&
        assistantExecutionPlacementMetrics.assistantMetaRect &&
        assistantExecutionPlacementMetrics.executionRect.top >= assistantExecutionPlacementMetrics.assistantMetaRect.bottom - 1,
      `Execution flow should start below the Shotwright avatar/meta row: ${JSON.stringify(assistantExecutionPlacementMetrics, null, 2)}`
    );
    assert.ok(
      assistantExecutionPlacementMetrics.executionRect &&
        assistantExecutionPlacementMetrics.assistantBodyRect &&
        assistantExecutionPlacementMetrics.executionRect.top <= assistantExecutionPlacementMetrics.assistantBodyRect.top,
      `Execution flow should appear before the assistant body copy, not above the whole assistant response: ${JSON.stringify(assistantExecutionPlacementMetrics, null, 2)}`
    );

    const firstExecutionGroup = inlineExecutionBlocks.first().locator('[data-testid="conversation-execution-group"]').nth(0);
    const secondExecutionGroup = inlineExecutionBlocks.first().locator('[data-testid="conversation-execution-group"]').nth(1);
    const collapsedFirstGroupMetrics = await collectExecutionGroupMetrics(page, 0, 0);
    assert.ok(
      collapsedFirstGroupMetrics.title?.includes('Preparing AE project'),
      `First execution group should use the assistant intent as its title: ${JSON.stringify(collapsedFirstGroupMetrics, null, 2)}`
    );
    assert.equal(
      collapsedFirstGroupMetrics.open,
      false,
      `Execution groups should start collapsed by default: ${JSON.stringify(collapsedFirstGroupMetrics, null, 2)}`
    );
    assert.ok(
      collapsedFirstGroupMetrics.preview?.includes('No uploaded projects') ||
        collapsedFirstGroupMetrics.preview?.includes('No files matched the pattern.'),
      `Collapsed group preview should summarize tool output instead of flattening the whole turn: ${JSON.stringify(collapsedFirstGroupMetrics, null, 2)}`
    );
    assert.equal(
      collapsedFirstGroupMetrics.cardDisplay,
      'none',
      `Collapsed execution groups should hide their detail card: ${JSON.stringify(collapsedFirstGroupMetrics, null, 2)}`
    );

    const collapsedSecondGroupMetrics = await collectExecutionGroupMetrics(page, 0, 1);
    assert.equal(
      collapsedSecondGroupMetrics.open,
      false,
      `Later execution groups should also start collapsed: ${JSON.stringify(collapsedSecondGroupMetrics, null, 2)}`
    );
    assert.ok(
      collapsedSecondGroupMetrics.title?.includes('Debugging JSX timeout'),
      `Failure group should keep the debugging intent title visible: ${JSON.stringify(collapsedSecondGroupMetrics, null, 2)}`
    );
    assert.ok(
      collapsedSecondGroupMetrics.preview?.includes('AfterFX JSX execution timed out.'),
      `Failure group preview should surface the runtime error: ${JSON.stringify(collapsedSecondGroupMetrics, null, 2)}`
    );

    await firstExecutionGroup.locator('[data-testid="conversation-execution-toggle"]').click();
    const expandedFirstGroupMetrics = await collectExecutionGroupMetrics(page, 0, 0);
    assert.equal(expandedFirstGroupMetrics.open, true, `Execution group should expand after clicking summary: ${JSON.stringify(expandedFirstGroupMetrics, null, 2)}`);
    assert.equal(expandedFirstGroupMetrics.cardDisplay, 'flex', `Expanded execution group should reveal its detail card: ${JSON.stringify(expandedFirstGroupMetrics, null, 2)}`);
    assert.ok(
      expandedFirstGroupMetrics.pills.includes('Succeeded') && expandedFirstGroupMetrics.pills.includes('2 substeps'),
      `Expanded execution group should surface grouped status and substep count badges: ${JSON.stringify(expandedFirstGroupMetrics, null, 2)}`
    );
    assert.equal(
      await firstExecutionGroup.locator('[data-testid="conversation-execution-step"]').count(),
      2,
      'First execution group should keep its nested step list intact'
    );

    const collapsedExecutionStepMetrics = await collectExecutionStepMetrics(page, 0, 0, 0);
    assert.equal(collapsedExecutionStepMetrics.open, false, `Execution step should start collapsed: ${JSON.stringify(collapsedExecutionStepMetrics, null, 2)}`);
    assert.equal(collapsedExecutionStepMetrics.bodyDisplay, 'none', `Collapsed execution step body should stay hidden: ${JSON.stringify(collapsedExecutionStepMetrics, null, 2)}`);
    assert.ok(
      collapsedExecutionStepMetrics.title?.includes('Inspect project structure'),
      `Execution step title should stay readable in the collapsed state: ${JSON.stringify(collapsedExecutionStepMetrics, null, 2)}`
    );

    await firstExecutionGroup.locator('[data-testid="conversation-execution-step-toggle"]').first().click();
    const expandedExecutionStepMetrics = await collectExecutionStepMetrics(page, 0, 0, 0);
    assert.equal(expandedExecutionStepMetrics.open, true, `Execution step should expand after clicking summary: ${JSON.stringify(expandedExecutionStepMetrics, null, 2)}`);
    assert.equal(expandedExecutionStepMetrics.bodyDisplay, 'flex', `Expanded execution step body should be visible: ${JSON.stringify(expandedExecutionStepMetrics, null, 2)}`);
    assert.ok(
      ((await firstExecutionGroup.textContent()) || "").includes("No uploaded projects"),
      'Expanded execution step should expose detailed tool output, not just the final assistant reply'
    );
    assert.ok(
      expandedExecutionStepMetrics.markdownBlockCount >= 1,
      `Expanded execution step should render structured event details through markdown blocks: ${JSON.stringify(expandedExecutionStepMetrics, null, 2)}`
    );
    assert.equal(
      expandedExecutionStepMetrics.rawPayloadBlockCount,
      0,
      `Expanded execution step should no longer expose a raw payload dump: ${JSON.stringify(expandedExecutionStepMetrics, null, 2)}`
    );

    await secondExecutionGroup.locator('[data-testid="conversation-execution-toggle"]').click();
    const expandedSecondGroupMetrics = await collectExecutionGroupMetrics(page, 0, 1);
    assert.equal(expandedSecondGroupMetrics.open, true, `Failure execution group should expand after clicking summary: ${JSON.stringify(expandedSecondGroupMetrics, null, 2)}`);
    assert.ok(
      expandedSecondGroupMetrics.pills.includes('Failed') && expandedSecondGroupMetrics.pills.includes('1 substeps'),
      `Failure execution group should keep a visible failed state badge: ${JSON.stringify(expandedSecondGroupMetrics, null, 2)}`
    );
    const failureExecutionStepMetrics = await collectExecutionStepMetrics(page, 0, 1, 0);
    assert.ok(
      failureExecutionStepMetrics.preview?.includes('AfterFX JSX execution timed out.'),
      `Failure step preview should preserve the JSX timeout message: ${JSON.stringify(failureExecutionStepMetrics, null, 2)}`
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

    assert.equal(await previewPanel.count(), 1, 'A compact render preview entry should appear when a render exists');
    assert.equal((await previewBadge.textContent())?.trim(), "MP4");
    assert.equal(await page.locator('[data-testid="session-context-sidebar"] .video-element').count(), 0, 'Right sidebar should not embed the video player directly anymore');
    await previewTrigger.click();
    await page.waitForSelector('[data-testid="render-preview-modal"]');
    const previewVideo = previewModal.locator('.video-element');
    assert.ok((await previewVideo.getAttribute('src'))?.includes(`/api/streams/renders/${primarySessionId}`), 'Preview modal should point at the direct mp4 route');
    await page.locator('[data-testid="render-preview-modal-close"]').click();
    await page.waitForFunction(() => !document.querySelector('[data-testid="render-preview-modal"]'));

    let overflowMetrics = await collectOverflowMetrics(page);
    assert.equal(
      overflowMetrics.offenders.length,
      0,
      `Initial session sidebar overflow detected: ${JSON.stringify(overflowMetrics, null, 2)}`
    );

    const initialPanelMetrics = await collectSessionWorkbenchPanelMetrics(page);
    assert.equal(initialPanelMetrics.settingsInComposer, true, `Session settings card should move into the composer shell: ${JSON.stringify(initialPanelMetrics, null, 2)}`);
    assert.equal(initialPanelMetrics.settingsInSidebar, false, `Right sidebar should no longer host the session settings card: ${JSON.stringify(initialPanelMetrics, null, 2)}`);
    assert.equal(initialPanelMetrics.settingsInComposerCard, true, `Session settings should live inside the composer card instead of floating above it: ${JSON.stringify(initialPanelMetrics, null, 2)}`);
    assert.equal(initialPanelMetrics.modelSelectText, "GPT-5.4 mini", "Model selector should show the full GPT-5.4 mini label");
    assert.equal(initialPanelMetrics.reasoningSelectText, "Extreme", `Reasoning selector should use compact option labels in the composer settings row: ${JSON.stringify(initialPanelMetrics, null, 2)}`);
    assert.ok(initialPanelMetrics.modelSelectRect && initialPanelMetrics.modelSelectRect.width >= 118, `Model selector is still too narrow: ${JSON.stringify(initialPanelMetrics, null, 2)}`);
    assert.ok(
      initialPanelMetrics.promptRect &&
        initialPanelMetrics.settingsCardRect &&
        initialPanelMetrics.settingsCardRect.top >= initialPanelMetrics.promptRect.bottom - 1,
      `Composer settings should sit in the bottom control strip below the prompt area: ${JSON.stringify(initialPanelMetrics, null, 2)}`
    );
    assert.ok(
      initialPanelMetrics.modelSelectRect &&
        initialPanelMetrics.reasoningSelectRect &&
        Math.abs(initialPanelMetrics.modelSelectRect.top - initialPanelMetrics.reasoningSelectRect.top) <= 2,
      `Composer settings selects should align on the same top edge: ${JSON.stringify(initialPanelMetrics, null, 2)}`
    );
    assert.ok(
      initialPanelMetrics.modelSelectRect &&
        initialPanelMetrics.reasoningSelectRect &&
        initialPanelMetrics.reasoningSelectRect.left - initialPanelMetrics.modelSelectRect.right >= 8,
      `Composer settings selects should keep enough horizontal gap instead of crowding together: ${JSON.stringify(initialPanelMetrics, null, 2)}`
    );
    assert.ok(
      initialPanelMetrics.selectWidthDelta !== null && initialPanelMetrics.selectWidthDelta <= 28,
      `Composer settings selects should stay visually balanced even in the compact footer row: ${JSON.stringify(initialPanelMetrics, null, 2)}`
    );
    assert.ok(
      initialPanelMetrics.saveButtonRect &&
        initialPanelMetrics.saveButtonRect.height >= 30 &&
        initialPanelMetrics.saveButtonRect.width <= 36,
      `Save control should collapse into a compact icon affordance inside the new composer toolbar: ${JSON.stringify(initialPanelMetrics, null, 2)}`
    );
    assert.equal(initialPanelMetrics.runtimeValueText, sessionStates[primarySessionId].copilot_session_id, `Runtime id should stay fully visible in the sidebar: ${JSON.stringify(initialPanelMetrics, null, 2)}`);
    assert.ok(initialPanelMetrics.runtimeValueRect && initialPanelMetrics.runtimeValueRect.height >= 30, `Runtime id row should have enough height to wrap long ids instead of truncating them: ${JSON.stringify(initialPanelMetrics, null, 2)}`);
    assert.equal(initialPanelMetrics.resourcesEmptyText, "No AEP files are bound yet.", "Resources empty state should stay visible");
    assert.equal(await page.locator('[data-testid="storyboard-gallery-trigger"]').count(), 5, "Reference media card should expose every storyboard image, not just the latest one");
    assert.equal(initialPanelMetrics.promptPaddingLeft, 16, `Prompt should keep the corrected left gutter inside the composer card: ${JSON.stringify(initialPanelMetrics, null, 2)}`);
    assert.equal(initialPanelMetrics.promptPaddingRight, 16, `Prompt should keep the corrected right gutter inside the composer card: ${JSON.stringify(initialPanelMetrics, null, 2)}`);
    assert.equal(initialPanelMetrics.footerPaddingLeft, initialPanelMetrics.promptPaddingLeft, `Composer footer controls should align with the prompt gutter: ${JSON.stringify(initialPanelMetrics, null, 2)}`);
    assert.equal(initialPanelMetrics.footerPaddingRight, initialPanelMetrics.promptPaddingRight, `Composer footer trailing controls should align with the prompt gutter: ${JSON.stringify(initialPanelMetrics, null, 2)}`);
    if (initialPanelMetrics.attachmentsPaddingLeft !== null || initialPanelMetrics.attachmentsPaddingRight !== null) {
      assert.equal(initialPanelMetrics.attachmentsPaddingLeft, initialPanelMetrics.promptPaddingLeft, `Composer attachment chips should align with the prompt gutter: ${JSON.stringify(initialPanelMetrics, null, 2)}`);
      assert.equal(initialPanelMetrics.attachmentsPaddingRight, initialPanelMetrics.promptPaddingRight, `Composer attachment chips should align with the prompt gutter on the trailing edge: ${JSON.stringify(initialPanelMetrics, null, 2)}`);
    }
    assert.ok(initialPanelMetrics.overviewGridRect, `Session overview should use the new compact summary grid: ${JSON.stringify(initialPanelMetrics, null, 2)}`);
    assert.ok(initialPanelMetrics.previewPanelRect && initialPanelMetrics.previewTriggerRect, `Render preview summary should expose a trigger instead of a fixed inline player: ${JSON.stringify(initialPanelMetrics, null, 2)}`);
    assert.equal(initialPanelMetrics.previewPanelFlexShrink, '0', `Render preview card should not collapse under sidebar pressure: ${JSON.stringify(initialPanelMetrics, null, 2)}`);
    if (initialPanelMetrics.containerPanelRect) {
      assert.equal(initialPanelMetrics.containerPanelFlexShrink, '0', `Container card should not shrink away when the sidebar fills up: ${JSON.stringify(initialPanelMetrics, null, 2)}`);
    }
    assert.ok(initialPanelMetrics.overviewRect && initialPanelMetrics.overviewRect.height <= 460, `Overview panel should stay concise instead of expanding into a long fact sheet: ${JSON.stringify(initialPanelMetrics, null, 2)}`);
    const referenceMediaOverflowMetrics = await page.evaluate(() => {
      const card = document.querySelector('.reference-media-card');
      const strip = document.querySelector('.reference-media-storyboard-strip');

      if (!card || !strip) {
        return null;
      }

      return {
        cardClientWidth: card.clientWidth,
        cardScrollWidth: card.scrollWidth,
        stripClientWidth: strip.clientWidth,
        stripScrollWidth: strip.scrollWidth,
      };
    });
    assert.ok(referenceMediaOverflowMetrics, 'Reference media metrics should be readable from the page');
    assert.ok(
      referenceMediaOverflowMetrics.cardScrollWidth <= referenceMediaOverflowMetrics.cardClientWidth + 1,
      `Reference media card should wrap long storyboard filenames instead of overflowing horizontally: ${JSON.stringify(referenceMediaOverflowMetrics, null, 2)}`
    );
    assert.ok(
      referenceMediaOverflowMetrics.stripScrollWidth > referenceMediaOverflowMetrics.stripClientWidth,
      `Storyboard gallery should switch to horizontal scrolling when there are many images: ${JSON.stringify(referenceMediaOverflowMetrics, null, 2)}`
    );

    const lastStoryboardTrigger = page.locator('[data-testid="storyboard-gallery-trigger"]').last();
    await lastStoryboardTrigger.scrollIntoViewIfNeeded();
    await lastStoryboardTrigger.click();
    await page.locator('[data-testid="media-preview-modal"]').waitFor();
    assert.equal(
      (await page.locator('.media-preview-panel h3').textContent())?.trim(),
      storyboardsBySession[primarySessionId][4].filename,
      'The preview modal should open the selected storyboard image, not only the latest one'
    );
    await page.locator('[data-testid="media-preview-modal-close"]').click();

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

    await page.waitForFunction(() => {
      const text = document.body.textContent || '';
      return text.includes('Collected composition details for streamed reply 1.') && !text.includes('Streaming reply 1 ready.');
    });

    await page.waitForFunction(() => document.body.textContent.includes("Streaming reply 1 ready."));
    await page.waitForFunction(() => !document.querySelector('[data-testid="composer-status"]'));
    await page.waitForFunction(() => document.querySelectorAll('[data-testid="conversation-execution-block"]').length >= 2);

    const postSendMeta = (await page.locator(".chat-stage-meta").textContent()) || "";
    assert.ok(postSendMeta.includes("Idle"), "Header status chip should return to idle after the streamed response completes");

    await promptInput.fill("Send a second streamed confirmation.");
    await sendButton.click();
    await page.waitForSelector('[data-testid="composer-status"]');
    await page.waitForFunction(() => document.body.textContent.includes("Streaming reply 2 ready."));
    await page.waitForFunction(() => !document.querySelector('[data-testid="composer-status"]'));
    await page.waitForFunction(() => document.querySelectorAll('[data-testid="conversation-execution-block"]').length >= 3);

    const transcriptText = (await page.locator(".chat-transcript").textContent()) || "";
    assert.ok(transcriptText.includes("Streaming reply 1 ready."), "First streamed response should remain in the transcript");
    assert.ok(transcriptText.includes("Streaming reply 2 ready."), "Second streamed response should render without requiring a page refresh");
    assert.ok(transcriptText.includes("Collected composition details for streamed reply 1."), "First streamed turn should surface its grouped tool result inline in the transcript");
    assert.ok(transcriptText.includes("Collected composition details for streamed reply 2."), "Second streamed turn should also append its grouped tool result inline");

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
    assert.equal(await page.locator('[data-testid="render-preview-panel"]').count(), 0, 'Sessions without a render result should not show the render preview card');
    assert.equal(await page.locator('[data-testid="render-preview-modal"]').count(), 0, 'Preview modal should stay absent when no render exists');

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
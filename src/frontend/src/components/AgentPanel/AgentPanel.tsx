import { ChangeEvent, KeyboardEvent, useEffect, useMemo, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { useLocation, useNavigate, useParams } from "react-router-dom";
import {
  createSession,
  deleteSession,
  exportProject,
  getAgentContext,
  getAgentEvents,
  getAgentMessages,
  getCopilotModelOptions,
  getSessions,
  openAgentSessionStream,
  sendChatTurn,
  stopContainer,
  updateSession,
  uploadProject,
} from "../../services/api";
import { AgentContext, ChatMessage, CopilotModelOption, ProjectInfo, ReasoningEffort, Session, SessionEvent } from "../../types";
import { TranslationCopy, useI18n } from "../../i18n";
import VideoPlayer from "../VideoPlayer/VideoPlayer";
import ContainerManager from "../ContainerManager/ContainerManager";
import "./AgentPanel.css";

type UiErrorKey =
  | "failedLoadSessions"
  | "failedLoadSessionData"
  | "failedCreateSession"
  | "failedLoadModelOptions"
  | "failedRenameSession"
  | "failedSendPrompt"
  | "failedSaveSessionSettings"
  | "uploadFailed"
  | "exportFailed"
  | "failedStopContainer"
  | "failedDeleteSession";

type UiError =
  | { type: "api"; message: string }
  | { type: "key"; key: UiErrorKey };

type MetaChip = {
  key: string;
  label: string;
  value: string;
  tone: "primary" | "accent" | "neutral" | "muted" | "danger" | "success";
};

function buildUiError(err: any, fallbackKey: UiErrorKey): UiError {
  const detail = err?.response?.data?.detail;
  if (typeof detail === "string" && detail.trim()) {
    return { type: "api", message: detail };
  }

  return { type: "key", key: fallbackKey };
}

function getUiErrorMessage(error: UiError | null, copy: TranslationCopy) {
  if (!error) return null;
  return error.type === "api" ? error.message : copy.errors[error.key];
}

function parseDateValue(value: string) {
  const normalizedValue = /(?:[zZ]|[+-]\d{2}:\d{2})$/.test(value) ? value : `${value}Z`;
  return new Date(normalizedValue);
}

function getPreferredTimeZone(locale: string) {
  if (locale === "zh-CN") return "Asia/Shanghai";
  return Intl.DateTimeFormat().resolvedOptions().timeZone;
}

function formatDateTime(value: string | null | undefined, locale: string, fallback: string) {
  if (!value) return fallback;

  const date = parseDateValue(value);
  if (Number.isNaN(date.getTime())) return value;

  return new Intl.DateTimeFormat(locale, {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
    timeZone: getPreferredTimeZone(locale),
  }).format(date);
}

function formatClockTime(value: string | null | undefined, locale: string, fallback: string) {
  if (!value) return fallback;

  const date = parseDateValue(value);
  if (Number.isNaN(date.getTime())) return value;

  return new Intl.DateTimeFormat(locale, {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
    timeZone: getPreferredTimeZone(locale),
  }).format(date);
}

function basename(value: string | null | undefined, fallback: string) {
  if (!value) return fallback;

  const parts = value.split(/[\\/]/);
  return parts[parts.length - 1] || value;
}

function hasEventData(event: SessionEvent) {
  return Boolean(event.data && Object.keys(event.data).length);
}

function formatTimelineEventLabel(value: string) {
  return value
    .split(/[._]+/)
    .filter(Boolean)
    .map((segment) => segment.charAt(0).toUpperCase() + segment.slice(1).toLowerCase())
    .join(" ");
}

function getTimelinePreviewText(event: SessionEvent) {
  return event.summary === event.type ? "" : event.summary;
}

function getTimelineExpandedSummary(event: SessionEvent) {
  return event.summary === event.type ? formatTimelineEventLabel(event.type) : event.summary;
}

function getReasoningSelectLabel(effort: ReasoningEffort, locale: string, copy: TranslationCopy) {
  if (locale === "en-US") {
    return {
      low: "Low",
      medium: "Medium",
      high: "High",
      xhigh: "Extreme",
    }[effort];
  }

  return copy.common.reasoningEfforts[effort];
}

function buildModelFallbackOption(session: Session): CopilotModelOption {
  return {
    id: session.copilot_model,
    name: session.copilot_model,
    supports_reasoning_effort: Boolean(session.copilot_reasoning_effort),
    supported_reasoning_efforts: session.copilot_reasoning_effort ? [session.copilot_reasoning_effort] : [],
    default_reasoning_effort: session.copilot_reasoning_effort,
  };
}

type OptimisticTurn = {
  sessionId: string;
  baselineCount: number;
  userMessage: ChatMessage;
  assistantMessage: ChatMessage;
};

function buildOptimisticMessage(
  sessionId: string,
  role: ChatMessage["role"],
  content: string,
  metadata: Record<string, unknown> = {}
): ChatMessage {
  return {
    _id: `optimistic-${role}-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
    session_id: sessionId,
    role,
    content,
    created_at: new Date().toISOString(),
    metadata,
  };
}

function isStreamingMessage(message: ChatMessage) {
  return Boolean(message.metadata?.["streaming"]);
}

function hasRenderableMessageContent(message: ChatMessage) {
  return Boolean(message.content.trim());
}

function upsertMessage(messages: ChatMessage[], nextMessage: ChatMessage) {
  const existingIndex = messages.findIndex((message) => message._id === nextMessage._id);
  if (existingIndex >= 0) {
    const nextMessages = [...messages];
    nextMessages[existingIndex] = nextMessage;
    return nextMessages;
  }

  return [...messages, nextMessage].sort(
    (left, right) => new Date(left.created_at).getTime() - new Date(right.created_at).getTime()
  );
}

function removeMessage(messages: ChatMessage[], messageId: string) {
  return messages.filter((message) => message._id !== messageId);
}

function upsertTimelineEvent(events: SessionEvent[], nextEvent: SessionEvent) {
  const existingIndex = events.findIndex((event) => event._id === nextEvent._id);
  if (existingIndex >= 0) {
    const nextEvents = [...events];
    nextEvents[existingIndex] = nextEvent;
    return nextEvents;
  }

  return [...events, nextEvent].sort(
    (left, right) => new Date(left.created_at).getTime() - new Date(right.created_at).getTime()
  );
}

function upsertSessionRecord(sessions: Session[], nextSession: Session) {
  const existingIndex = sessions.findIndex((session) => session._id === nextSession._id);
  if (existingIndex >= 0) {
    const nextSessions = [...sessions];
    nextSessions[existingIndex] = nextSession;
    return nextSessions;
  }

  return [...sessions, nextSession].sort(
    (left, right) => new Date(right.created_at).getTime() - new Date(left.created_at).getTime()
  );
}

function buildRenderUrl(sessionId: string, latestRenderPath: string | null | undefined) {
  return latestRenderPath ? `/api/streams/renders/${sessionId}` : null;
}

export default function AgentPanel() {
  const navigate = useNavigate();
  const location = useLocation();
  const { sessionId: routedSessionId } = useParams<{ sessionId?: string }>();
  const { copy, locale } = useI18n();
  const [sessions, setSessions] = useState<Session[]>([]);
  const [currentSession, setCurrentSession] = useState<Session | null>(null);
  const [context, setContext] = useState<AgentContext | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [events, setEvents] = useState<SessionEvent[]>([]);
  const [optimisticTurn, setOptimisticTurn] = useState<OptimisticTurn | null>(null);
  const [prompt, setPrompt] = useState("");
  const [sendingSessionId, setSendingSessionId] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);
  const [modelOptions, setModelOptions] = useState<CopilotModelOption[]>([]);
  const [modelOptionsLoading, setModelOptionsLoading] = useState(false);
  const [draftModel, setDraftModel] = useState("");
  const [draftReasoning, setDraftReasoning] = useState<ReasoningEffort | null>(null);
  const [expandedTimelineEventIds, setExpandedTimelineEventIds] = useState<string[]>([]);
  const [savingSessionSettings, setSavingSessionSettings] = useState(false);
  const [sessionsError, setSessionsError] = useState<UiError | null>(null);
  const [panelError, setPanelError] = useState<UiError | null>(null);
  const [sessionSettingsError, setSessionSettingsError] = useState<UiError | null>(null);
  const [editingSessionName, setEditingSessionName] = useState(false);
  const [draftSessionName, setDraftSessionName] = useState("");
  const [savingSessionName, setSavingSessionName] = useState(false);
  const messageEndRef = useRef<HTMLDivElement | null>(null);
  const renameInputRef = useRef<HTMLInputElement | null>(null);
  const activeSessionIdRef = useRef<string | null>(null);
  const streamRef = useRef<EventSource | null>(null);
  const sessionStatusLabels = copy.status.session;
  const projectStatusLabels = copy.status.project;
  const containerStatusLabels = copy.status.container;
  const starterPrompts = copy.agent.prompts;
  const sessionsErrorMessage = getUiErrorMessage(sessionsError, copy);
  const panelErrorMessage = getUiErrorMessage(panelError, copy);
  const sessionSettingsErrorMessage = getUiErrorMessage(sessionSettingsError, copy);

  const sortedEvents = useMemo(
    () => [...events].sort((left, right) => new Date(left.created_at).getTime() - new Date(right.created_at).getTime()),
    [events]
  );

  const activeProject = useMemo(() => {
    if (!context?.projects.length || !context?.session.active_project_id) return null;

    return context.projects.find((project) => project._id === context.session.active_project_id) ?? null;
  }, [context?.projects, context?.session.active_project_id]);

  const sessionModelOptions = useMemo(() => {
    if (!currentSession) return modelOptions;
    if (modelOptions.some((option) => option.id === currentSession.copilot_model)) return modelOptions;
    return [buildModelFallbackOption(currentSession), ...modelOptions];
  }, [currentSession, modelOptions]);

  const selectedModelOption = useMemo(() => {
    if (!draftModel) return null;
    return sessionModelOptions.find((option) => option.id === draftModel) ?? null;
  }, [draftModel, sessionModelOptions]);

  const currentModelLabel = useMemo(() => {
    if (!currentSession) return copy.common.copilot;
    return sessionModelOptions.find((option) => option.id === currentSession.copilot_model)?.name || currentSession.copilot_model;
  }, [copy.common.copilot, currentSession, sessionModelOptions]);

  const sending = Boolean(currentSession && sendingSessionId === currentSession._id);
  const sessionStatus = context?.session.status || currentSession?.status || "idle";
  const isResponding = sending || sessionStatus === "running";
  const visibleMessages = useMemo(() => {
    if (!currentSession || !optimisticTurn || optimisticTurn.sessionId !== currentSession._id) {
      return messages;
    }

    if (messages.length >= optimisticTurn.baselineCount + 2) {
      return messages;
    }

    return [...messages, optimisticTurn.userMessage, optimisticTurn.assistantMessage];
  }, [currentSession, messages, optimisticTurn]);

  const latestAssistantMessage = useMemo(() => {
    for (let index = visibleMessages.length - 1; index >= 0; index -= 1) {
      const message = visibleMessages[index];
      if (message.role === "assistant" && (hasRenderableMessageContent(message) || isStreamingMessage(message))) {
        return message;
      }
    }

    return null;
  }, [visibleMessages]);

  const lastVisibleMessageContent = visibleMessages.length ? visibleMessages[visibleMessages.length - 1].content : "";
  const selectedReasoningOptions = selectedModelOption?.supported_reasoning_efforts ?? [];
  const reasoningSupported = Boolean(selectedModelOption?.supports_reasoning_effort && selectedReasoningOptions.length);
  const effectiveDraftReasoning = reasoningSupported ? draftReasoning : null;
  const previewVideoSrc = context?.latest_render_url || context?.latest_stream_url || null;
  const previewVideoFormat = context?.latest_render_url ? "mp4" : context?.latest_stream_url ? "hls" : null;
  const sessionSettingsDirty = Boolean(currentSession) && (
    draftModel !== (currentSession?.copilot_model ?? "") ||
    effectiveDraftReasoning !== (currentSession?.copilot_reasoning_effort ?? null)
  );
  const showStarterCards = Boolean(currentSession) && visibleMessages.length === 0;
  const showComposerSuggestions = Boolean(currentSession) && visibleMessages.length > 0;
  const metaChips = useMemo((): MetaChip[] => {
    if (!currentSession) {
      return [
        {
          key: "session",
          label: copy.agent.sessionPanelFields.status,
          value: copy.agent.noActiveSession,
          tone: "muted",
        },
      ];
    }

    const chips: MetaChip[] = [];

    chips.push({
      key: "model",
      label: copy.agent.sessionSettingsFields.model,
      value: currentModelLabel,
      tone: "primary",
    });

    if (currentSession.copilot_reasoning_effort) {
      chips.push({
        key: "reasoning",
        label: copy.agent.sessionSettingsFields.reasoning,
        value: copy.common.reasoningEfforts[currentSession.copilot_reasoning_effort],
        tone: "accent",
      });
    }

    chips.push({
      key: "status",
      label: copy.agent.sessionPanelFields.status,
      value: sessionStatusLabels[sessionStatus],
      tone: sessionStatus === "error" ? "danger" : sessionStatus === "running" ? "success" : "neutral",
    });

    chips.push({
      key: "project",
      label: copy.agent.sessionPanelFields.activeProject,
      value: activeProject?.filename || copy.common.notSpecified,
      tone: "muted",
    });

    if (context?.container) {
      chips.push({
        key: "container",
        label: copy.agent.containerPrefix,
        value: containerStatusLabels[context.container.status],
        tone: "neutral",
      });
    }

    return chips;
  }, [
    activeProject?.filename,
    containerStatusLabels,
    context?.container,
    copy.agent.containerPrefix,
    copy.agent.noActiveSession,
    copy.agent.sessionPanelFields.activeProject,
    copy.agent.sessionPanelFields.status,
    copy.agent.sessionSettingsFields.model,
    copy.agent.sessionSettingsFields.reasoning,
    copy.common.notSpecified,
    copy.common.reasoningEfforts,
    currentModelLabel,
    currentSession,
    sessionStatus,
    sessionStatusLabels,
  ]);

  const handlePromptKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if ((event.ctrlKey || event.metaKey) && event.key === "Enter") {
      event.preventDefault();
      handleSend();
    }
  };

  const navigateToSession = (sessionId: string | null, replace = false) => {
    const targetPath = sessionId ? `/sessions/${sessionId}` : "/";
    if (location.pathname !== targetPath) {
      navigate(targetPath, { replace });
    }
  };

  const syncSessionRecord = (session: Session) => {
    setCurrentSession((previous) => (previous && previous._id === session._id ? session : previous));
    setSessions((previous) => upsertSessionRecord(previous, session));
    setContext((previous) => {
      if (!previous || previous.session._id !== session._id) {
        return previous;
      }

      return {
        ...previous,
        session,
        latest_render_path: session.latest_render_path,
        latest_render_url: buildRenderUrl(session._id, session.latest_render_path),
        latest_stream_url: session.latest_stream_url,
      };
    });
  };

  const fetchSessions = async () => {
    try {
      const res = await getSessions();
      setSessions(res.data);
      setSessionsError(null);
      if (res.data.length === 0) {
        setCurrentSession(null);
        setOptimisticTurn(null);
        setContext(null);
        setMessages([]);
        setEvents([]);
        return;
      }
    } catch (err: any) {
      setSessionsError(buildUiError(err, "failedLoadSessions"));
    }
  };

  const loadSessionContext = async (sessionId: string) => {
    const contextRes = await getAgentContext(sessionId);
    if (activeSessionIdRef.current !== sessionId) return;

    setContext(contextRes.data);
    syncSessionRecord(contextRes.data.session);
    setPanelError(null);
  };

  const loadModelOptions = async () => {
    setModelOptionsLoading(true);
    try {
      const res = await getCopilotModelOptions();
      setModelOptions(res.data);
      setSessionSettingsError(null);
    } catch (err: any) {
      setSessionSettingsError(buildUiError(err, "failedLoadModelOptions"));
    } finally {
      setModelOptionsLoading(false);
    }
  };

  const loadCurrentSession = async (sessionId: string) => {
    const [contextRes, messageRes, eventRes] = await Promise.all([
      getAgentContext(sessionId),
      getAgentMessages(sessionId),
      getAgentEvents(sessionId),
    ]);
    if (activeSessionIdRef.current !== sessionId) return;

    setContext(contextRes.data);
    setMessages(messageRes.data);
    setEvents(eventRes.data);
    setOptimisticTurn((previous) => {
      if (!previous || previous.sessionId !== sessionId) {
        return previous;
      }

      return messageRes.data.length >= previous.baselineCount + 2 ? null : previous;
    });
    syncSessionRecord(contextRes.data.session);
    setPanelError(null);
  };

  useEffect(() => {
    fetchSessions();
    loadModelOptions();
  }, []);

  useEffect(() => {
    if (!sessions.length) {
      if (routedSessionId) {
        navigateToSession(null, true);
      }
      return;
    }

    const routedSession = routedSessionId
      ? sessions.find((session) => session._id === routedSessionId) ?? null
      : null;
    const preservedSession = currentSession
      ? sessions.find((session) => session._id === currentSession._id) ?? null
      : null;
    const nextSession = routedSession ?? preservedSession ?? sessions[0];

    setCurrentSession(nextSession);

    if ((!routedSessionId || !routedSession) && nextSession) {
      navigateToSession(nextSession._id, true);
    }
  }, [sessions, routedSessionId]);

  useEffect(() => {
    if (!currentSession) {
      setDraftModel("");
      setDraftReasoning(null);
      setDraftSessionName("");
      setEditingSessionName(false);
      setSessionSettingsError(null);
      return;
    }

    setDraftModel(currentSession.copilot_model);
    setDraftReasoning(currentSession.copilot_reasoning_effort);
    setDraftSessionName(currentSession.name);
    setEditingSessionName(false);
    setSessionSettingsError(null);
  }, [currentSession?._id, currentSession?.copilot_model, currentSession?.copilot_reasoning_effort, currentSession?.name]);

  useEffect(() => {
    if (!editingSessionName) return;

    renameInputRef.current?.focus();
    renameInputRef.current?.select();
  }, [editingSessionName, currentSession?._id]);

  useEffect(() => {
    activeSessionIdRef.current = currentSession?._id ?? null;
  }, [currentSession?._id]);

  useEffect(() => {
    if (!visibleMessages.length) return;

    messageEndRef.current?.scrollIntoView({ block: "end" });
  }, [visibleMessages.length, lastVisibleMessageContent]);

  useEffect(() => {
    setExpandedTimelineEventIds([]);
  }, [currentSession?._id]);

  useEffect(() => {
    setOptimisticTurn((previous) => {
      if (!previous) return null;
      return previous.sessionId === currentSession?._id ? previous : null;
    });
  }, [currentSession?._id]);

  useEffect(() => {
    streamRef.current?.close();
    streamRef.current = null;

    if (!currentSession) {
      setContext(null);
      setMessages([]);
      setEvents([]);
      setPanelError(null);
      activeSessionIdRef.current = null;
      return;
    }

    const sessionId = currentSession._id;
    activeSessionIdRef.current = sessionId;

    loadCurrentSession(sessionId).catch((err) => {
      setPanelError(buildUiError(err, "failedLoadSessionData"));
    });

    const stream = openAgentSessionStream(sessionId, {
      onSessionUpdated: (session) => {
        if (activeSessionIdRef.current !== sessionId) return;
        syncSessionRecord(session);
        if (session.status !== "running") {
          setSendingSessionId((previous) => (previous === sessionId ? null : previous));
        }
      },
      onMessageUpsert: (message) => {
        if (activeSessionIdRef.current !== sessionId) return;
        setMessages((previous) => upsertMessage(previous, message));
      },
      onMessageDeleted: (payload) => {
        if (activeSessionIdRef.current !== sessionId) return;
        setMessages((previous) => removeMessage(previous, payload.message_id));
        setOptimisticTurn((previous) => (previous?.sessionId === sessionId ? null : previous));
      },
      onTimelineEvent: (event) => {
        if (activeSessionIdRef.current !== sessionId) return;
        setEvents((previous) => upsertTimelineEvent(previous, event));
      },
      onContextRefresh: () => {
        if (activeSessionIdRef.current !== sessionId) return;
        void loadSessionContext(sessionId).catch((err) => {
          setPanelError(buildUiError(err, "failedLoadSessionData"));
        });
      },
    });
    streamRef.current = stream;

    return () => {
      if (streamRef.current === stream) {
        streamRef.current = null;
      }
      stream.close();
    };
  }, [currentSession?._id]);

  const createNewSession = async () => {
    try {
      const name = `${copy.common.sessionPrefix} ${sessions.length + 1}`;
      const res = await createSession(name);
      setSessions((prev: Session[]) => [res.data, ...prev]);
      setCurrentSession(res.data);
      setOptimisticTurn(null);
      navigateToSession(res.data._id);
      setSessionsError(null);
      return res.data;
    } catch (err: any) {
      setSessionsError(buildUiError(err, "failedCreateSession"));
      return null;
    }
  };

  const toggleTimelineEvent = (eventId: string) => {
    setExpandedTimelineEventIds((previous) =>
      previous.includes(eventId) ? previous.filter((id) => id !== eventId) : [...previous, eventId]
    );
  };

  const handleNewSession = async () => {
    await createNewSession();
  };

  const handleStarterPrompt = async (starterPrompt: string) => {
    if (!currentSession) {
      const session = await createNewSession();
      if (!session) return;
    }

    setPrompt(starterPrompt);
  };

  const handleSelectSession = (session: Session) => {
    setCurrentSession(session);
    setPanelError(null);
    navigateToSession(session._id);
  };

  const handleStartRenameSession = () => {
    if (!currentSession) return;

    setDraftSessionName(currentSession.name);
    setEditingSessionName(true);
    setPanelError(null);
  };

  const handleCancelRenameSession = () => {
    setDraftSessionName(currentSession?.name ?? "");
    setEditingSessionName(false);
  };

  const handleRenameSession = async () => {
    if (!currentSession) return;

    const nextName = draftSessionName.trim();
    if (!nextName) return;
    if (nextName === currentSession.name) {
      setEditingSessionName(false);
      return;
    }

    setSavingSessionName(true);
    try {
      const response = await updateSession(currentSession._id, { name: nextName });
      syncSessionRecord(response.data);
      setDraftSessionName(response.data.name);
      setEditingSessionName(false);
      setPanelError(null);
    } catch (err: any) {
      setPanelError(buildUiError(err, "failedRenameSession"));
    } finally {
      setSavingSessionName(false);
    }
  };

  const handleRenameSessionKeyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    if (event.key === "Enter") {
      event.preventDefault();
      void handleRenameSession();
      return;
    }

    if (event.key === "Escape") {
      event.preventDefault();
      handleCancelRenameSession();
    }
  };

  const handleSend = () => {
    if (!currentSession || !prompt.trim() || sending) return;

    const sessionId = currentSession._id;
    const content = prompt.trim();
    const optimisticUserMessage = buildOptimisticMessage(sessionId, "user", content);
    const optimisticAssistantMessage = buildOptimisticMessage(sessionId, "assistant", "", {
      streaming: true,
      state: "pending",
    });

    setOptimisticTurn({
      sessionId,
      baselineCount: messages.length,
      userMessage: optimisticUserMessage,
      assistantMessage: optimisticAssistantMessage,
    });
    setSendingSessionId(sessionId);
    setPrompt("");
    setPanelError(null);
    syncSessionRecord({
      ...currentSession,
      status: "running",
      last_error: null,
      updated_at: new Date().toISOString(),
    });

    void sendChatTurn(sessionId, content)
      .then(() => {
        setPanelError(null);
        void Promise.allSettled([loadCurrentSession(sessionId), fetchSessions()]);
      })
      .catch(async (err: any) => {
        setPanelError(buildUiError(err, "failedSendPrompt"));
        setOptimisticTurn((previous) => (previous?.sessionId === sessionId ? null : previous));
        await Promise.allSettled([loadCurrentSession(sessionId), fetchSessions()]);
      })
      .finally(() => {
        setSendingSessionId((previous) => (previous === sessionId ? null : previous));
      });
  };
  const handleModelChange = (event: ChangeEvent<HTMLSelectElement>) => {
    const nextModel = event.target.value;
    const nextModelOption = sessionModelOptions.find((option) => option.id === nextModel) ?? null;

    let nextReasoning: ReasoningEffort | null = draftReasoning;
    if (!nextModelOption?.supports_reasoning_effort || !nextModelOption.supported_reasoning_efforts.length) {
      nextReasoning = null;
    } else if (!nextReasoning || !nextModelOption.supported_reasoning_efforts.includes(nextReasoning)) {
      nextReasoning = nextModelOption.default_reasoning_effort ?? nextModelOption.supported_reasoning_efforts[0] ?? null;
    }

    setDraftModel(nextModel);
    setDraftReasoning(nextReasoning);
    setSessionSettingsError(null);
  };

  const handleReasoningChange = (event: ChangeEvent<HTMLSelectElement>) => {
    const nextValue = event.target.value as ReasoningEffort | "";
    setDraftReasoning(nextValue || null);
    setSessionSettingsError(null);
  };

  const handleSaveSessionSettings = async () => {
    if (!currentSession || !draftModel) return;

    setSavingSessionSettings(true);
    try {
      const response = await updateSession(currentSession._id, {
        copilot_model: draftModel,
        copilot_reasoning_effort: effectiveDraftReasoning,
      });
      setCurrentSession(response.data);
      await Promise.all([loadCurrentSession(response.data._id), fetchSessions()]);
      setSessionSettingsError(null);
    } catch (err: any) {
      setSessionSettingsError(buildUiError(err, "failedSaveSessionSettings"));
    } finally {
      setSavingSessionSettings(false);
    }
  };

  const handleUpload = async (file: File) => {
    if (!currentSession) return;
    setUploading(true);
    try {
      await uploadProject(currentSession._id, file);
      await loadCurrentSession(currentSession._id);
      await fetchSessions();
      setPanelError(null);
    } catch (err: any) {
      setPanelError(buildUiError(err, "uploadFailed"));
    }
    setUploading(false);
  };

  const handleDownload = async (project: ProjectInfo) => {
    if (!currentSession) return;
    try {
      const res = await exportProject(currentSession._id, project._id);
      const blob = new Blob([res.data], { type: "application/zip" });
      const href = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = href;
      anchor.download = `${project.filename.replace(/\.zip$/i, "")}-export.zip`;
      anchor.click();
      URL.revokeObjectURL(href);
      setPanelError(null);
    } catch (err: any) {
      setPanelError(buildUiError(err, "exportFailed"));
    }
  };

  const handleStopContainer = async (containerId: string) => {
    try {
      await stopContainer(containerId);
      if (currentSession) {
        await loadCurrentSession(currentSession._id);
        await fetchSessions();
      }
      setPanelError(null);
    } catch (err: any) {
      setPanelError(buildUiError(err, "failedStopContainer"));
    }
  };

  const handleDeleteSession = async (sessionId: string) => {
    try {
      await deleteSession(sessionId);
      const remaining = sessions.filter((session: Session) => session._id !== sessionId);
      setSessions(remaining);
      setCurrentSession(remaining[0] ?? null);
      setOptimisticTurn(null);
      navigateToSession(remaining[0]?._id ?? null);
      setSessionsError(null);
    } catch (err: any) {
      setSessionsError(buildUiError(err, "failedDeleteSession"));
    }
  };

  return (
    <div className="agent-workbench">
      <aside className="secondary-sidebar">
        <div className="sidebar-section">
          <div className="sidebar-section-header">
            <span>{copy.agent.sidebarTitle}</span>
            <span>{sessions.length}</span>
          </div>
          <button className="ghost-button sidebar-new-button" onClick={handleNewSession}>
            {copy.common.newChat}
          </button>
          {sessionsErrorMessage && <div className="sidebar-alert">{sessionsErrorMessage}</div>}

          <ul className="session-list">
            {sessions.length ? (
              sessions.map((session) => (
                <li key={session._id}>
                  <button
                    type="button"
                    data-testid="session-list-item"
                    className={`session-item ${currentSession?._id === session._id ? "active" : ""}`}
                    onClick={() => handleSelectSession(session)}
                  >
                    <div className="session-item-top">
                      <div className="session-title-group">
                        <span className={`status-dot status-${session.status}`} />
                        <span className="session-name">{session.name}</span>
                      </div>
                      <span className={`status-badge status-${session.status}`}>{sessionStatusLabels[session.status]}</span>
                    </div>
                    <div className="session-footline">
                      <span>{session.active_project_id ? copy.common.yesBoundProject : copy.common.noProjectUploaded}</span>
                      <time>{formatDateTime(session.updated_at, locale, copy.common.notStarted)}</time>
                    </div>
                  </button>
                </li>
              ))
            ) : (
              <li className="sidebar-empty">{copy.agent.sidebarEmpty}</li>
            )}
          </ul>
        </div>
      </aside>

      <section className="chat-stage">
        <header className="chat-stage-header">
          <div className="chat-stage-heading">
            <span className="eyebrow">{copy.agent.eyebrow}</span>
            {currentSession && editingSessionName ? (
              <input
                ref={renameInputRef}
                className="session-title-input"
                data-testid="session-rename-input"
                value={draftSessionName}
                onChange={(event: ChangeEvent<HTMLInputElement>) => setDraftSessionName(event.target.value)}
                onKeyDown={handleRenameSessionKeyDown}
                disabled={savingSessionName}
              />
            ) : (
              <h1>{currentSession ? currentSession.name : copy.agent.title.empty}</h1>
            )}
          </div>
          <div className="chat-stage-actions">
            {currentSession && !editingSessionName && (
              <button
                type="button"
                className="ghost-button chat-stage-action-button"
                data-testid="session-rename-trigger"
                onClick={handleStartRenameSession}
              >
                {copy.common.rename}
              </button>
            )}
            {currentSession && editingSessionName && (
              <>
                <button
                  type="button"
                  className="btn-primary chat-stage-action-button"
                  onClick={() => void handleRenameSession()}
                  disabled={savingSessionName || !draftSessionName.trim()}
                >
                  {savingSessionName ? copy.common.saving : copy.common.confirm}
                </button>
                <button
                  type="button"
                  className="ghost-button chat-stage-action-button"
                  onClick={handleCancelRenameSession}
                  disabled={savingSessionName}
                >
                  {copy.common.cancel}
                </button>
              </>
            )}
            <label className="toolbar-button chat-stage-action-button file-action">
              {uploading ? copy.common.uploading : copy.common.uploadProject}
              <input
                type="file"
                accept=".zip"
                hidden
                onChange={(e: ChangeEvent<HTMLInputElement>) => e.target.files?.[0] && handleUpload(e.target.files[0])}
              />
            </label>
            {currentSession && (
              <button className="btn-danger chat-stage-action-button" onClick={() => handleDeleteSession(currentSession._id)}>
                {copy.common.deleteSession}
              </button>
            )}
          </div>
        </header>

        <div className="chat-stage-meta">
          {metaChips.map((chip) => (
            <span key={chip.key} className={`meta-chip tone-${chip.tone}`}>
              <span className="meta-chip-label">{chip.label}</span>
              <span className="meta-chip-value">{chip.value}</span>
            </span>
          ))}
        </div>

        <div className="chat-transcript">
          {panelErrorMessage && <div className="notice-banner transcript-notice">{panelErrorMessage}</div>}
          {currentSession ? (
            visibleMessages.length ? (
              visibleMessages.map((message) => {
                const streaming = isStreamingMessage(message);
                const hasContent = hasRenderableMessageContent(message);

                return (
                  <article key={message._id} className={`chat-message role-${message.role}`}>
                    <div className="chat-message-meta">
                      <span className="chat-message-author">{message.role === "user" ? copy.agent.you : copy.agent.assistant}</span>
                      <time>{formatDateTime(message.created_at, locale, copy.common.notStarted)}</time>
                    </div>
                    <div className={`chat-message-body markdown-content${streaming ? " streaming" : ""}`} aria-live={streaming ? "polite" : undefined}>
                      {hasContent ? (
                        <>
                          <ReactMarkdown remarkPlugins={[remarkGfm]}>{message.content}</ReactMarkdown>
                          {streaming ? <span className="streaming-cursor" aria-hidden="true" /> : null}
                        </>
                      ) : streaming ? (
                        <div className="chat-message-placeholder">
                          <span>{copy.agent.typingPlaceholder}</span>
                          <span className="typing-dots" aria-hidden="true">
                            <span />
                            <span />
                            <span />
                          </span>
                        </div>
                      ) : (
                        <p>{copy.common.emptyResponse}</p>
                      )}
                    </div>
                  </article>
                );
              })
            ) : showStarterCards ? (
              <div className="chat-welcome">
                <span className="eyebrow">{copy.agent.starterEyebrow}</span>
                <h2>{copy.agent.starterTitle}</h2>
                <p>{copy.agent.starterDescription}</p>
                <div className="welcome-prompts">
                  {starterPrompts.map((starter, index) => (
                    <button
                      key={starter.prompt}
                      type="button"
                      data-testid="starter-card"
                      className="starter-card"
                      onClick={() => handleStarterPrompt(starter.prompt)}
                    >
                      <span className="starter-card-index">0{index + 1}</span>
                      <strong className="starter-card-title">{starter.title}</strong>
                      <span className="starter-card-description">{starter.description}</span>
                    </button>
                  ))}
                </div>
              </div>
            ) : (
              <div className="chat-empty-transcript" />
            )
          ) : (
            <div className="chat-welcome empty">
              <span className="eyebrow">{copy.agent.emptyEyebrow}</span>
              <h2>{copy.agent.emptyTitle}</h2>
              <p>{copy.agent.emptyDescription}</p>
              <div className="welcome-actions">
                <button className="btn-primary" onClick={handleNewSession}>
                  {copy.common.createSession}
                </button>
              </div>
              <div className="welcome-prompts compact-prompts">
                {starterPrompts.map((starter, index) => (
                  <button
                    key={starter.prompt}
                    type="button"
                    data-testid="starter-card"
                    className="starter-card"
                    onClick={() => handleStarterPrompt(starter.prompt)}
                  >
                    <span className="starter-card-index">0{index + 1}</span>
                    <strong className="starter-card-title">{starter.title}</strong>
                    <span className="starter-card-description">{starter.description}</span>
                  </button>
                ))}
              </div>
            </div>
          )}
          <div ref={messageEndRef} />
        </div>

        <div className="composer-shell">
          {currentSession && isResponding && (
            <div className="composer-status" data-testid="composer-status">
              <span className="typing-dots" aria-hidden="true">
                <span />
                <span />
                <span />
              </span>
              <span>{copy.agent.respondingStatus}</span>
            </div>
          )}

          {showComposerSuggestions && (
            <div className="composer-toolbar">
              {starterPrompts.map((starter) => (
                <button
                  key={starter.prompt}
                  type="button"
                  className="composer-suggestion"
                  onClick={() => setPrompt(starter.prompt)}
                >
                  {starter.title}
                </button>
              ))}
            </div>
          )}

          <div className="composer-card">
            <textarea
              id="agent-prompt"
              rows={5}
              className="composer-textarea"
              placeholder={
                currentSession
                  ? copy.agent.textareaActive
                  : copy.agent.textareaInactive
              }
              value={prompt}
              disabled={!currentSession}
              onChange={(e: ChangeEvent<HTMLTextAreaElement>) => setPrompt(e.target.value)}
              onKeyDown={handlePromptKeyDown}
            />

            <div className="composer-footer">
              <div className="composer-meta">
                <span className="composer-hint">{copy.common.ctrlEnterHint}</span>
                <span className="composer-hint">{copy.common.autoRefreshHint}</span>
              </div>
              <div className="composer-actions">
                <button className="btn-primary send-button" onClick={handleSend} disabled={!currentSession || sending || !prompt.trim()}>
                  {sending ? copy.common.working : copy.common.send}
                </button>
              </div>
            </div>
          </div>
        </div>
      </section>

      <aside className="context-sidebar" data-testid="session-context-sidebar">
        {currentSession ? (
          <>
            <div className="card context-panel session-overview-panel">
              <div className="panel-heading">
                <div>
                  <span className="eyebrow">{copy.agent.sessionPanelEyebrow}</span>
                  <h3>{currentSession.name}</h3>
                </div>
              </div>
              <div className="session-overview-scroll">
                <div className="session-settings-block" data-testid="session-settings-card">
                  <div className="session-settings-heading">
                    <div>
                      <span className="eyebrow">{copy.agent.sessionSettingsEyebrow}</span>
                      <h4>{copy.agent.sessionSettingsTitle}</h4>
                    </div>
                  </div>
                  <div className="session-settings-grid">
                    <label className="settings-field settings-field-model">
                      <span className="settings-label">{copy.agent.sessionSettingsFields.model}</span>
                      <div className={`settings-control-shell${modelOptionsLoading || !sessionModelOptions.length ? " is-disabled" : ""}`}>
                        <select
                          className="settings-select"
                          data-testid="session-model-select"
                          value={draftModel}
                          onChange={handleModelChange}
                          disabled={modelOptionsLoading || !sessionModelOptions.length}
                        >
                          {sessionModelOptions.length ? (
                            sessionModelOptions.map((option) => (
                              <option key={option.id} value={option.id}>
                                {option.name}
                              </option>
                            ))
                          ) : (
                            <option value="">{copy.agent.sessionSettingsNoOptions}</option>
                          )}
                        </select>
                      </div>
                    </label>

                    <label className="settings-field settings-field-reasoning">
                      <span className="settings-label">{copy.agent.sessionSettingsFields.reasoning}</span>
                      <div className={`settings-control-shell${!reasoningSupported ? " is-disabled" : ""}`}>
                        <select
                          className="settings-select"
                          data-testid="session-reasoning-select"
                          value={draftReasoning ?? ""}
                          onChange={handleReasoningChange}
                          disabled={!reasoningSupported}
                        >
                          {reasoningSupported ? (
                            selectedReasoningOptions.map((effort) => (
                              <option key={effort} value={effort}>
                                {getReasoningSelectLabel(effort, locale, copy)}
                              </option>
                            ))
                          ) : (
                            <option value="">{copy.agent.sessionSettingsReasoningDisabled}</option>
                          )}
                        </select>
                      </div>
                    </label>
                  </div>
                  {modelOptionsLoading ? <p className="settings-help">{copy.agent.sessionSettingsLoading}</p> : null}
                  {sessionSettingsErrorMessage && <div className="inline-alert">{sessionSettingsErrorMessage}</div>}
                  <div className="session-settings-actions">
                    <button
                      className="btn-primary btn-sm"
                      data-testid="session-settings-save"
                      onClick={handleSaveSessionSettings}
                      disabled={savingSessionSettings || !draftModel || !sessionSettingsDirty}
                    >
                      {savingSessionSettings ? copy.common.saving : copy.common.save}
                    </button>
                  </div>
                </div>
                <dl className="fact-list">
                  <div className="fact-row">
                    <dt>{copy.agent.sessionPanelFields.status}</dt>
                    <dd>{sessionStatusLabels[sessionStatus]}</dd>
                  </div>
                  <div className="fact-row">
                    <dt>{copy.agent.sessionPanelFields.activeProject}</dt>
                    <dd>{activeProject?.filename || copy.common.notSpecified}</dd>
                  </div>
                  <div className="fact-row">
                    <dt>{copy.agent.sessionPanelFields.container}</dt>
                    <dd>{context?.container ? containerStatusLabels[context.container.status] : copy.common.notStarted}</dd>
                  </div>
                  <div className="fact-row">
                    <dt>{copy.agent.sessionPanelFields.lastReply}</dt>
                    <dd>{latestAssistantMessage ? formatDateTime(latestAssistantMessage.created_at, locale, copy.common.notStarted) : copy.common.none}</dd>
                  </div>
                  <div className="fact-row">
                    <dt>{copy.agent.sessionPanelFields.latestRender}</dt>
                    <dd>{basename(context?.latest_render_path, copy.common.notGenerated)}</dd>
                  </div>
                  <div className="fact-row">
                    <dt>{copy.agent.sessionPanelFields.lastSync}</dt>
                    <dd>{formatDateTime(currentSession.updated_at, locale, copy.common.notStarted)}</dd>
                  </div>
                </dl>
                <div className="session-runtime-meta">
                  <span className="eyebrow">{copy.agent.sessionPanelFields.runtime}</span>
                  <span className="mono" data-testid="session-runtime-id">
                    {context?.session.copilot_session_id || copy.common.notStarted}
                  </span>
                </div>
                {currentSession.last_error && <div className="inline-alert">{currentSession.last_error}</div>}
              </div>
            </div>

            <ContainerManager containers={context?.container ? [context.container] : []} onStop={handleStopContainer} />

            {previewVideoSrc && previewVideoFormat && (
              <VideoPlayer
                src={previewVideoSrc}
                format={previewVideoFormat}
                downloadUrl={context?.latest_render_url}
              />
            )}

            <div className="card context-panel resources-panel">
              <div className="panel-heading">
                <div>
                  <span className="eyebrow">{copy.agent.assetsEyebrow}</span>
                  <h3>{copy.agent.assetsTitle}</h3>
                </div>
                <span className="panel-count">{context?.projects.length ?? 0}</span>
              </div>

              {context?.projects.length ? (
                <div className="project-list panel-list-scroll">
                  {context.projects.map((project) => (
                    <div key={project._id} className="project-item">
                      <div className="project-copy">
                        <div className="project-name">{project.filename}</div>
                        <div className="project-meta">{project.aep_files.length ? project.aep_files.join(", ") : copy.common.noDetectedAep}</div>
                        <div className="project-submeta">{formatDateTime(project.created_at, locale, copy.common.notStarted)}</div>
                      </div>
                      <div className="project-actions">
                        <span className={`status-badge status-${project.status === "active" ? "running" : "idle"}`}>
                          {projectStatusLabels[project.status]}
                        </span>
                        <button className="ghost-button btn-sm" onClick={() => handleDownload(project)}>
                          {copy.common.export}
                        </button>
                      </div>
                    </div>
                  ))}
                </div>
              ) : (
                <p className="empty-side">{copy.agent.assetsEmpty}</p>
              )}
            </div>

            <div className="card context-panel timeline-panel">
              <div className="panel-heading">
                <div>
                  <span className="eyebrow">{copy.agent.executionEyebrow}</span>
                  <h3>{copy.agent.executionTitle}</h3>
                </div>
                <span className="panel-count">{sortedEvents.length}</span>
              </div>
              {sortedEvents.length ? (
                <div className="timeline-list panel-list-scroll" data-testid="session-timeline">
                  {sortedEvents.map((event) => {
                    const isExpanded = expandedTimelineEventIds.includes(event._id);
                    const eventLabel = formatTimelineEventLabel(event.type);
                    const previewText = getTimelinePreviewText(event);

                    return (
                    <div
                      key={event._id}
                      className={`timeline-entry ${isExpanded ? "expanded" : ""}`}
                      data-testid="timeline-entry"
                    >
                      <button
                        type="button"
                        className="timeline-entry-summary"
                        data-testid="timeline-entry-toggle"
                        aria-expanded={isExpanded}
                        onClick={() => toggleTimelineEvent(event._id)}
                      >
                        <div className="timeline-entry-summary-layout">
                          <span className="timeline-chevron" aria-hidden="true" />
                          <div className="timeline-summary-stack">
                            <div className="timeline-summary-topline">
                              <span className="timeline-type">{eventLabel}</span>
                              <span className="timeline-time">{formatClockTime(event.created_at, locale, copy.common.notStarted)}</span>
                            </div>
                            {previewText ? <div className="timeline-summary-preview">{previewText}</div> : null}
                          </div>
                        </div>
                      </button>
                      {isExpanded ? (
                        <div className="timeline-entry-body">
                          <div className="timeline-event-code">{event.type}</div>
                          <p className="timeline-summary-full">{getTimelineExpandedSummary(event)}</p>
                          {hasEventData(event) && <pre className="timeline-event-data">{JSON.stringify(event.data, null, 2)}</pre>}
                        </div>
                      ) : null}
                    </div>
                    );
                  })}
                </div>
              ) : (
                <p className="empty-side">{copy.agent.executionEmpty}</p>
              )}
            </div>
          </>
        ) : (
          <div className="card context-panel onboarding-panel">
            <div className="panel-heading">
              <div>
                <span className="eyebrow">{copy.agent.workflowEyebrow}</span>
                <h3>{copy.agent.workflowTitle}</h3>
                <p className="panel-description">{copy.agent.workflowDescription}</p>
              </div>
            </div>
            <ol className="onboarding-list">
              {copy.agent.workflowSteps.map((step) => (
                <li key={step}>{step}</li>
              ))}
            </ol>
          </div>
        )}
      </aside>
    </div>
  );
}

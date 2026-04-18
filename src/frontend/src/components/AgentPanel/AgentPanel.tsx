import { ChangeEvent, KeyboardEvent, useEffect, useMemo, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  createSession,
  deleteSession,
  exportProject,
  getAgentContext,
  getAgentEvents,
  getAgentMessages,
  getCopilotModelOptions,
  getSessions,
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

function shortenId(value: string | null | undefined, fallback: string, size = 12) {
  if (!value) return fallback;
  return value.length <= size ? value : `${value.slice(0, size)}...`;
}

function hasEventData(event: SessionEvent) {
  return Boolean(event.data && Object.keys(event.data).length);
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

export default function AgentPanel() {
  const { copy, locale } = useI18n();
  const [sessions, setSessions] = useState<Session[]>([]);
  const [currentSession, setCurrentSession] = useState<Session | null>(null);
  const [context, setContext] = useState<AgentContext | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [events, setEvents] = useState<SessionEvent[]>([]);
  const [prompt, setPrompt] = useState("");
  const [sending, setSending] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [modelOptions, setModelOptions] = useState<CopilotModelOption[]>([]);
  const [modelOptionsLoading, setModelOptionsLoading] = useState(false);
  const [draftModel, setDraftModel] = useState("");
  const [draftReasoning, setDraftReasoning] = useState<ReasoningEffort | null>(null);
  const [savingSessionSettings, setSavingSessionSettings] = useState(false);
  const [sessionsError, setSessionsError] = useState<UiError | null>(null);
  const [panelError, setPanelError] = useState<UiError | null>(null);
  const [sessionSettingsError, setSessionSettingsError] = useState<UiError | null>(null);
  const messageEndRef = useRef<HTMLDivElement | null>(null);
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

  const selectedReasoningOptions = selectedModelOption?.supported_reasoning_efforts ?? [];
  const reasoningSupported = Boolean(selectedModelOption?.supports_reasoning_effort && selectedReasoningOptions.length);
  const effectiveDraftReasoning = reasoningSupported ? draftReasoning : null;
  const previewVideoSrc = context?.latest_render_url || context?.latest_stream_url || null;
  const previewVideoFormat = context?.latest_render_url ? "mp4" : context?.latest_stream_url ? "hls" : null;
  const sessionSettingsDirty = Boolean(currentSession) && (
    draftModel !== (currentSession?.copilot_model ?? "") ||
    effectiveDraftReasoning !== (currentSession?.copilot_reasoning_effort ?? null)
  );

  const sessionStatus = context?.session.status || currentSession?.status || "idle";
  const latestMessage = messages.length ? messages[messages.length - 1] : null;
  const showStarterCards = Boolean(currentSession) && messages.length === 0;
  const showComposerSuggestions = Boolean(currentSession) && messages.length > 0;
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

  const fetchSessions = async () => {
    try {
      const res = await getSessions();
      setSessions(res.data);
      setSessionsError(null);
      if (res.data.length === 0) {
        setCurrentSession(null);
        return;
      }

      setCurrentSession((previous) => {
        if (!previous) return res.data[0];
        return res.data.find((session: Session) => session._id === previous._id) ?? res.data[0];
      });
    } catch (err: any) {
      setSessionsError(buildUiError(err, "failedLoadSessions"));
    }
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
    setContext(contextRes.data);
    setMessages(messageRes.data);
    setEvents(eventRes.data);
    setCurrentSession((previous) =>
      previous && previous._id === contextRes.data.session._id ? contextRes.data.session : previous
    );
    setPanelError(null);
  };

  useEffect(() => {
    fetchSessions();
    loadModelOptions();
  }, []);

  useEffect(() => {
    if (!currentSession) {
      setDraftModel("");
      setDraftReasoning(null);
      setSessionSettingsError(null);
      return;
    }

    setDraftModel(currentSession.copilot_model);
    setDraftReasoning(currentSession.copilot_reasoning_effort);
    setSessionSettingsError(null);
  }, [currentSession?._id, currentSession?.copilot_model, currentSession?.copilot_reasoning_effort]);

  useEffect(() => {
    if (!messages.length) return;

    messageEndRef.current?.scrollIntoView({ block: "end" });
  }, [messages.length]);

  useEffect(() => {
    if (!currentSession) {
      setContext(null);
      setMessages([]);
      setEvents([]);
      setPanelError(null);
      return;
    }

    loadCurrentSession(currentSession._id).catch((err) => {
      setPanelError(buildUiError(err, "failedLoadSessionData"));
    });

    const timer = window.setInterval(() => {
      loadCurrentSession(currentSession._id).catch(() => {});
      fetchSessions().catch(() => {});
    }, 2500);

    return () => window.clearInterval(timer);
  }, [currentSession?._id]);

  const createNewSession = async () => {
    try {
      const name = `${copy.common.sessionPrefix} ${sessions.length + 1}`;
      const res = await createSession(name);
      setSessions((prev: Session[]) => [res.data, ...prev]);
      setCurrentSession(res.data);
      setSessionsError(null);
      return res.data;
    } catch (err: any) {
      setSessionsError(buildUiError(err, "failedCreateSession"));
      return null;
    }
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

  const handleSend = async () => {
    if (!currentSession || !prompt.trim()) return;
    const content = prompt.trim();
    setSending(true);
    setPrompt("");
    try {
      await sendChatTurn(currentSession._id, content);
      await loadCurrentSession(currentSession._id);
      await fetchSessions();
      setPanelError(null);
    } catch (err: any) {
      setPanelError(buildUiError(err, "failedSendPrompt"));
    }
    setSending(false);
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
                    className={`session-item ${currentSession?._id === session._id ? "active" : ""}`}
                    onClick={() => setCurrentSession(session)}
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
            <h1>{currentSession ? currentSession.name : copy.agent.title.empty}</h1>
          </div>
          <div className="chat-stage-actions">
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
            messages.length ? (
              messages.map((message) => (
                <article key={message._id} className={`chat-message role-${message.role}`}>
                  <div className="chat-message-meta">
                    <span className="chat-message-author">{message.role === "user" ? copy.agent.you : copy.agent.assistant}</span>
                    <time>{formatDateTime(message.created_at, locale, copy.common.notStarted)}</time>
                  </div>
                  <div className="chat-message-body markdown-content">
                    {message.content ? (
                      <ReactMarkdown remarkPlugins={[remarkGfm]}>{message.content}</ReactMarkdown>
                    ) : (
                      <p>{copy.common.emptyResponse}</p>
                    )}
                  </div>
                </article>
              ))
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
                  <p className="panel-description">{copy.agent.sessionPanelDescription}</p>
                </div>
              </div>
              <div className="session-settings-block" data-testid="session-settings-card">
                <div className="session-settings-heading">
                  <div>
                    <span className="eyebrow">{copy.agent.sessionSettingsEyebrow}</span>
                    <h4>{copy.agent.sessionSettingsTitle}</h4>
                    <p className="panel-description">{copy.agent.sessionSettingsDescription}</p>
                  </div>
                </div>
                <div className="session-settings-grid">
                  <label className="settings-field">
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

                  <label className="settings-field">
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
                              {copy.common.reasoningEfforts[effort]}
                            </option>
                          ))
                        ) : (
                          <option value="">{copy.agent.sessionSettingsReasoningDisabled}</option>
                        )}
                      </select>
                    </div>
                  </label>
                </div>
                <p className="settings-help">
                  {modelOptionsLoading ? copy.agent.sessionSettingsLoading : copy.agent.sessionSettingsHint}
                </p>
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
                  <dd>{latestMessage ? formatDateTime(latestMessage.created_at, locale, copy.common.notStarted) : copy.common.none}</dd>
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
                <span className="mono">{shortenId(context?.session.copilot_session_id, copy.common.notStarted)}</span>
              </div>
              {currentSession.last_error && <div className="inline-alert">{currentSession.last_error}</div>}
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
                  {sortedEvents.map((event) => (
                    <details key={event._id} className="timeline-entry" data-testid="timeline-entry">
                      <summary className="timeline-entry-summary">
                        <span className="timeline-chevron" aria-hidden="true" />
                        <div className="timeline-summary-stack">
                          <div className="timeline-summary-topline">
                            <span className="timeline-type">{event.type}</span>
                            <span className="timeline-time">{formatClockTime(event.created_at, locale, copy.common.notStarted)}</span>
                          </div>
                          <div className="timeline-summary-preview">{event.summary}</div>
                        </div>
                      </summary>
                      <div className="timeline-entry-body">
                        <p className="timeline-summary-full">{event.summary}</p>
                        {hasEventData(event) && <pre className="timeline-event-data">{JSON.stringify(event.data, null, 2)}</pre>}
                      </div>
                    </details>
                  ))}
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

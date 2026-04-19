import {
  ChangeEvent,
  ClipboardEvent,
  CSSProperties,
  DragEvent,
  KeyboardEvent,
  PointerEvent as ReactPointerEvent,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
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
import {
  AgentContext,
  ChatImageAttachment,
  ChatMessage,
  CopilotModelOption,
  ProjectInfo,
  ReasoningEffort,
  Session,
  SessionEvent,
} from "../../types";
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

const SUPPORTED_INLINE_IMAGE_MIME_TYPES = new Set(["image/png", "image/jpeg", "image/webp", "image/gif"]);
const MAX_COMPOSER_IMAGE_BYTES = 6 * 1024 * 1024;
const MAX_COMPOSER_ATTACHMENTS = 4;
const DEFAULT_COMPOSER_HEIGHT = 220;
const MIN_COMPOSER_HEIGHT = 176;
const MIN_TRANSCRIPT_HEIGHT = 220;
const COMPOSER_SPLITTER_HEIGHT = 14;
const COMPOSER_TEXTAREA_MIN_HEIGHT = 132;
const COMPOSER_HEIGHT_STORAGE_KEY = "shotwright_composer_height";

type TimelineTone = MetaChip["tone"];

type TimelineDetailField = {
  label: string;
  value: string;
  mono?: boolean;
  tone?: TimelineTone;
};

type TimelineDetailBlock = {
  label: string;
  value: string;
  kind: "text" | "code" | "error";
};

type TimelinePresentation = {
  tone: TimelineTone;
  stage: string;
  fields: TimelineDetailField[];
  blocks: TimelineDetailBlock[];
  rawPayload: string | null;
};

type PendingImageAttachment = ChatImageAttachment & {
  id: string;
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

function clamp(value: number, min: number, max: number) {
  return Math.min(Math.max(value, min), max);
}

function isScrolledNearBottom(element: HTMLElement, threshold = 32) {
  return element.scrollTop + element.clientHeight >= element.scrollHeight - threshold;
}

function isScrolledNearTop(element: HTMLElement, threshold = 32) {
  return element.scrollTop <= threshold;
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

function readFileAsDataUrl(file: File) {
  return new Promise<string>((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(new Error("image-read-failed"));
    reader.onload = () => {
      if (typeof reader.result === "string") {
        resolve(reader.result);
        return;
      }

      reject(new Error("image-read-failed"));
    };
    reader.readAsDataURL(file);
  });
}

function measureImageDataUrl(dataUrl: string) {
  return new Promise<{ width: number; height: number }>((resolve, reject) => {
    const image = new Image();
    image.onload = () => resolve({ width: image.naturalWidth, height: image.naturalHeight });
    image.onerror = () => reject(new Error("image-read-failed"));
    image.src = dataUrl;
  });
}

async function buildPendingImageAttachment(file: File): Promise<PendingImageAttachment> {
  const mimeType = file.type.trim().toLowerCase();
  if (!SUPPORTED_INLINE_IMAGE_MIME_TYPES.has(mimeType)) {
    throw new Error("unsupported-image");
  }

  if (file.size > MAX_COMPOSER_IMAGE_BYTES) {
    throw new Error("image-too-large");
  }

  const dataUrl = await readFileAsDataUrl(file);
  const dimensions = await measureImageDataUrl(dataUrl);
  const extension = mimeType.split("/")[1] || "png";
  const displayName = file.name?.trim() || `image-${Date.now()}.${extension}`;

  return {
    id: `attachment-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
    type: "image",
    mime_type: mimeType,
    data_url: dataUrl,
    display_name: displayName,
    width: dimensions.width,
    height: dimensions.height,
    size_bytes: file.size,
  };
}

function getClipboardImageFiles(event: ClipboardEvent<HTMLTextAreaElement>) {
  return Array.from(event.clipboardData?.items || [])
    .filter((item) => item.kind === "file" && SUPPORTED_INLINE_IMAGE_MIME_TYPES.has(item.type.toLowerCase()))
    .map((item) => item.getAsFile())
    .filter((file): file is File => Boolean(file));
}

function getDroppedImageFiles(event: DragEvent<HTMLElement>) {
  return Array.from(event.dataTransfer?.files || []).filter((file) => SUPPORTED_INLINE_IMAGE_MIME_TYPES.has(file.type.toLowerCase()));
}

function getComposerAttachmentErrorMessage(error: unknown, copy: TranslationCopy) {
  const code = error instanceof Error ? error.message : "";

  switch (code) {
    case "unsupported-image":
      return copy.agent.attachmentErrorUnsupported;
    case "image-too-large":
      return copy.agent.attachmentErrorTooLarge;
    default:
      return copy.agent.attachmentErrorRead;
  }
}

function hasEventData(event: SessionEvent) {
  return Boolean(event.data && Object.keys(event.data).length);
}

function compactEventPayload(value: unknown): unknown | null {
  if (value == null) return null;

  if (Array.isArray(value)) {
    const nextValues = value.map((entry) => compactEventPayload(entry)).filter((entry) => entry !== null);
    return nextValues.length ? nextValues : null;
  }

  if (typeof value === "object") {
    const nextEntries = Object.entries(value as Record<string, unknown>)
      .map(([key, entry]) => [key, compactEventPayload(entry)] as const)
      .filter(([, entry]) => entry !== null);

    return nextEntries.length ? Object.fromEntries(nextEntries) : null;
  }

  if (typeof value === "string") {
    const trimmed = value.trim();
    return trimmed ? trimmed : null;
  }

  return value;
}

function extractEventText(value: unknown): string {
  if (typeof value === "string") return value.trim();
  if (typeof value === "number" || typeof value === "boolean") return String(value);

  if (Array.isArray(value)) {
    return value.map((entry) => extractEventText(entry)).filter(Boolean).join(", ");
  }

  if (value && typeof value === "object") {
    const record = value as Record<string, unknown>;
    return (
      [record.message, record.error, record.summary, record.content, record.reason]
        .map((entry) => extractEventText(entry))
        .find(Boolean) || ""
    );
  }

  return "";
}

function formatEventFieldValue(value: unknown): string {
  if (value == null) return "";

  const textValue = extractEventText(value);
  if (textValue) return textValue;

  if (Array.isArray(value)) {
    return value.map((entry) => formatEventFieldValue(entry)).filter(Boolean).join(", ");
  }

  if (typeof value === "object") {
    return JSON.stringify(value);
  }

  return String(value);
}

function formatEventBlockValue(value: unknown): string {
  if (value == null) return "";
  if (typeof value === "string") return value.trim();
  return JSON.stringify(value, null, 2);
}

function formatCommandValue(command: unknown, args: unknown): string {
  const parts = [extractEventText(command)];

  if (Array.isArray(args)) {
    parts.push(...args.map((entry) => formatEventFieldValue(entry)).filter(Boolean));
  } else if (args != null) {
    parts.push(formatEventFieldValue(args));
  }

  return parts.filter(Boolean).join(" ").trim();
}

function formatTimelineEventLabel(value: string) {
  return value
    .split(/[._]+/)
    .filter(Boolean)
    .map((segment) => segment.charAt(0).toUpperCase() + segment.slice(1).toLowerCase())
    .join(" ");
}

function getTimelinePreviewText(event: SessionEvent) {
  if (event.summary !== event.type) {
    return event.summary;
  }

  const compactData = compactEventPayload(event.data);
  const payload = compactData && typeof compactData === "object" && !Array.isArray(compactData)
    ? (compactData as Record<string, unknown>)
    : {};

  const preview = [
    payload.tool_name,
    payload.toolName,
    event.type.startsWith("skill") ? payload.name : null,
    payload.agent_display_name,
    payload.agent_name,
    payload.agentName,
    payload.path,
    payload.command,
    payload.reason,
    payload.message,
    payload.error,
  ]
    .map((value) => extractEventText(value))
    .find(Boolean);

  if (!preview) return "";
  return preview.length > 180 ? `${preview.slice(0, 177)}...` : preview;
}

function getTimelineExpandedSummary(event: SessionEvent) {
  return event.summary === event.type ? formatTimelineEventLabel(event.type) : event.summary;
}

function getTimelineEventTone(event: SessionEvent, payload: Record<string, unknown>): TimelineTone {
  if (event.type.includes("error") || payload.success === false || Boolean(extractEventText(payload.error))) {
    return "danger";
  }

  if (event.type.includes("complete") || event.type.endsWith("idle") || payload.success === true) {
    return "success";
  }

  if (
    event.type.includes("start") ||
    event.type.includes("requested") ||
    event.type.includes("submitted") ||
    event.type.includes("invoked")
  ) {
    return "accent";
  }

  if (event.type.includes("usage") || event.type.includes("modified") || event.type.includes("info")) {
    return "muted";
  }

  return "neutral";
}

function getTimelineEventStage(event: SessionEvent, copy: TranslationCopy): string {
  const namespace = event.type.split(".")[0];

  switch (namespace) {
    case "session":
      return copy.agent.timelineDetails.stage.session;
    case "assistant":
      return copy.agent.timelineDetails.stage.assistant;
    case "tool":
      return copy.agent.timelineDetails.stage.tool;
    case "permission":
      return copy.agent.timelineDetails.stage.permission;
    case "skill":
      return copy.agent.timelineDetails.stage.skill;
    case "agent":
    case "subagent":
      return copy.agent.timelineDetails.stage.agent;
    default:
      return copy.agent.timelineDetails.stage.other;
  }
}

function getTimelineResultLabel(event: SessionEvent, payload: Record<string, unknown>, copy: TranslationCopy): string | null {
  if (typeof payload.success === "boolean") {
    return payload.success
      ? copy.agent.timelineDetails.result.success
      : copy.agent.timelineDetails.result.failure;
  }

  if (event.summary.endsWith("(ok)")) {
    return copy.agent.timelineDetails.result.success;
  }

  if (event.summary.endsWith("(failed)")) {
    return copy.agent.timelineDetails.result.failure;
  }

  if (event.type.includes("start") || event.type.includes("requested") || event.type.includes("submitted")) {
    return copy.agent.timelineDetails.result.pending;
  }

  return null;
}

function buildTimelinePresentation(event: SessionEvent, copy: TranslationCopy): TimelinePresentation {
  const compactData = compactEventPayload(event.data);
  const payload = compactData && typeof compactData === "object" && !Array.isArray(compactData)
    ? (compactData as Record<string, unknown>)
    : {};
  const tone = getTimelineEventTone(event, payload);
  const fields: TimelineDetailField[] = [];
  const blocks: TimelineDetailBlock[] = [];
  const fieldKeys = new Set<string>();
  const blockKeys = new Set<string>();

  const addField = (label: string, value: unknown, options: Partial<TimelineDetailField> = {}) => {
    const formattedValue = formatEventFieldValue(value).trim();
    if (!formattedValue) return;

    const key = `${label}:${formattedValue}`;
    if (fieldKeys.has(key)) return;
    fieldKeys.add(key);

    fields.push({
      label,
      value: formattedValue,
      mono: options.mono ?? false,
      tone: options.tone ?? "neutral",
    });
  };

  const addBlock = (label: string, value: unknown, kind: TimelineDetailBlock["kind"] = "text") => {
    const formattedValue = formatEventBlockValue(value).trim();
    if (!formattedValue) return;

    const key = `${label}:${kind}:${formattedValue}`;
    if (blockKeys.has(key)) return;
    blockKeys.add(key);

    blocks.push({ label, value: formattedValue, kind });
  };

  const resultLabel = getTimelineResultLabel(event, payload, copy);
  if (resultLabel) {
    addField(copy.agent.timelineDetails.labels.result, resultLabel, { tone });
  }

  addField(copy.agent.timelineDetails.labels.tool, payload.tool_name ?? payload.toolName);
  if (event.type.startsWith("skill") || payload.name) {
    addField(copy.agent.timelineDetails.labels.skill, payload.name);
  }
  addField(copy.agent.timelineDetails.labels.agent, payload.agent_display_name ?? payload.agent_name ?? payload.agentName);
  addField(
    copy.agent.timelineDetails.labels.model,
    payload.model ?? payload.current_model ?? payload.new_model ?? payload.selected_model,
  );
  addField(copy.agent.timelineDetails.labels.status, payload.status);
  addField(copy.agent.timelineDetails.labels.permission, (payload.permission_request as Record<string, unknown> | undefined)?.kind ?? payload.kind);
  addField(copy.agent.timelineDetails.labels.phase, payload.phase);
  addField(copy.agent.timelineDetails.labels.path, payload.path, { mono: true });
  addField(copy.agent.timelineDetails.labels.reason, payload.reason);

  const errorText = extractEventText(payload.error);
  const messageText = extractEventText(payload.message);
  if (errorText) {
    addBlock(copy.agent.timelineDetails.labels.error, errorText, "error");
  } else if (messageText) {
    addBlock(copy.agent.timelineDetails.labels.message, messageText, tone === "danger" ? "error" : "text");
  }

  const commandValue = formatCommandValue(payload.command, payload.args);
  if (commandValue) {
    addBlock(copy.agent.timelineDetails.labels.command, commandValue, "code");
  }

  addBlock(copy.agent.timelineDetails.labels.arguments, payload.arguments, "code");
  addBlock(copy.agent.timelineDetails.labels.input, payload.input, "code");
  addBlock(copy.agent.timelineDetails.labels.output, payload.output ?? payload.result, "code");

  if (typeof payload.content === "string") {
    if (payload.content.length <= 600) {
      addBlock(copy.agent.timelineDetails.labels.content, payload.content, "code");
    }
  } else {
    addBlock(copy.agent.timelineDetails.labels.content, payload.content, "code");
  }

  return {
    tone,
    stage: getTimelineEventStage(event, copy),
    fields,
    blocks,
    rawPayload: Object.keys(payload).length ? JSON.stringify(payload, null, 2) : null,
  };
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

type TranscriptEntry =
  | { kind: "message"; key: string; message: ChatMessage }
  | { kind: "execution"; key: string; turnId: string; events: SessionEvent[] };

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

function getMessageImageAttachments(message: ChatMessage): ChatImageAttachment[] {
  const attachments = message.metadata?.["attachments"];
  if (!Array.isArray(attachments)) return [];

  return attachments
    .filter((attachment): attachment is Record<string, unknown> => Boolean(attachment && typeof attachment === "object"))
    .map((attachment) => ({
      type: "image" as const,
      mime_type: typeof attachment["mime_type"] === "string" ? attachment["mime_type"] : "image/png",
      data_url: typeof attachment["data_url"] === "string" ? attachment["data_url"] : "",
      display_name: typeof attachment["display_name"] === "string" ? attachment["display_name"] : null,
      width: typeof attachment["width"] === "number" ? attachment["width"] : null,
      height: typeof attachment["height"] === "number" ? attachment["height"] : null,
      size_bytes: typeof attachment["size_bytes"] === "number" ? attachment["size_bytes"] : null,
    }))
    .filter((attachment) => Boolean(attachment.data_url));
}

function getMessageMetadataString(message: ChatMessage, key: string) {
  const value = message.metadata?.[key];
  return typeof value === "string" && value.trim() ? value.trim() : null;
}

function getMessageTurnId(message: ChatMessage) {
  return getMessageMetadataString(message, "turn_id");
}

function getEventTurnId(event: SessionEvent) {
  return typeof event.turn_id === "string" && event.turn_id.trim() ? event.turn_id.trim() : null;
}

function shouldRenderInlineExecutionEvent(event: SessionEvent) {
  if (!getEventTurnId(event)) return false;

  if (
    event.type === "session.idle" ||
    event.type === "session.created" ||
    event.type.startsWith("assistant.") ||
    event.type.startsWith("user.")
  ) {
    return false;
  }

  return true;
}

function buildTranscriptEntries(messages: ChatMessage[], events: SessionEvent[]): TranscriptEntry[] {
  const entries: TranscriptEntry[] = [];
  const emittedTurns = new Set<string>();
  const eventsByTurn = new Map<string, SessionEvent[]>();

  for (const event of events) {
    if (!shouldRenderInlineExecutionEvent(event)) continue;
    const turnId = getEventTurnId(event);
    if (!turnId) continue;
    const turnEvents = eventsByTurn.get(turnId) ?? [];
    turnEvents.push(event);
    eventsByTurn.set(turnId, turnEvents);
  }

  for (const message of messages) {
    entries.push({ kind: "message", key: `message-${message._id}`, message });

    if (message.role !== "user") continue;

    const turnId = getMessageTurnId(message);
    if (!turnId || emittedTurns.has(turnId)) continue;

    const turnEvents = eventsByTurn.get(turnId);
    if (!turnEvents?.length) continue;

    entries.push({ kind: "execution", key: `execution-${turnId}`, turnId, events: turnEvents });
    emittedTurns.add(turnId);
  }

  for (const [turnId, turnEvents] of eventsByTurn) {
    if (emittedTurns.has(turnId)) continue;
    entries.push({ kind: "execution", key: `execution-${turnId}`, turnId, events: turnEvents });
  }

  return entries;
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

function getSessionModelToneClass(model: string) {
  const normalized = model.trim().toLowerCase();
  if (normalized.includes("gpt-5.4-mini")) return "tone-gpt-54-mini";
  if (normalized.includes("gpt-5.4")) return "tone-gpt-54";
  if (normalized.startsWith("gpt")) return "tone-gpt";
  if (normalized.includes("claude") && normalized.includes("haiku")) return "tone-claude-haiku";
  if (normalized.includes("claude") && normalized.includes("sonnet")) return "tone-claude-sonnet";
  if (normalized.includes("claude")) return "tone-claude";
  if (normalized.includes("gemini")) return "tone-gemini";
  if (normalized.includes("qwen")) return "tone-qwen";
  return "tone-neutral";
}

function formatSessionModelLabel(model: string) {
  const normalized = model.trim();
  if (!normalized) return "Unknown";

  if (/^gpt-/i.test(normalized)) {
    return normalized.replace(/^gpt-/i, "GPT-").replace(/-mini$/i, " mini");
  }

  return normalized
    .split("-")
    .map((segment, index) => {
      if (index === 0 && /^claude$/i.test(segment)) return "Claude";
      if (index === 0 && /^gemini$/i.test(segment)) return "Gemini";
      if (index === 0 && /^qwen$/i.test(segment)) return "Qwen";
      if (/^mini$/i.test(segment)) return "mini";
      if (!segment) return segment;
      return segment.charAt(0).toUpperCase() + segment.slice(1);
    })
    .join(" ");
}

type AgentPanelProps = {
  isSessionSidebarCollapsed?: boolean;
  isContextSidebarCollapsed?: boolean;
};

export default function AgentPanel({
  isSessionSidebarCollapsed = false,
  isContextSidebarCollapsed = false,
}: AgentPanelProps) {
  const navigate = useNavigate();
  const location = useLocation();
  const { sessionId: routedSessionId } = useParams<{ sessionId?: string }>();
  const { copy, locale } = useI18n();
  const [sessions, setSessions] = useState<Session[]>([]);
  const [hasLoadedSessions, setHasLoadedSessions] = useState(false);
  const [currentSession, setCurrentSession] = useState<Session | null>(null);
  const [context, setContext] = useState<AgentContext | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [events, setEvents] = useState<SessionEvent[]>([]);
  const [optimisticTurn, setOptimisticTurn] = useState<OptimisticTurn | null>(null);
  const [prompt, setPrompt] = useState("");
  const [pendingAttachments, setPendingAttachments] = useState<PendingImageAttachment[]>([]);
  const [composerAttachmentError, setComposerAttachmentError] = useState<string | null>(null);
  const [isDraggingComposer, setIsDraggingComposer] = useState(false);
  const [composerHeight, setComposerHeight] = useState(() => {
    if (typeof window === "undefined") return DEFAULT_COMPOSER_HEIGHT;
    const storedValue = Number(window.localStorage.getItem(COMPOSER_HEIGHT_STORAGE_KEY));
    return Number.isFinite(storedValue)
      ? clamp(storedValue, MIN_COMPOSER_HEIGHT, 420)
      : DEFAULT_COMPOSER_HEIGHT;
  });
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
  const composerTextareaRef = useRef<HTMLTextAreaElement | null>(null);
  const composerCardRef = useRef<HTMLDivElement | null>(null);
  const composerFooterRef = useRef<HTMLDivElement | null>(null);
  const composerAttachmentsRef = useRef<HTMLDivElement | null>(null);
  const chatStageBodyRef = useRef<HTMLDivElement | null>(null);
  const timelineListRef = useRef<HTMLDivElement | null>(null);
  const followTimelineRef = useRef(true);
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
    () => [...events].sort((left, right) => {
      const leftSequence = typeof left.sequence === "number" ? left.sequence : null;
      const rightSequence = typeof right.sequence === "number" ? right.sequence : null;

      if (leftSequence !== null && rightSequence !== null && leftSequence !== rightSequence) {
        return leftSequence - rightSequence;
      }

      const createdAtDelta = new Date(left.created_at).getTime() - new Date(right.created_at).getTime();
      if (createdAtDelta !== 0) {
        return createdAtDelta;
      }

      if (leftSequence !== null && rightSequence === null) return -1;
      if (leftSequence === null && rightSequence !== null) return 1;
      return 0;
    }),
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

  const transcriptEntries = useMemo(
    () => buildTranscriptEntries(visibleMessages, sortedEvents),
    [sortedEvents, visibleMessages]
  );
  const timelineEvents = useMemo(() => [...sortedEvents].reverse(), [sortedEvents]);
  const latestTimelineEventId = timelineEvents.length ? timelineEvents[0]._id : null;

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

  const clampComposerHeight = () => {
    const stageBody = chatStageBodyRef.current;
    if (!stageBody) return;

    const maxComposerHeight = Math.max(
      MIN_COMPOSER_HEIGHT,
      stageBody.clientHeight - MIN_TRANSCRIPT_HEIGHT - COMPOSER_SPLITTER_HEIGHT,
    );
    setComposerHeight((previous) => clamp(previous, MIN_COMPOSER_HEIGHT, maxComposerHeight));
  };

  const resizeComposerTextarea = () => {
    const textarea = composerTextareaRef.current;
    const composerCard = composerCardRef.current;
    if (!textarea || !composerCard) return;

    const footerHeight = composerFooterRef.current?.offsetHeight ?? 0;
    const attachmentsHeight = composerAttachmentsRef.current?.offsetHeight ?? 0;
    const availableHeight = Math.max(
      COMPOSER_TEXTAREA_MIN_HEIGHT,
      composerCard.clientHeight - footerHeight - attachmentsHeight,
    );

    textarea.style.height = "0px";
    textarea.style.height = `${Math.max(COMPOSER_TEXTAREA_MIN_HEIGHT, availableHeight, textarea.scrollHeight)}px`;
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
      setHasLoadedSessions(true);
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
    if (!hasLoadedSessions) {
      return;
    }

    if (!sessions.length) {
      if (routedSessionId) {
        navigateToSession(null, true);
      }
      return;
    }

    const routedSession = routedSessionId
      ? sessions.find((session) => session._id === routedSessionId) ?? null
      : null;
    if (routedSession) {
      setCurrentSession(routedSession);
      return;
    }

    const preservedSession = currentSession ? sessions.find((session) => session._id === currentSession._id) ?? null : null;
    const nextSession = preservedSession ?? sessions[0];

    setCurrentSession(nextSession);

    if (nextSession) {
      navigateToSession(nextSession._id, true);
    }
  }, [currentSession?._id, hasLoadedSessions, routedSessionId, sessions]);

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
    if (typeof window === "undefined") return;

    const frameId = window.requestAnimationFrame(() => {
      resizeComposerTextarea();
    });

    return () => window.cancelAnimationFrame(frameId);
  }, [prompt, pendingAttachments.length, composerHeight, currentSession?._id]);

  useEffect(() => {
    if (typeof window === "undefined") return;
    window.localStorage.setItem(COMPOSER_HEIGHT_STORAGE_KEY, String(composerHeight));
  }, [composerHeight]);

  useEffect(() => {
    clampComposerHeight();

    const handleResize = () => {
      clampComposerHeight();
      resizeComposerTextarea();
    };
    window.addEventListener("resize", handleResize);
    return () => window.removeEventListener("resize", handleResize);
  }, []);

  useEffect(() => {
    followTimelineRef.current = true;
  }, [currentSession?._id]);

  useEffect(() => {
    if (typeof window === "undefined" || !latestTimelineEventId || !followTimelineRef.current) return;

    const frameId = window.requestAnimationFrame(() => {
      const timelineList = timelineListRef.current;
      if (!timelineList || !followTimelineRef.current) return;
      timelineList.scrollTop = 0;
    });

    return () => window.cancelAnimationFrame(frameId);
  }, [currentSession?._id, latestTimelineEventId]);

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
    if (!currentSession || sending) return;

    const sessionId = currentSession._id;
    const content = prompt.trim();
    const attachmentsToSend = pendingAttachments.map(({ id, ...attachment }) => attachment);
    if (!content && !attachmentsToSend.length) return;

    followTimelineRef.current = true;

    const optimisticUserMessage = buildOptimisticMessage(sessionId, "user", content, {
      attachments: attachmentsToSend,
    });
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
    setPendingAttachments([]);
    setComposerAttachmentError(null);
    setPanelError(null);
    syncSessionRecord({
      ...currentSession,
      status: "running",
      last_error: null,
      updated_at: new Date().toISOString(),
    });

    void sendChatTurn(sessionId, { content, attachments: attachmentsToSend })
      .then(() => {
        setPanelError(null);
        void Promise.allSettled([loadCurrentSession(sessionId), fetchSessions()]);
      })
      .catch(async (err: any) => {
        setPanelError(buildUiError(err, "failedSendPrompt"));
        setPrompt(content);
        setPendingAttachments((previous) => (previous.length ? previous : pendingAttachments));
        setOptimisticTurn((previous) => (previous?.sessionId === sessionId ? null : previous));
        await Promise.allSettled([loadCurrentSession(sessionId), fetchSessions()]);
      })
      .finally(() => {
        setSendingSessionId((previous) => (previous === sessionId ? null : previous));
      });
  };

  const handleAppendAttachments = async (files: File[]) => {
    if (!files.length) return;

    const availableSlots = Math.max(0, MAX_COMPOSER_ATTACHMENTS - pendingAttachments.length);
    if (!availableSlots) {
      setComposerAttachmentError(copy.agent.attachmentErrorLimit);
      return;
    }

    const nextFiles = files.slice(0, availableSlots);
    const overflowed = files.length > availableSlots;

    try {
      const nextAttachments = await Promise.all(nextFiles.map((file) => buildPendingImageAttachment(file)));
      setPendingAttachments((previous) => [...previous, ...nextAttachments]);
      setComposerAttachmentError(overflowed ? copy.agent.attachmentErrorLimit : null);
    } catch (error) {
      setComposerAttachmentError(getComposerAttachmentErrorMessage(error, copy));
    }
  };

  const handleComposerPaste = (event: ClipboardEvent<HTMLTextAreaElement>) => {
    const imageFiles = getClipboardImageFiles(event);
    if (!imageFiles.length) return;

    event.preventDefault();
    void handleAppendAttachments(imageFiles);
  };

  const handleComposerDragOver = (event: DragEvent<HTMLDivElement>) => {
    const imageFiles = getDroppedImageFiles(event);
    if (!imageFiles.length) return;

    event.preventDefault();
    event.dataTransfer.dropEffect = "copy";
    setIsDraggingComposer(true);
  };

  const handleComposerDragLeave = (event: DragEvent<HTMLDivElement>) => {
    if (event.currentTarget.contains(event.relatedTarget as Node | null)) return;
    setIsDraggingComposer(false);
  };

  const handleComposerDrop = (event: DragEvent<HTMLDivElement>) => {
    const imageFiles = getDroppedImageFiles(event);
    if (!imageFiles.length) return;

    event.preventDefault();
    setIsDraggingComposer(false);
    void handleAppendAttachments(imageFiles);
  };

  const handleRemoveAttachment = (attachmentId: string) => {
    setPendingAttachments((previous) => previous.filter((attachment) => attachment.id !== attachmentId));
    setComposerAttachmentError(null);
    composerTextareaRef.current?.focus();
  };

  const handleComposerResizeStart = (event: ReactPointerEvent<HTMLDivElement>) => {
    const stageBody = chatStageBodyRef.current;
    if (!stageBody) return;

    event.preventDefault();
    const handle = event.currentTarget;
    const pointerId = event.pointerId;
    const startHeight = composerHeight;
    const startY = event.clientY;

    handle.setPointerCapture(pointerId);

    const handlePointerMove = (moveEvent: PointerEvent) => {
      const maxComposerHeight = Math.max(
        MIN_COMPOSER_HEIGHT,
        stageBody.clientHeight - MIN_TRANSCRIPT_HEIGHT - COMPOSER_SPLITTER_HEIGHT,
      );
      setComposerHeight(clamp(startHeight - (moveEvent.clientY - startY), MIN_COMPOSER_HEIGHT, maxComposerHeight));
    };

    const handlePointerUp = () => {
      if (handle.hasPointerCapture(pointerId)) {
        handle.releasePointerCapture(pointerId);
      }
      window.removeEventListener("pointermove", handlePointerMove);
      window.removeEventListener("pointerup", handlePointerUp);
    };

    window.addEventListener("pointermove", handlePointerMove);
    window.addEventListener("pointerup", handlePointerUp);
  };

  const handleTimelineScroll = () => {
    const timelineList = timelineListRef.current;
    if (!timelineList) return;
    followTimelineRef.current = isScrolledNearTop(timelineList);
  };

  const composerLayoutStyle = useMemo(
    () => ({ "--composer-height": `${composerHeight}px` } as CSSProperties),
    [composerHeight],
  );
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
      <aside
        className="secondary-sidebar"
        data-testid="session-list-sidebar"
        hidden={isSessionSidebarCollapsed}
      >
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
                        <span className={`session-status-orb status-${session.status}`} aria-hidden="true">
                          <span className="session-status-core" />
                        </span>
                        <div className="session-title-copy">
                          <span className="session-name">{session.name}</span>
                          <span className={`session-model-chip ${getSessionModelToneClass(session.copilot_model)}`}>
                            {formatSessionModelLabel(session.copilot_model)}
                          </span>
                        </div>
                      </div>
                      <span className={`status-badge status-${session.status}`}>{sessionStatusLabels[session.status]}</span>
                    </div>
                    <div className="session-footline">
                      <span className={`session-info-chip ${session.active_project_id ? "is-linked" : "is-empty"}`}>
                        <span className="session-chip-dot" aria-hidden="true" />
                        <span>{session.active_project_id ? copy.common.yesBoundProject : copy.common.noProjectUploaded}</span>
                      </span>
                      <time className="session-time-chip">{formatDateTime(session.updated_at, locale, copy.common.notStarted)}</time>
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

      <section className="chat-stage" data-testid="chat-stage">
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

        <div className="chat-stage-body" ref={chatStageBodyRef} style={composerLayoutStyle}>
          <div className="chat-transcript">
            {panelErrorMessage && <div className="notice-banner transcript-notice">{panelErrorMessage}</div>}
            {currentSession ? (
              transcriptEntries.length ? (
                transcriptEntries.map((entry) => {
                  if (entry.kind === "message") {
                    const { message } = entry;
                    const streaming = isStreamingMessage(message);
                    const hasContent = hasRenderableMessageContent(message);
                    const messageImageAttachments = getMessageImageAttachments(message);
                    const hasRenderableBody = hasContent || messageImageAttachments.length > 0;
                    const roleLabel = message.role === "user" ? copy.agent.you : copy.agent.assistant;
                    const avatarLabel = message.role === "user" ? roleLabel.charAt(0).toUpperCase() : "SW";

                    return (
                      <div key={entry.key} className={`chat-entry role-${message.role}`}>
                        <div className={`chat-entry-shell role-${message.role}`}>
                          <span className={`chat-avatar chat-avatar-${message.role}`} aria-hidden="true">
                            {avatarLabel}
                          </span>

                          <article className={`chat-message role-${message.role}`}>
                            <div className="chat-message-meta">
                              <span className="chat-message-author">{roleLabel}</span>
                              <time>{formatDateTime(message.created_at, locale, copy.common.notStarted)}</time>
                            </div>
                            <div className={`chat-message-body markdown-content${streaming ? " streaming" : ""}`} aria-live={streaming ? "polite" : undefined}>
                              {hasContent ? <ReactMarkdown remarkPlugins={[remarkGfm]}>{message.content}</ReactMarkdown> : null}

                              {messageImageAttachments.length ? (
                                <div className={`chat-attachment-grid${hasContent ? " has-copy" : ""}`}>
                                  {messageImageAttachments.map((attachment, index) => {
                                    const attachmentMeta = [
                                      attachment.display_name,
                                      attachment.width && attachment.height ? `${attachment.width} x ${attachment.height}` : null,
                                    ]
                                      .filter(Boolean)
                                      .join(" · ");

                                    return (
                                      <figure key={`${message._id}-${attachment.data_url.slice(0, 24)}-${index}`} className="chat-attachment-card">
                                        <img
                                          className="chat-attachment-image"
                                          src={attachment.data_url}
                                          alt={attachment.display_name || copy.agent.attachmentImageAlt}
                                          loading="lazy"
                                        />
                                        {attachmentMeta ? <figcaption className="chat-attachment-meta">{attachmentMeta}</figcaption> : null}
                                      </figure>
                                    );
                                  })}
                                </div>
                              ) : null}

                              {hasContent && streaming ? <span className="streaming-cursor" aria-hidden="true" /> : null}

                              {!hasRenderableBody && streaming ? (
                                <div className="chat-message-placeholder">
                                  <span>{copy.agent.typingPlaceholder}</span>
                                  <span className="typing-dots" aria-hidden="true">
                                    <span />
                                    <span />
                                    <span />
                                  </span>
                                </div>
                              ) : null}

                              {!hasRenderableBody && !streaming ? <p>{copy.common.emptyResponse}</p> : null}
                            </div>
                          </article>
                        </div>
                      </div>
                    );
                  }

                  return (
                    <section key={entry.key} className="chat-execution-block" data-testid="conversation-execution-block">
                      <div className="chat-execution-header">
                        <div>
                          <span className="eyebrow">{copy.agent.executionInlineTitle}</span>
                          <div className="chat-execution-title">{copy.agent.executionTitle}</div>
                        </div>
                        <span className="chat-execution-count">{entry.events.length}</span>
                      </div>

                      <div className="chat-execution-steps">
                        {entry.events.map((event) => {
                          const eventLabel = formatTimelineEventLabel(event.type);
                          const timelinePresentation = buildTimelinePresentation(event, copy);
                          const hasExecutionDetails = Boolean(
                            timelinePresentation.fields.length || timelinePresentation.blocks.length
                          );
                          const inlineSummary = getTimelinePreviewText(event) || getTimelineExpandedSummary(event);

                          return (
                            <article
                              key={event._id}
                              className={`chat-execution-step tone-${timelinePresentation.tone}`}
                              data-testid="conversation-execution-step"
                            >
                              <div className="chat-execution-step-topline">
                                <div className="chat-execution-step-heading">
                                  <span className={`timeline-stage-badge tone-${timelinePresentation.tone}`}>{timelinePresentation.stage}</span>
                                  <span className="chat-execution-step-title">{eventLabel}</span>
                                </div>
                                <span className="timeline-time">{formatClockTime(event.created_at, locale, copy.common.notStarted)}</span>
                              </div>

                              <p className="chat-execution-step-summary">{inlineSummary}</p>

                              {hasExecutionDetails ? (
                                <details className="chat-execution-step-details" data-testid="conversation-execution-step-details">
                                  <summary>{copy.agent.executionInlineDetails}</summary>
                                  {timelinePresentation.fields.length ? (
                                    <dl className="timeline-detail-grid">
                                      {timelinePresentation.fields.map((field) => (
                                        <div key={`${event._id}-${field.label}-${field.value}`} className="timeline-detail-row">
                                          <dt>{field.label}</dt>
                                          <dd className={`${field.mono ? "is-mono" : ""} tone-${field.tone ?? "neutral"}`}>{field.value}</dd>
                                        </div>
                                      ))}
                                    </dl>
                                  ) : null}

                                  {timelinePresentation.blocks.map((block) => (
                                    <div key={`${event._id}-${block.label}-${block.value.slice(0, 48)}`} className={`timeline-detail-block kind-${block.kind}`}>
                                      <div className="timeline-detail-block-label">{block.label}</div>
                                      {block.kind === "text" ? (
                                        <p className="timeline-detail-block-text">{block.value}</p>
                                      ) : (
                                        <pre className="timeline-event-data">{block.value}</pre>
                                      )}
                                    </div>
                                  ))}
                                </details>
                              ) : null}
                            </article>
                          );
                        })}
                      </div>
                    </section>
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

          <div
            className="composer-resizer"
            data-testid="composer-resizer"
            role="separator"
            aria-orientation="horizontal"
            aria-valuemin={MIN_COMPOSER_HEIGHT}
            aria-valuemax={Math.max(MIN_COMPOSER_HEIGHT, composerHeight)}
            aria-valuenow={composerHeight}
            onPointerDown={handleComposerResizeStart}
          />

          <div className="composer-shell">
            {currentSession && isResponding ? (
              <div className="composer-status" data-testid="composer-status">
                <span className="typing-dots" aria-hidden="true">
                  <span />
                  <span />
                  <span />
                </span>
                <span>{copy.agent.respondingStatus}</span>
              </div>
            ) : null}

            {composerAttachmentError ? <div className="inline-alert composer-alert">{composerAttachmentError}</div> : null}

            <div
              ref={composerCardRef}
              className={`composer-card${isDraggingComposer ? " is-dragging" : ""}`}
              onDragOver={handleComposerDragOver}
              onDragLeave={handleComposerDragLeave}
              onDrop={handleComposerDrop}
            >
              {pendingAttachments.length ? (
                <div ref={composerAttachmentsRef} className="composer-attachments">
                  {pendingAttachments.map((attachment) => {
                    const attachmentMeta = [
                      attachment.display_name,
                      attachment.width && attachment.height ? `${attachment.width} x ${attachment.height}` : null,
                    ]
                      .filter(Boolean)
                      .join(" · ");

                    return (
                      <figure key={attachment.id} className="composer-attachment" data-testid="composer-attachment">
                        <button
                          type="button"
                          className="composer-attachment-remove"
                          aria-label={copy.common.remove}
                          title={copy.common.remove}
                          onClick={() => handleRemoveAttachment(attachment.id)}
                        >
                          <svg viewBox="0 0 16 16" aria-hidden="true" focusable="false">
                            <path d="M6 2.5h4l.5 1H13a.75.75 0 0 1 0 1.5h-.6l-.55 7.18A1.75 1.75 0 0 1 10.1 13.8H5.9a1.75 1.75 0 0 1-1.75-1.62L3.6 5H3a.75.75 0 0 1 0-1.5h2.5l.5-1Zm-.9 2.5.54 7.06a.25.25 0 0 0 .26.24h4.2a.25.25 0 0 0 .26-.24L10.9 5H5.1ZM6.75 6.5a.75.75 0 0 1 .75.75v3.25a.75.75 0 0 1-1.5 0V7.25a.75.75 0 0 1 .75-.75Zm2.5 0a.75.75 0 0 1 .75.75v3.25a.75.75 0 0 1-1.5 0V7.25a.75.75 0 0 1 .75-.75Z" fill="currentColor"/>
                          </svg>
                        </button>
                        <img
                          className="composer-attachment-image"
                          src={attachment.data_url}
                          alt={attachment.display_name || copy.agent.attachmentImageAlt}
                        />
                        <figcaption className="composer-attachment-caption">{attachmentMeta}</figcaption>
                      </figure>
                    );
                  })}
                </div>
              ) : null}

              <textarea
                ref={composerTextareaRef}
                id="agent-prompt"
                rows={5}
                className="composer-textarea"
                placeholder={currentSession ? copy.agent.textareaActive : copy.agent.textareaInactive}
                value={prompt}
                disabled={!currentSession}
                onChange={(e: ChangeEvent<HTMLTextAreaElement>) => setPrompt(e.target.value)}
                onKeyDown={handlePromptKeyDown}
                onPaste={handleComposerPaste}
              />

              <div ref={composerFooterRef} className="composer-footer">
                <div className="composer-meta">
                  <span className="composer-hint">{copy.common.ctrlEnterHint}</span>
                  <span className="composer-hint">{copy.agent.composerImageHint}</span>
                  <span className="composer-hint">{copy.agent.composerResizeHint}</span>
                  <span className="composer-hint">{copy.common.autoRefreshHint}</span>
                </div>
                <div className="composer-actions">
                  <button
                    className="btn-primary send-button"
                    onClick={handleSend}
                    disabled={!currentSession || sending || (!prompt.trim() && !pendingAttachments.length)}
                  >
                    {sending ? copy.common.working : copy.common.send}
                  </button>
                </div>
              </div>
            </div>
          </div>
        </div>
      </section>

      <aside
        className="context-sidebar"
        data-testid="session-context-sidebar"
        hidden={isContextSidebarCollapsed}
      >
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
                <span className="panel-count">{timelineEvents.length}</span>
              </div>
              {timelineEvents.length ? (
                <div
                  ref={timelineListRef}
                  className="timeline-list panel-list-scroll"
                  data-testid="session-timeline"
                  onScroll={handleTimelineScroll}
                >
                  {timelineEvents.map((event) => {
                    const isExpanded = expandedTimelineEventIds.includes(event._id);
                    const eventLabel = formatTimelineEventLabel(event.type);
                    const previewText = getTimelinePreviewText(event);
                    const timelinePresentation = buildTimelinePresentation(event, copy);

                    return (
                    <div
                      key={event._id}
                      className={`timeline-entry ${isExpanded ? "expanded" : ""} tone-${timelinePresentation.tone}`}
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
                              <div className="timeline-summary-heading">
                                <span className={`timeline-stage-badge tone-${timelinePresentation.tone}`}>{timelinePresentation.stage}</span>
                                <span className="timeline-type">{eventLabel}</span>
                              </div>
                              <span className="timeline-time">{formatClockTime(event.created_at, locale, copy.common.notStarted)}</span>
                            </div>
                            {previewText ? <div className="timeline-summary-preview">{previewText}</div> : null}
                          </div>
                        </div>
                      </button>
                      {isExpanded ? (
                        <div className="timeline-entry-body">
                          <div className="timeline-body-header">
                            <span className={`timeline-tone-marker tone-${timelinePresentation.tone}`} aria-hidden="true" />
                            <div className="timeline-body-header-copy">
                              <div className="timeline-event-code">{event.type}</div>
                              <p className="timeline-summary-full">{getTimelineExpandedSummary(event)}</p>
                            </div>
                          </div>

                          {timelinePresentation.fields.length ? (
                            <dl className="timeline-detail-grid">
                              {timelinePresentation.fields.map((field) => (
                                <div key={`${field.label}-${field.value}`} className="timeline-detail-row">
                                  <dt>{field.label}</dt>
                                  <dd className={`${field.mono ? "is-mono" : ""} tone-${field.tone ?? "neutral"}`}>{field.value}</dd>
                                </div>
                              ))}
                            </dl>
                          ) : null}

                          {timelinePresentation.blocks.map((block) => (
                            <div key={`${block.label}-${block.value.slice(0, 48)}`} className={`timeline-detail-block kind-${block.kind}`}>
                              <div className="timeline-detail-block-label">{block.label}</div>
                              {block.kind === "text" ? (
                                <p className="timeline-detail-block-text">{block.value}</p>
                              ) : (
                                <pre className="timeline-event-data">{block.value}</pre>
                              )}
                            </div>
                          ))}

                          {timelinePresentation.rawPayload ? (
                            <details className="timeline-raw-details">
                              <summary>{copy.agent.timelineDetails.labels.rawData}</summary>
                              <pre className="timeline-event-data">{timelinePresentation.rawPayload}</pre>
                            </details>
                          ) : null}
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

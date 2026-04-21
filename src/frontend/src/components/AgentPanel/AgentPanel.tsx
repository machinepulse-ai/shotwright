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
  AgentSessionStreamConnection,
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
const DEFAULT_COMPOSER_HEIGHT = 172;
const MIN_COMPOSER_HEIGHT = 136;
const MIN_TRANSCRIPT_HEIGHT = 220;
const COMPOSER_SPLITTER_HEIGHT = 14;
const COMPOSER_TEXTAREA_MIN_HEIGHT = 76;
const COMPOSER_HEIGHT_STORAGE_KEY = "shotwright_composer_height";
const DEFAULT_SESSION_SIDEBAR_WIDTH = 232;
const MIN_SESSION_SIDEBAR_WIDTH = 196;
const MAX_SESSION_SIDEBAR_WIDTH = 360;
const DEFAULT_CONTEXT_SIDEBAR_WIDTH = 392;
const MIN_CONTEXT_SIDEBAR_WIDTH = 320;
const MAX_CONTEXT_SIDEBAR_WIDTH = 520;
const SIDEBAR_RESIZER_WIDTH = 14;
const MIN_CHAT_STAGE_WIDTH = 480;
const SESSION_SIDEBAR_WIDTH_STORAGE_KEY = "shotwright_session_sidebar_width";
const CONTEXT_SIDEBAR_WIDTH_STORAGE_KEY = "shotwright_context_sidebar_width";

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
  kind: "markdown" | "code" | "error";
};

type TimelinePresentation = {
  tone: TimelineTone;
  stage: string;
  fields: TimelineDetailField[];
  blocks: TimelineDetailBlock[];
};

type ExecutionStepPresentation = {
  key: string;
  title: string;
  preview: string;
  tone: TimelineTone;
  leadEvent: SessionEvent;
  timelinePresentation: TimelinePresentation;
};

type ExecutionGroupPresentation = {
  key: string;
  title: string;
  preview: string;
  tone: TimelineTone;
  statusLabel: string;
  stepCountLabel: string;
  timelinePresentation: TimelinePresentation | null;
  steps: ExecutionStepPresentation[];
};

type ExecutionStepDraft = {
  startEvent: SessionEvent | null;
  completeEvent: SessionEvent | null;
  relatedEvents: SessionEvent[];
};

type ExecutionGroupDraft = {
  key: string;
  headerEvent: SessionEvent | null;
  fallbackTitle: string | null;
  events: SessionEvent[];
  steps: ExecutionStepDraft[];
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

function readStoredDimension(storageKey: string, fallbackValue: number, min: number, max: number) {
  if (typeof window === "undefined") {
    return fallbackValue;
  }

  const storedValue = Number(window.localStorage.getItem(storageKey));
  return Number.isFinite(storedValue) ? clamp(storedValue, min, max) : fallbackValue;
}

function isScrolledNearBottom(element: HTMLElement, threshold = 32) {
  return element.scrollTop + element.clientHeight >= element.scrollHeight - threshold;
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

function renderExecutionMarker(tone: TimelineTone, variant: "group" | "step") {
  return (
    <span className={`chat-execution-status-icon ${variant === "group" ? "is-group" : "is-step"} tone-${tone}`} aria-hidden="true">
      <svg viewBox="0 0 16 16" focusable="false">
        {tone === "success" ? (
          <path d="M4.2 8.15 6.8 10.8 11.8 5.8" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
        ) : tone === "danger" ? (
          <path d="M5.2 5.2 10.8 10.8M10.8 5.2 5.2 10.8" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
        ) : tone === "accent" ? (
          <>
            <circle cx="8" cy="8" r="4.5" fill="none" stroke="currentColor" strokeWidth="1.8" opacity="0.9" />
            <path d="M8 5.4v2.9l2.1 1.5" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
          </>
        ) : (
          <circle cx="8" cy="8" r="2.25" fill="currentColor" />
        )}
      </svg>
    </span>
  );
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

function tryParseJsonText(value: string): unknown | null {
  const trimmed = value.trim();
  if (!trimmed || (!trimmed.startsWith("{") && !trimmed.startsWith("["))) {
    return null;
  }

  try {
    return JSON.parse(trimmed);
  } catch {
    return null;
  }
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
    if (!trimmed) return null;

    const parsed = tryParseJsonText(trimmed);
    if (parsed !== null) {
      return compactEventPayload(parsed);
    }

    return trimmed ? trimmed : null;
  }

  return value;
}

function extractEventText(value: unknown): string {
  if (typeof value === "string") {
    const trimmed = value.trim();
    if (!trimmed) return "";

    const parsed = tryParseJsonText(trimmed);
    if (parsed !== null) {
      const parsedText = extractEventText(parsed);
      if (parsedText) return parsedText;
    }

    return trimmed;
  }
  if (typeof value === "number" || typeof value === "boolean") return String(value);

  if (Array.isArray(value)) {
    return value.map((entry) => extractEventText(entry)).filter(Boolean).join(", ");
  }

  if (value && typeof value === "object") {
    const record = value as Record<string, unknown>;

    const preferredText = [
      record.message,
      record.error,
      record.summary,
      record.content,
      record.reason,
      record.intent,
      record.title,
      record.display_name,
      record.name,
      record.filename,
      record.entry_aep_file,
      record.tool_name,
      record.status,
      record.image,
    ]
      .map((entry) => extractEventText(entry))
      .find(Boolean);

    if (preferredText) return preferredText;

    return Object.values(record)
      .map((entry) => extractEventText(entry))
      .find(Boolean) || "";
  }

  return "";
}

function trimPreviewText(value: string, maxLength = 140) {
  return value.length > maxLength ? `${value.slice(0, maxLength - 3)}...` : value;
}

function collectPreviewTokens(value: unknown): string[] {
  const compactValue = compactEventPayload(value);
  if (compactValue == null) return [];

  if (typeof compactValue === "string") {
    return compactValue ? [trimPreviewText(compactValue)] : [];
  }

  if (typeof compactValue === "number" || typeof compactValue === "boolean") {
    return [String(compactValue)];
  }

  if (Array.isArray(compactValue)) {
    if (!compactValue.length) return [];
    return compactValue.flatMap((entry) => collectPreviewTokens(entry));
  }

  const record = compactValue as Record<string, unknown>;

  if (Array.isArray(record.projects)) {
    if (!record.projects.length) {
      return ["No uploaded projects"];
    }

    return record.projects.flatMap((entry) => collectPreviewTokens(entry));
  }

  const preferredKeys = [
    "summary",
    "message",
    "error",
    "content",
    "reason",
    "intent",
    "display_name",
    "name",
    "filename",
    "entry_aep_file",
    "tool_name",
    "status",
    "image",
    "path",
  ];

  const preferredTokens = preferredKeys
    .flatMap((key) => collectPreviewTokens(record[key]))
    .filter(Boolean);

  if (preferredTokens.length) {
    return preferredTokens;
  }

  return Object.entries(record)
    .flatMap(([key, entry]) => {
      if (entry == null) return [];

      const text = extractEventText(entry);
      if (!text) return [];
      if (["session_id", "container_id", "docker_id", "project_id", "workspace_dir"].includes(key)) {
        return [];
      }

      return [trimPreviewText(`${key}: ${text}`)];
    })
    .filter(Boolean);
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

function formatTimelineMarkdownFieldLabel(value: string) {
  return value
    .split(/[_\s]+/)
    .filter(Boolean)
    .map((segment) => segment.charAt(0).toUpperCase() + segment.slice(1))
    .join(" ");
}

function unwrapTimelineMarkdownValue(value: unknown): unknown {
  const compactValue = compactEventPayload(value);
  if (!compactValue || typeof compactValue !== "object" || Array.isArray(compactValue)) {
    return compactValue;
  }

  const record = compactValue as Record<string, unknown>;
  const meaningfulEntries = Object.entries(record).filter(([, entry]) => compactEventPayload(entry) !== null);
  const wrapperKeys = new Set(["content", "detailed_content", "contents", "kind"]);
  const nonWrapperEntries = meaningfulEntries.filter(([key]) => !wrapperKeys.has(key));

  if (!nonWrapperEntries.length) {
    return unwrapTimelineMarkdownValue(record.detailed_content ?? record.content ?? record.contents ?? compactValue);
  }

  return compactValue;
}

function buildTimelineMarkdownLines(value: unknown, depth = 0): string[] {
  const unwrappedValue = unwrapTimelineMarkdownValue(value);
  if (unwrappedValue == null) {
    return [];
  }

  const indent = "  ".repeat(depth);

  if (typeof unwrappedValue === "string") {
    return unwrappedValue
      .trim()
      .split(/\r?\n/)
      .filter(Boolean)
      .map((line) => `${indent}${line}`);
  }

  if (typeof unwrappedValue === "number" || typeof unwrappedValue === "boolean") {
    return [`${indent}${String(unwrappedValue)}`];
  }

  if (Array.isArray(unwrappedValue)) {
    return unwrappedValue.flatMap((entry) => {
      const childLines = buildTimelineMarkdownLines(entry, depth + 1);
      if (!childLines.length) return [];
      const [firstLine, ...restLines] = childLines;
      return [`${indent}- ${firstLine.trimStart()}`, ...restLines.map((line) => `${indent}  ${line.trimStart()}`)];
    });
  }

  const record = unwrappedValue as Record<string, unknown>;
  return Object.entries(record).flatMap(([key, entry]) => {
    const childLines = buildTimelineMarkdownLines(entry, depth + 1);
    if (!childLines.length) return [];

    const label = formatTimelineMarkdownFieldLabel(key);
    if (childLines.length === 1 && !childLines[0].trimStart().startsWith("- ")) {
      return [`${indent}- **${label}**: ${childLines[0].trim()}`];
    }

    return [`${indent}- **${label}**:`, ...childLines.map((line) => `${indent}  ${line.trimStart()}`)];
  });
}

function formatEventMarkdownValue(value: unknown): string {
  return buildTimelineMarkdownLines(value).join("\n").trim();
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

function getCompactEventPayloadRecord(event: SessionEvent): Record<string, unknown> {
  const compactData = compactEventPayload(event.data);
  return compactData && typeof compactData === "object" && !Array.isArray(compactData)
    ? (compactData as Record<string, unknown>)
    : {};
}

function getTimelinePreviewText(event: SessionEvent) {
  if (event.summary !== event.type) {
    return event.summary;
  }

  const payload = getCompactEventPayloadRecord(event);

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
  const payload = getCompactEventPayloadRecord(event);
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

  const addBlock = (label: string, value: unknown, kind: TimelineDetailBlock["kind"] = "markdown") => {
    const formattedValue = (kind === "code" ? formatEventBlockValue(value) : formatEventMarkdownValue(value)).trim();
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
    addBlock(copy.agent.timelineDetails.labels.message, messageText, tone === "danger" ? "error" : "markdown");
  }

  const commandValue = formatCommandValue(payload.command, payload.args);
  if (commandValue) {
    addBlock(copy.agent.timelineDetails.labels.command, commandValue, "code");
  }

  addBlock(copy.agent.timelineDetails.labels.arguments, payload.arguments, "markdown");
  addBlock(copy.agent.timelineDetails.labels.input, payload.input, "markdown");
  addBlock(copy.agent.timelineDetails.labels.output, payload.output ?? payload.result, "markdown");

  if (typeof payload.content === "string") {
    if (payload.content.length <= 600) {
      addBlock(copy.agent.timelineDetails.labels.content, payload.content, "markdown");
    }
  } else {
    addBlock(copy.agent.timelineDetails.labels.content, payload.content, "markdown");
  }

  return {
    tone,
    stage: getTimelineEventStage(event, copy),
    fields,
    blocks,
  };
}

function formatExecutionStepCount(stepCount: number, locale: string) {
  return locale === "zh-CN" ? `子步骤 ${stepCount}` : `${stepCount} substeps`;
}

function formatExecutionOverflowCount(extraCount: number, locale: string) {
  return locale === "zh-CN" ? `+${extraCount} 项` : `+${extraCount} more`;
}

function summarizePreviewItems(items: string[], locale: string, limit = 2) {
  const uniqueItems = Array.from(new Set(items.map((item) => item.trim()).filter(Boolean)));
  const previewItems = uniqueItems.slice(0, limit);
  const extraCount = uniqueItems.length - previewItems.length;

  return [
    ...previewItems,
    extraCount > 0 ? formatExecutionOverflowCount(extraCount, locale) : null,
  ]
    .filter(Boolean)
    .join(" · ");
}

function getEventToolName(event: SessionEvent | null): string | null {
  if (!event) return null;
  const payload = getCompactEventPayloadRecord(event);
  const toolName = payload.tool_name ?? payload.toolName ?? payload.name;
  return typeof toolName === "string" && toolName.trim() ? toolName.trim() : null;
}

function humanizeToolName(toolName: string) {
  const overrides: Record<string, string> = {
    rg: "Ran Rg",
    glob: "Ran Glob",
    glob_search: "Ran Glob",
    report_intent: "Planned next step",
    inspect_workspace: "Inspect workspace",
    list_uploaded_projects: "Checked uploaded projects",
    ensure_after_effects_container: "Ensure After Effects container",
    create_after_effects_project: "Create managed project",
    run_after_effects_jsx: "Run After Effects JSX",
    stop_after_effects_container: "Stop After Effects container",
    read_file: "Read file",
    view: "Read file",
  };

  if (overrides[toolName]) {
    return overrides[toolName];
  }

  return toolName
    .split(/[._-]+/)
    .filter(Boolean)
    .map((segment) => segment.charAt(0).toUpperCase() + segment.slice(1))
    .join(" ");
}

function isGenericToolSummary(summary: string) {
  return /^Tool (start|complete):/i.test(summary) || summary === "tool.execution_start" || summary === "tool.execution_complete";
}

function buildMergedExecutionEvent(stepDraft: ExecutionStepDraft): SessionEvent | null {
  const leadEvent = stepDraft.completeEvent ?? stepDraft.startEvent ?? stepDraft.relatedEvents[stepDraft.relatedEvents.length - 1] ?? null;
  if (!leadEvent) return null;

  const startPayload = stepDraft.startEvent?.data ?? {};
  const completePayload = stepDraft.completeEvent?.data ?? {};

  return {
    ...leadEvent,
    data: {
      ...startPayload,
      ...completePayload,
      tool_name: completePayload.tool_name ?? startPayload.tool_name,
      toolName: completePayload.toolName ?? startPayload.toolName,
      name: completePayload.name ?? startPayload.name,
      arguments: completePayload.arguments ?? startPayload.arguments,
      input: completePayload.input ?? startPayload.input,
    },
  };
}

function getStepTitleFromEvents(stepDraft: ExecutionStepDraft, mergedEvent: SessionEvent) {
  const completeSummary = stepDraft.completeEvent ? getTimelineExpandedSummary(stepDraft.completeEvent).trim() : "";
  if (completeSummary && !isGenericToolSummary(completeSummary) && completeSummary !== stepDraft.completeEvent?.type) {
    return completeSummary;
  }

  const startSummary = stepDraft.startEvent ? getTimelineExpandedSummary(stepDraft.startEvent).trim() : "";
  if (startSummary && !isGenericToolSummary(startSummary) && startSummary !== stepDraft.startEvent?.type) {
    return startSummary;
  }

  const toolName = getEventToolName(mergedEvent) ?? getEventToolName(stepDraft.startEvent) ?? getEventToolName(stepDraft.completeEvent);
  if (toolName === "report_intent") {
    return extractEventText(stepDraft.startEvent?.data?.arguments ?? stepDraft.completeEvent?.data?.arguments) || humanizeToolName(toolName);
  }

  return toolName ? humanizeToolName(toolName) : getTimelineExpandedSummary(mergedEvent);
}

function getStepPreviewFromEvents(stepDraft: ExecutionStepDraft, mergedEvent: SessionEvent, locale: string) {
  const payload = getCompactEventPayloadRecord(mergedEvent);
  const errorText = extractEventText(payload.error);
  if (errorText) {
    return trimPreviewText(errorText);
  }

  const previewTokens = collectPreviewTokens(payload.output ?? payload.result ?? payload.content ?? payload.message ?? payload);
  const title = getStepTitleFromEvents(stepDraft, mergedEvent);
  const filteredTokens = previewTokens.filter((token) => token && token !== title);
  if (filteredTokens.length) {
    return summarizePreviewItems(filteredTokens, locale, 3);
  }

  const fallbackPreview = getTimelinePreviewText(mergedEvent);
  return fallbackPreview !== title ? fallbackPreview : "";
}

function buildExecutionStepPresentation(
  stepDraft: ExecutionStepDraft,
  locale: string,
  copy: TranslationCopy,
): ExecutionStepPresentation | null {
  const mergedEvent = buildMergedExecutionEvent(stepDraft);
  if (!mergedEvent) return null;

  const toolName = getEventToolName(mergedEvent) ?? getEventToolName(stepDraft.startEvent) ?? getEventToolName(stepDraft.completeEvent);
  if (toolName === "report_intent") {
    return null;
  }

  const timelinePresentation = buildTimelinePresentation(mergedEvent, copy);
  return {
    key: stepDraft.completeEvent?._id ?? stepDraft.startEvent?._id ?? mergedEvent._id,
    title: getStepTitleFromEvents(stepDraft, mergedEvent),
    preview: getStepPreviewFromEvents(stepDraft, mergedEvent, locale),
    tone: timelinePresentation.tone,
    leadEvent: mergedEvent,
    timelinePresentation,
  };
}

function getIntentLoggedCompletionDetail(event: SessionEvent) {
  if (getEventToolName(event)) {
    return null;
  }

  const payload = getCompactEventPayloadRecord(event);
  const result = payload.result;
  if (!result || typeof result !== "object" || Array.isArray(result)) {
    return null;
  }

  const resultRecord = result as Record<string, unknown>;
  const content = extractEventText(resultRecord.content);
  if (content !== "Intent logged") {
    return null;
  }

  return extractEventText(resultRecord.detailed_content) || extractEventText(resultRecord.intent) || content;
}

function pickActiveStepForEvent(activeSteps: ExecutionStepDraft[], event: SessionEvent) {
  const eventToolName = getEventToolName(event);
  if (eventToolName) {
    const matchingStep = [...activeSteps].reverse().find((step) => getEventToolName(step.startEvent) === eventToolName);
    if (matchingStep) {
      return matchingStep;
    }
  }

  return activeSteps[activeSteps.length - 1] ?? null;
}

function takeCompletedStep(activeSteps: ExecutionStepDraft[], event: SessionEvent) {
  const eventToolName = getEventToolName(event);
  let targetIndex = -1;

  if (eventToolName) {
    targetIndex = activeSteps.findIndex((step) => getEventToolName(step.startEvent) === eventToolName);
  }

  if (targetIndex < 0) {
    targetIndex = 0;
  }

  if (targetIndex < 0 || !activeSteps[targetIndex]) {
    return null;
  }

  const [stepDraft] = activeSteps.splice(targetIndex, 1);
  stepDraft.completeEvent = event;
  stepDraft.relatedEvents.push(event);
  return stepDraft;
}

function flushActiveSteps(activeSteps: ExecutionStepDraft[], groupDraft: ExecutionGroupDraft) {
  while (activeSteps.length) {
    const stepDraft = activeSteps.shift();
    if (stepDraft) {
      groupDraft.steps.push(stepDraft);
    }
  }
}

function buildGroupTitle(groupDraft: ExecutionGroupDraft, steps: ExecutionStepPresentation[]) {
  const headerEvent = groupDraft.headerEvent;
  if (headerEvent?.type === "assistant.intent") {
    return extractEventText(headerEvent.data.intent) || groupDraft.fallbackTitle || steps[0]?.title || getTimelineExpandedSummary(headerEvent);
  }

  if (headerEvent) {
    return getTimelineExpandedSummary(headerEvent);
  }

  return groupDraft.fallbackTitle || steps[0]?.title || "Execution";
}

function getGroupTone(groupDraft: ExecutionGroupDraft, steps: ExecutionStepPresentation[]) {
  const tones = [
    ...steps.map((step) => step.tone),
    ...(groupDraft.headerEvent ? [getTimelineEventTone(groupDraft.headerEvent, getCompactEventPayloadRecord(groupDraft.headerEvent))] : []),
  ];

  if (tones.includes("danger")) return "danger";
  if (tones.includes("success")) return "success";
  if (tones.includes("accent")) return "accent";
  if (tones.includes("muted")) return "muted";
  return "neutral";
}

function buildExecutionGroupPresentation(
  groupDraft: ExecutionGroupDraft,
  locale: string,
  copy: TranslationCopy,
): ExecutionGroupPresentation | null {
  const steps = groupDraft.steps
    .map((stepDraft) => buildExecutionStepPresentation(stepDraft, locale, copy))
    .filter((step): step is ExecutionStepPresentation => Boolean(step));

  if (!steps.length && !groupDraft.headerEvent && !groupDraft.fallbackTitle) {
    return null;
  }

  const title = buildGroupTitle(groupDraft, steps);
  const tone = getGroupTone(groupDraft, steps);
  const preview = summarizePreviewItems(
    steps
      .map((step) => step.preview || step.title)
      .filter((value) => value && value !== title),
    locale,
    2,
  );
  const fallbackStatusLabel =
    tone === "danger"
      ? copy.agent.timelineDetails.result.failure
      : tone === "success"
        ? copy.agent.timelineDetails.result.success
        : copy.agent.timelineDetails.result.pending;
  const lastStep = steps[steps.length - 1] ?? null;
  const statusLabel = lastStep
    ? getTimelineResultLabel(lastStep.leadEvent, getCompactEventPayloadRecord(lastStep.leadEvent), copy) ?? fallbackStatusLabel
    : fallbackStatusLabel;
  const timelinePresentation =
    groupDraft.headerEvent && groupDraft.headerEvent.type !== "assistant.intent"
      ? buildTimelinePresentation(groupDraft.headerEvent, copy)
      : null;

  return {
    key: groupDraft.key,
    title,
    preview,
    tone,
    statusLabel,
    stepCountLabel: formatExecutionStepCount(steps.length, locale),
    timelinePresentation,
    steps,
  };
}

function buildExecutionGroups(events: SessionEvent[], locale: string, copy: TranslationCopy) {
  const groups: ExecutionGroupPresentation[] = [];
  let currentGroup: ExecutionGroupDraft | null = null;
  let activeSteps: ExecutionStepDraft[] = [];

  const ensureGroup = () => {
    if (!currentGroup) {
      currentGroup = {
        key: `group-${events[0]?._id ?? Date.now()}`,
        headerEvent: null,
        fallbackTitle: null,
        events: [],
        steps: [],
      };
    }

    return currentGroup;
  };

  const finalizeCurrentGroup = () => {
    if (!currentGroup) return;

    flushActiveSteps(activeSteps, currentGroup);
    const group = buildExecutionGroupPresentation(currentGroup, locale, copy);
    if (group) {
      groups.push(group);
    }
    currentGroup = null;
  };

  for (const event of events) {
    const isHeaderEvent = event.type === "assistant.intent" || event.type.startsWith("skill.") || event.type.startsWith("agent.") || event.type.startsWith("subagent.");

    if (isHeaderEvent) {
      finalizeCurrentGroup();
      currentGroup = {
        key: `group-${event._id}`,
        headerEvent: event,
        fallbackTitle: event.type === "assistant.intent" ? extractEventText(event.data.intent) : null,
        events: [event],
        steps: [],
      };
      continue;
    }

    const groupDraft = ensureGroup();
    groupDraft.events.push(event);

    if (event.type === "tool.execution_start") {
      const toolName = getEventToolName(event);
      if (toolName === "report_intent") {
        groupDraft.fallbackTitle = extractEventText(event.data.arguments) || groupDraft.fallbackTitle;
        continue;
      }

      activeSteps.push({
        startEvent: event,
        completeEvent: null,
        relatedEvents: [event],
      });
      continue;
    }

    if (event.type === "tool.execution_complete") {
      const intentLoggedDetail = getIntentLoggedCompletionDetail(event);
      if (intentLoggedDetail) {
        groupDraft.fallbackTitle = intentLoggedDetail || groupDraft.fallbackTitle;
        continue;
      }

      const completedStep = takeCompletedStep(activeSteps, event);
      if (completedStep) {
        groupDraft.steps.push(completedStep);
      } else {
        groupDraft.steps.push({
          startEvent: null,
          completeEvent: event,
          relatedEvents: [event],
        });
      }
      continue;
    }

    if (
      event.type === "external_tool.requested" ||
      event.type === "external_tool.completed" ||
      event.type === "permission.requested" ||
      event.type === "permission.completed"
    ) {
      const activeStep = pickActiveStepForEvent(activeSteps, event);
      if (activeStep) {
        activeStep.relatedEvents.push(event);
      }
      continue;
    }

    groupDraft.steps.push({
      startEvent: event,
      completeEvent: null,
      relatedEvents: [event],
    });
  }

  finalizeCurrentGroup();
  return groups;
}

function getExecutionLeadEvent(events: SessionEvent[]) {
  return (
    events.find((event) => event.type.startsWith("skill.") && /(invoked|requested|start|submitted)/.test(event.type)) ??
    events.find((event) => event.type.startsWith("tool.") && /(invoked|requested|start|submitted|complete)/.test(event.type)) ??
    events.find((event) => !event.type.startsWith("session.")) ??
    events[0]
  );
}

function getExecutionBlockTone(events: SessionEvent[]) {
  const tones = events.map((event) => getTimelineEventTone(event, getCompactEventPayloadRecord(event)));

  if (tones.includes("danger")) return "danger";
  if (tones.includes("success")) return "success";
  if (tones.includes("accent")) return "accent";
  if (tones.includes("muted")) return "muted";
  return "neutral";
}

function buildExecutionBlockPresentation(
  events: SessionEvent[],
  locale: string,
  copy: TranslationCopy,
): ExecutionGroupPresentation {
  const leadEvent = getExecutionLeadEvent(events);
  const tone = getExecutionBlockTone(events);
  const fallbackStatusLabel =
    tone === "danger"
      ? copy.agent.timelineDetails.result.failure
      : tone === "success"
        ? copy.agent.timelineDetails.result.success
        : copy.agent.timelineDetails.result.pending;
  const lastEvent = events[events.length - 1] ?? leadEvent;
  const statusLabel =
    getTimelineResultLabel(lastEvent, getCompactEventPayloadRecord(lastEvent), copy) ?? fallbackStatusLabel;

  const summaryCandidates = Array.from(
    new Set(
      events
        .filter((event) => event._id !== leadEvent._id && !event.type.startsWith("session."))
        .map((event) => getTimelineExpandedSummary(event).trim())
        .filter(Boolean),
    ),
  );

  const previewItems = summaryCandidates.slice(0, 2);
  const extraCount = summaryCandidates.length - previewItems.length;
  const summary = [
    ...previewItems,
    extraCount > 0 ? formatExecutionOverflowCount(extraCount, locale) : null,
  ]
    .filter(Boolean)
    .join(" · ");

  return {
    key: leadEvent._id,
    title: getTimelineExpandedSummary(leadEvent),
    preview: summary,
    tone,
    statusLabel,
    stepCountLabel: formatExecutionStepCount(events.length, locale),
    timelinePresentation: buildTimelinePresentation(leadEvent, copy),
    steps: events
      .map((event) => buildExecutionStepPresentation({ startEvent: event, completeEvent: null, relatedEvents: [event] }, locale, copy))
      .filter((step): step is ExecutionStepPresentation => Boolean(step)),
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

  if (event.type === "assistant.intent") return true;
  if (event.type.startsWith("tool.")) return true;
  if (event.type.startsWith("skill.")) return true;
  if (event.type.startsWith("external_tool.")) return true;
  if (event.type.startsWith("permission.")) return true;
  if (event.type.startsWith("agent.") || event.type.startsWith("subagent.")) return true;
  if (event.type === "session.timeout" || event.type === "session.error") return true;

  return false;
}

function buildExecutionEventsByTurn(events: SessionEvent[]) {
  const eventsByTurn = new Map<string, SessionEvent[]>();

  for (const event of events) {
    if (!shouldRenderInlineExecutionEvent(event)) continue;
    const turnId = getEventTurnId(event);
    if (!turnId) continue;
    const turnEvents = eventsByTurn.get(turnId) ?? [];
    turnEvents.push(event);
    eventsByTurn.set(turnId, turnEvents);
  }

  return eventsByTurn;
}

function buildTranscriptEntries(messages: ChatMessage[], eventsByTurn: Map<string, SessionEvent[]>): TranscriptEntry[] {
  const entries: TranscriptEntry[] = [];
  const assistantTurnIds = new Set(
    messages
      .filter((message) => message.role === "assistant")
      .map((message) => getMessageTurnId(message))
      .filter((turnId): turnId is string => Boolean(turnId)),
  );

  for (const message of messages) {
    entries.push({ kind: "message", key: `message-${message._id}`, message });
  }

  for (const [turnId, turnEvents] of eventsByTurn) {
    if (assistantTurnIds.has(turnId)) continue;
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
  const [sessionSidebarWidth, setSessionSidebarWidth] = useState(() =>
    readStoredDimension(
      SESSION_SIDEBAR_WIDTH_STORAGE_KEY,
      DEFAULT_SESSION_SIDEBAR_WIDTH,
      MIN_SESSION_SIDEBAR_WIDTH,
      MAX_SESSION_SIDEBAR_WIDTH,
    ),
  );
  const [contextSidebarWidth, setContextSidebarWidth] = useState(() =>
    readStoredDimension(
      CONTEXT_SIDEBAR_WIDTH_STORAGE_KEY,
      DEFAULT_CONTEXT_SIDEBAR_WIDTH,
      MIN_CONTEXT_SIDEBAR_WIDTH,
      MAX_CONTEXT_SIDEBAR_WIDTH,
    ),
  );
  const [sendingSessionId, setSendingSessionId] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);
  const [modelOptions, setModelOptions] = useState<CopilotModelOption[]>([]);
  const [modelOptionsLoading, setModelOptionsLoading] = useState(false);
  const [draftModel, setDraftModel] = useState("");
  const [draftReasoning, setDraftReasoning] = useState<ReasoningEffort | null>(null);
  const [savingSessionSettings, setSavingSessionSettings] = useState(false);
  const [sessionsError, setSessionsError] = useState<UiError | null>(null);
  const [panelError, setPanelError] = useState<UiError | null>(null);
  const [sessionSettingsError, setSessionSettingsError] = useState<UiError | null>(null);
  const [editingSessionName, setEditingSessionName] = useState(false);
  const [draftSessionName, setDraftSessionName] = useState("");
  const [savingSessionName, setSavingSessionName] = useState(false);
  const messageEndRef = useRef<HTMLDivElement | null>(null);
  const workbenchRef = useRef<HTMLDivElement | null>(null);
  const transcriptRef = useRef<HTMLDivElement | null>(null);
  const renameInputRef = useRef<HTMLInputElement | null>(null);
  const composerTextareaRef = useRef<HTMLTextAreaElement | null>(null);
  const composerCardRef = useRef<HTMLDivElement | null>(null);
  const composerFooterRef = useRef<HTMLDivElement | null>(null);
  const composerAttachmentsRef = useRef<HTMLDivElement | null>(null);
  const chatStageBodyRef = useRef<HTMLDivElement | null>(null);
  const activeSessionIdRef = useRef<string | null>(null);
  const streamRef = useRef<AgentSessionStreamConnection | null>(null);
  const shouldFollowTranscriptRef = useRef(true);
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

  const executionEventsByTurn = useMemo(() => buildExecutionEventsByTurn(sortedEvents), [sortedEvents]);

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
    () => buildTranscriptEntries(visibleMessages, executionEventsByTurn),
    [executionEventsByTurn, visibleMessages]
  );

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

    if (!shouldFollowTranscriptRef.current) return;

    messageEndRef.current?.scrollIntoView({ block: "end" });
  }, [currentSession?._id, lastVisibleMessageContent, transcriptEntries.length, visibleMessages.length, sortedEvents.length]);

  useEffect(() => {
    const transcript = transcriptRef.current;
    if (!transcript) return;

    const handleTranscriptScroll = () => {
      shouldFollowTranscriptRef.current = isScrolledNearBottom(transcript, 72);
    };

    shouldFollowTranscriptRef.current = true;
    handleTranscriptScroll();
    transcript.addEventListener("scroll", handleTranscriptScroll);

    return () => transcript.removeEventListener("scroll", handleTranscriptScroll);
  }, [currentSession?._id]);

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
    if (typeof window === "undefined") return;
    window.localStorage.setItem(SESSION_SIDEBAR_WIDTH_STORAGE_KEY, String(sessionSidebarWidth));
  }, [sessionSidebarWidth]);

  useEffect(() => {
    if (typeof window === "undefined") return;
    window.localStorage.setItem(CONTEXT_SIDEBAR_WIDTH_STORAGE_KEY, String(contextSidebarWidth));
  }, [contextSidebarWidth]);

  const getMaxSessionSidebarWidth = () => {
    const workbench = workbenchRef.current;
    if (!workbench) return MAX_SESSION_SIDEBAR_WIDTH;

    const oppositeWidth = isContextSidebarCollapsed ? 0 : contextSidebarWidth + SIDEBAR_RESIZER_WIDTH;
    return Math.max(
      MIN_SESSION_SIDEBAR_WIDTH,
      Math.min(
        MAX_SESSION_SIDEBAR_WIDTH,
        workbench.clientWidth - oppositeWidth - SIDEBAR_RESIZER_WIDTH - MIN_CHAT_STAGE_WIDTH,
      ),
    );
  };

  const getMaxContextSidebarWidth = () => {
    const workbench = workbenchRef.current;
    if (!workbench) return MAX_CONTEXT_SIDEBAR_WIDTH;

    const oppositeWidth = isSessionSidebarCollapsed ? 0 : sessionSidebarWidth + SIDEBAR_RESIZER_WIDTH;
    return Math.max(
      MIN_CONTEXT_SIDEBAR_WIDTH,
      Math.min(
        MAX_CONTEXT_SIDEBAR_WIDTH,
        workbench.clientWidth - oppositeWidth - SIDEBAR_RESIZER_WIDTH - MIN_CHAT_STAGE_WIDTH,
      ),
    );
  };

  const clampSidebarWidths = () => {
    setSessionSidebarWidth((previous) => clamp(previous, MIN_SESSION_SIDEBAR_WIDTH, getMaxSessionSidebarWidth()));
    setContextSidebarWidth((previous) => clamp(previous, MIN_CONTEXT_SIDEBAR_WIDTH, getMaxContextSidebarWidth()));
  };

  useEffect(() => {
    clampComposerHeight();

    const handleResize = () => {
      clampComposerHeight();
      clampSidebarWidths();
      resizeComposerTextarea();
    };
    window.addEventListener("resize", handleResize);
    return () => window.removeEventListener("resize", handleResize);
  }, [contextSidebarWidth, isContextSidebarCollapsed, isSessionSidebarCollapsed, sessionSidebarWidth]);

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

  useEffect(() => {
    if (typeof window === "undefined") return;
    if (!currentSession || sessionStatus !== "running") return;

    const sessionId = currentSession._id;
    const intervalId = window.setInterval(() => {
      if (activeSessionIdRef.current !== sessionId) return;

      void loadCurrentSession(sessionId).catch((err) => {
        setPanelError(buildUiError(err, "failedLoadSessionData"));
      });
    }, 1200);

    return () => window.clearInterval(intervalId);
  }, [currentSession, sessionStatus]);

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

  const handleSessionSidebarResizeStart = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (isSessionSidebarCollapsed) return;

    event.preventDefault();
    const handle = event.currentTarget;
    const pointerId = event.pointerId;
    const startWidth = sessionSidebarWidth;
    const startX = event.clientX;

    handle.setPointerCapture(pointerId);

    const handlePointerMove = (moveEvent: PointerEvent) => {
      const nextWidth = clamp(
        startWidth + (moveEvent.clientX - startX),
        MIN_SESSION_SIDEBAR_WIDTH,
        getMaxSessionSidebarWidth(),
      );
      setSessionSidebarWidth(nextWidth);
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

  const handleContextSidebarResizeStart = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (isContextSidebarCollapsed) return;

    event.preventDefault();
    const handle = event.currentTarget;
    const pointerId = event.pointerId;
    const startWidth = contextSidebarWidth;
    const startX = event.clientX;

    handle.setPointerCapture(pointerId);

    const handlePointerMove = (moveEvent: PointerEvent) => {
      const nextWidth = clamp(
        startWidth - (moveEvent.clientX - startX),
        MIN_CONTEXT_SIDEBAR_WIDTH,
        getMaxContextSidebarWidth(),
      );
      setContextSidebarWidth(nextWidth);
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

  const composerLayoutStyle = useMemo(
    () => ({ "--composer-height": `${composerHeight}px` } as CSSProperties),
    [composerHeight],
  );
  const workbenchLayoutStyle = useMemo(
    () =>
      ({
        "--session-sidebar-width": `${sessionSidebarWidth}px`,
        "--context-sidebar-width": `${contextSidebarWidth}px`,
      } as CSSProperties),
    [contextSidebarWidth, sessionSidebarWidth],
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

  const renderExecutionBlock = (key: string, turnEvents: SessionEvent[], options?: { inlineAssistant?: boolean }) => {
    const executionGroups = buildExecutionGroups(turnEvents, locale, copy);

    if (!executionGroups.length) {
      return null;
    }

    return (
      <section
        key={key}
        className={`chat-execution-block${options?.inlineAssistant ? " is-inline-assistant" : ""}`}
        data-testid="conversation-execution-block"
      >
        {executionGroups.map((group) => {
          const groupPresentation = group.timelinePresentation;
          const hasGroupDetails = Boolean(groupPresentation && (groupPresentation.fields.length || groupPresentation.blocks.length));

          return (
            <details
              key={group.key}
              className={`chat-execution-group vscode-chat-tool-call tone-${group.tone}`}
              data-testid="conversation-execution-group"
            >
              <summary
                className="chat-execution-summary vscode-chat-tool-call-header"
                data-testid="conversation-execution-toggle"
              >
                <span className="chat-execution-summary-chevron" aria-hidden="true" />
                {renderExecutionMarker(group.tone, "group")}
                <span className="chat-execution-summary-text">{group.title}</span>
                {group.preview ? <span className="chat-execution-summary-preview">{group.preview}</span> : null}
              </summary>

              <div className="chat-execution-card vscode-chat-tool-call-body">
                <div className="chat-execution-card-header">
                  <div className="chat-execution-card-meta">
                    <span className={`chat-execution-pill tone-${group.tone}`}>{group.statusLabel}</span>
                    <span className="chat-execution-pill tone-neutral">{group.stepCountLabel}</span>
                  </div>
                </div>

                {hasGroupDetails ? (
                  <div className="chat-execution-group-details" data-testid="conversation-execution-group-details">
                    {groupPresentation?.fields.length ? (
                      <dl className="timeline-detail-grid">
                        {groupPresentation.fields.map((field) => (
                          <div key={`${group.key}-${field.label}-${field.value}`} className="timeline-detail-row">
                            <dt>{field.label}</dt>
                            <dd className={`${field.mono ? "is-mono" : ""} tone-${field.tone ?? "neutral"}`}>{field.value}</dd>
                          </div>
                        ))}
                      </dl>
                    ) : null}

                    {groupPresentation?.blocks.map((block) => (
                      <div key={`${group.key}-${block.label}-${block.value.slice(0, 48)}`} className={`timeline-detail-block kind-${block.kind}`}>
                        <div className="timeline-detail-block-label">{block.label}</div>
                        {block.kind === "code" ? (
                          <pre className="timeline-event-data">{block.value}</pre>
                        ) : (
                          <div className={`timeline-detail-block-markdown markdown-content kind-${block.kind}`}>
                            <ReactMarkdown remarkPlugins={[remarkGfm]}>{block.value}</ReactMarkdown>
                          </div>
                        )}
                      </div>
                    ))}
                  </div>
                ) : null}

                <div className="chat-execution-steps">
                  {group.steps.map((step) => {
                    const timelinePresentation = step.timelinePresentation;
                    const hasExecutionDetails = Boolean(timelinePresentation.fields.length || timelinePresentation.blocks.length);

                    return (
                      <details
                        key={step.key}
                        className={`chat-execution-step tone-${step.tone}`}
                        data-testid="conversation-execution-step"
                      >
                        <summary className="chat-execution-step-toggle" data-testid="conversation-execution-step-toggle">
                          <span className="chat-execution-step-chevron" aria-hidden="true" />
                          {renderExecutionMarker(step.tone, "step")}
                          <div className="chat-execution-step-copy">
                            <div className="chat-execution-step-title-row">
                              <span className="chat-execution-step-title">{step.title}</span>
                              <span className={`timeline-stage-badge tone-${timelinePresentation.tone}`}>{timelinePresentation.stage}</span>
                            </div>
                            {step.preview && step.preview !== step.title ? (
                              <p className="chat-execution-step-summary">{step.preview}</p>
                            ) : null}
                          </div>
                        </summary>

                        {hasExecutionDetails ? (
                          <div className="chat-execution-step-body" data-testid="conversation-execution-step-details">
                            {timelinePresentation.fields.length ? (
                              <dl className="timeline-detail-grid">
                                {timelinePresentation.fields.map((field) => (
                                  <div key={`${step.key}-${field.label}-${field.value}`} className="timeline-detail-row">
                                    <dt>{field.label}</dt>
                                    <dd className={`${field.mono ? "is-mono" : ""} tone-${field.tone ?? "neutral"}`}>{field.value}</dd>
                                  </div>
                                ))}
                              </dl>
                            ) : null}

                            {timelinePresentation.blocks.map((block) => (
                              <div key={`${step.key}-${block.label}-${block.value.slice(0, 48)}`} className={`timeline-detail-block kind-${block.kind}`}>
                                <div className="timeline-detail-block-label">{block.label}</div>
                                {block.kind === "code" ? (
                                  <pre className="timeline-event-data">{block.value}</pre>
                                ) : (
                                  <div className={`timeline-detail-block-markdown markdown-content kind-${block.kind}`}>
                                    <ReactMarkdown remarkPlugins={[remarkGfm]}>{block.value}</ReactMarkdown>
                                  </div>
                                )}
                              </div>
                            ))}
                          </div>
                        ) : null}
                      </details>
                    );
                  })}
                </div>
              </div>
            </details>
          );
        })}
      </section>
    );
  };

  const composerSessionSettings = currentSession ? (
    <div className="session-settings-block composer-session-settings" data-testid="session-settings-card">
      <div className="composer-session-settings-grid">
        <label className="settings-field settings-field-model">
          <span className="settings-label">{copy.agent.sessionSettingsFields.model}</span>
          <div className={`settings-control-shell${modelOptionsLoading || !sessionModelOptions.length ? " is-disabled" : ""}`}>
            <select
              className="settings-select"
              data-testid="session-model-select"
              aria-label={copy.agent.sessionSettingsFields.model}
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
              aria-label={copy.agent.sessionSettingsFields.reasoning}
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

        <div className="session-settings-actions composer-session-settings-actions">
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
      {modelOptionsLoading ? <p className="settings-help">{copy.agent.sessionSettingsLoading}</p> : null}
      {sessionSettingsErrorMessage ? <div className="inline-alert composer-session-settings-alert">{sessionSettingsErrorMessage}</div> : null}
    </div>
  ) : null;

  return (
    <div className="agent-workbench" ref={workbenchRef} style={workbenchLayoutStyle}>
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

      {!isSessionSidebarCollapsed ? (
        <div
          className="pane-resizer pane-resizer-session"
          data-testid="session-sidebar-resizer"
          role="separator"
          aria-orientation="vertical"
          aria-label={copy.agent.resizeSessions}
          aria-valuemin={MIN_SESSION_SIDEBAR_WIDTH}
          aria-valuemax={getMaxSessionSidebarWidth()}
          aria-valuenow={sessionSidebarWidth}
          onPointerDown={handleSessionSidebarResizeStart}
        />
      ) : null}

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
          <div className="chat-transcript" ref={transcriptRef}>
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
                    const messageTurnId = getMessageTurnId(message);
                    const inlineExecutionEvents =
                      message.role === "assistant" && messageTurnId ? executionEventsByTurn.get(messageTurnId) ?? [] : [];
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

                            {inlineExecutionEvents.length ? renderExecutionBlock(`execution-${messageTurnId}`, inlineExecutionEvents, { inlineAssistant: true }) : null}

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

                  return renderExecutionBlock(entry.key, entry.events);
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
                  {pendingAttachments.map((attachment, index) => {
                    const attachmentTitle = attachment.display_name || `${copy.agent.attachmentImageAlt} ${index + 1}`;
                    const attachmentMeta = attachment.width && attachment.height ? `${attachment.width} x ${attachment.height}` : null;
                    const attachmentSummary = [attachmentTitle, attachmentMeta].filter(Boolean).join(" · ");

                    return (
                      <div
                        key={attachment.id}
                        className="composer-attachment"
                        data-testid="composer-attachment"
                        title={attachmentSummary}
                      >
                        <div className="composer-attachment-chip">
                          <span className="composer-attachment-icon" aria-hidden="true">
                            <svg viewBox="0 0 16 16" focusable="false">
                              <path d="M2.25 3A1.25 1.25 0 0 1 3.5 1.75h9A1.25 1.25 0 0 1 13.75 3v10A1.25 1.25 0 0 1 12.5 14.25h-9A1.25 1.25 0 0 1 2.25 13V3Zm1.25.25a.25.25 0 0 0-.25.25v7.82l2.7-2.93a.75.75 0 0 1 1.08-.03l1.64 1.64 1.96-2.29a.75.75 0 0 1 1.14.97l-2.48 2.9a.75.75 0 0 1-1.1.05L6.45 9.95l-3.2 3.47v.08c0 .14.11.25.25.25h9a.25.25 0 0 0 .25-.25v-10a.25.25 0 0 0-.25-.25h-9Zm6.9 1.35a1.15 1.15 0 1 1 0 2.3 1.15 1.15 0 0 1 0-2.3Z" fill="currentColor"/>
                            </svg>
                          </span>
                          <div className="composer-attachment-copy">
                            <span className="composer-attachment-title">{attachmentTitle}</span>
                            {attachmentMeta ? <span className="composer-attachment-caption">{attachmentMeta}</span> : null}
                          </div>
                        </div>
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
                      </div>
                    );
                  })}
                </div>
              ) : null}

              <textarea
                ref={composerTextareaRef}
                id="agent-prompt"
                rows={3}
                className="composer-textarea"
                placeholder={currentSession ? copy.agent.textareaActive : copy.agent.textareaInactive}
                value={prompt}
                disabled={!currentSession}
                onChange={(e: ChangeEvent<HTMLTextAreaElement>) => setPrompt(e.target.value)}
                onKeyDown={handlePromptKeyDown}
                onPaste={handleComposerPaste}
              />

              <div ref={composerFooterRef} className="composer-footer">
                <div className="composer-footer-main">
                  {composerSessionSettings}
                  <div className="composer-meta">
                    <span className="composer-hint">{copy.common.ctrlEnterHint}</span>
                    <span className="composer-hint">{copy.agent.composerImageHint}</span>
                  </div>
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

      {!isContextSidebarCollapsed ? (
        <div
          className="pane-resizer pane-resizer-context"
          data-testid="context-sidebar-resizer"
          role="separator"
          aria-orientation="vertical"
          aria-label={copy.agent.resizeDetails}
          aria-valuemin={MIN_CONTEXT_SIDEBAR_WIDTH}
          aria-valuemax={getMaxContextSidebarWidth()}
          aria-valuenow={contextSidebarWidth}
          onPointerDown={handleContextSidebarResizeStart}
        />
      ) : null}

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

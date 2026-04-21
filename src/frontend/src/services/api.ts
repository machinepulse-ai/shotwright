import axios, { InternalAxiosRequestConfig } from "axios";

import {
  ChatImageAttachment,
  ChatMessage,
  ChatMessageDeletedEvent,
  Session,
  SessionContextRefreshEvent,
  SessionEvent,
} from "../types";

const api = axios.create({
  baseURL: "/api",
  timeout: 30000,
});

const CHAT_TURN_TIMEOUT_MS = 0;
const STREAM_RECONNECT_DELAY_MS = 1000;
const STREAM_RECONNECT_DELAY_MAX_MS = 5000;

function getStoredAdminToken() {
  if (typeof window === "undefined") {
    return null;
  }

  try {
    return window.localStorage.getItem("shotwright_token");
  } catch {
    return null;
  }
}

// Attach admin token if present
api.interceptors.request.use((config: InternalAxiosRequestConfig) => {
  const token = getStoredAdminToken();
  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

// --- Sessions ---
export const getSessions = () => api.get("/sessions");
export const createSession = (name: string) => api.post("/sessions", { name });
export const updateSession = (id: string, payload: Record<string, unknown>) => api.patch(`/sessions/${id}`, payload);
export const deleteSession = (id: string) => api.delete(`/sessions/${id}`);
export const getCopilotModelOptions = () => api.get("/sessions/model-options");

// --- Containers ---
export const getContainers = (sessionId?: string) =>
  api.get("/containers", { params: sessionId ? { session_id: sessionId } : {} });
export const createContainer = (sessionId: string) =>
  api.post("/containers", { session_id: sessionId });
export const stopContainer = (id: string) => api.post(`/containers/${id}/stop`);
export const removeContainer = (id: string) => api.delete(`/containers/${id}`);

// --- Projects ---
export const listProjects = (sessionId: string) => api.get(`/projects/${sessionId}`);
export const uploadProject = (sessionId: string, file: File) => {
  const form = new FormData();
  form.append("file", file);
  return api.post(`/agent/sessions/${sessionId}/uploads`, form);
};
export const uploadReferenceVideo = (sessionId: string, file: File) => {
  const form = new FormData();
  form.append("file", file);
  return api.post(`/agent/sessions/${sessionId}/reference-videos`, form);
};
export const exportProject = (sessionId: string, projectId: string) =>
  api.get(`/projects/${sessionId}/${projectId}/archive`, { responseType: "blob" });

// --- Agent ---
export const getAgentContext = (sessionId: string) => api.get(`/agent/sessions/${sessionId}/context`);
export const getAgentMessages = (sessionId: string) => api.get(`/agent/sessions/${sessionId}/messages`);
export const getAgentEvents = (sessionId: string) => api.get(`/agent/sessions/${sessionId}/events`);
export const sendChatTurn = (
  sessionId: string,
  payload: { content: string; attachments?: ChatImageAttachment[] }
) => api.post(`/agent/sessions/${sessionId}/messages`, payload, { timeout: CHAT_TURN_TIMEOUT_MS });
export const cancelChatTurn = (sessionId: string) => api.post(`/agent/sessions/${sessionId}/cancel`);

function parseStreamPayload<T>(event: MessageEvent<string>): T | null {
  try {
    return JSON.parse(event.data) as T;
  } catch {
    return null;
  }
}

function parseStreamData<T>(data: string): T | null {
  try {
    return JSON.parse(data) as T;
  } catch {
    return null;
  }
}

export type AgentSessionStreamHandlers = {
  onOpen?: () => void;
  onError?: (event: Event) => void;
  onSessionUpdated?: (session: Session) => void;
  onMessageUpsert?: (message: ChatMessage) => void;
  onMessageDeleted?: (payload: ChatMessageDeletedEvent) => void;
  onTimelineEvent?: (event: SessionEvent) => void;
  onContextRefresh?: (payload: SessionContextRefreshEvent) => void;
};

export type AgentSessionStreamConnection = {
  close: () => void;
};

function dispatchAgentSessionStreamEvent(
  eventType: string,
  data: string,
  handlers: AgentSessionStreamHandlers,
) {
  switch (eventType) {
    case "session.updated": {
      const payload = parseStreamData<Session>(data);
      if (payload) handlers.onSessionUpdated?.(payload);
      return;
    }
    case "message.upsert": {
      const payload = parseStreamData<ChatMessage>(data);
      if (payload) handlers.onMessageUpsert?.(payload);
      return;
    }
    case "message.deleted": {
      const payload = parseStreamData<ChatMessageDeletedEvent>(data);
      if (payload) handlers.onMessageDeleted?.(payload);
      return;
    }
    case "timeline.event": {
      const payload = parseStreamData<SessionEvent>(data);
      if (payload) handlers.onTimelineEvent?.(payload);
      return;
    }
    case "context.refresh": {
      const payload = parseStreamData<SessionContextRefreshEvent>(data);
      if (payload) handlers.onContextRefresh?.(payload);
      return;
    }
    default:
      return;
  }
}

function consumeSseBuffer(buffer: string) {
  const events: Array<{ type: string; data: string }> = [];
  const blocks = buffer.split(/\r?\n\r?\n/);
  const remainder = blocks.pop() ?? "";

  for (const block of blocks) {
    const trimmedBlock = block.trim();
    if (!trimmedBlock) {
      continue;
    }

    let eventType = "message";
    const dataLines: string[] = [];

    for (const line of block.split(/\r?\n/)) {
      if (!line || line.startsWith(":")) {
        continue;
      }

      if (line.startsWith("event:")) {
        eventType = line.slice("event:".length).trim();
        continue;
      }

      if (line.startsWith("data:")) {
        dataLines.push(line.slice("data:".length).trimStart());
      }
    }

    if (!dataLines.length) {
      continue;
    }

    events.push({
      type: eventType,
      data: dataLines.join("\n"),
    });
  }

  return { events, remainder };
}

function openFetchAgentSessionStream(
  streamUrl: string,
  token: string | null,
  handlers: AgentSessionStreamHandlers,
): AgentSessionStreamConnection {
  let disposed = false;
  let reconnectDelayMs = STREAM_RECONNECT_DELAY_MS;
  let reconnectTimer: number | null = null;
  let activeController: AbortController | null = null;

  const scheduleReconnect = () => {
    if (disposed || reconnectTimer !== null || typeof window === "undefined") {
      return;
    }

    const delayMs = reconnectDelayMs;
    reconnectDelayMs = Math.min(reconnectDelayMs * 2, STREAM_RECONNECT_DELAY_MAX_MS);
    reconnectTimer = window.setTimeout(() => {
      reconnectTimer = null;
      void connect();
    }, delayMs);
  };

  const connect = async () => {
    const controller = new AbortController();
    activeController = controller;

    try {
      const headers: Record<string, string> = {
        Accept: "text/event-stream",
      };
      if (token) {
        headers.Authorization = `Bearer ${token}`;
      }

      const response = await fetch(streamUrl, {
        method: "GET",
        headers,
        cache: "no-store",
        signal: controller.signal,
      });

      if (!response.ok || !response.body) {
        throw new Error(`SSE request failed with status ${response.status}`);
      }

      reconnectDelayMs = STREAM_RECONNECT_DELAY_MS;
      handlers.onOpen?.();

      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";

      while (!disposed) {
        const { value, done } = await reader.read();
        if (done) {
          break;
        }

        buffer += decoder.decode(value, { stream: true });
        const parsed = consumeSseBuffer(buffer);
        buffer = parsed.remainder;

        for (const event of parsed.events) {
          dispatchAgentSessionStreamEvent(event.type, event.data, handlers);
        }
      }

      if (!disposed) {
        throw new Error("SSE stream closed unexpectedly");
      }
    } catch {
      if (disposed || controller.signal.aborted) {
        return;
      }

      handlers.onError?.(new Event("error"));
      scheduleReconnect();
    }
  };

  void connect();

  return {
    close: () => {
      disposed = true;
      if (reconnectTimer !== null && typeof window !== "undefined") {
        window.clearTimeout(reconnectTimer);
      }
      reconnectTimer = null;
      activeController?.abort();
      activeController = null;
    },
  };
}

export function openAgentSessionStream(
  sessionId: string,
  handlers: AgentSessionStreamHandlers,
): AgentSessionStreamConnection {
  const streamUrl = new URL(`/api/agent/sessions/${sessionId}/stream`, window.location.origin);
  const token = getStoredAdminToken();

  if (typeof window.fetch === "function") {
    return openFetchAgentSessionStream(streamUrl.toString(), token, handlers);
  }

  const stream = new EventSource(streamUrl.toString());

  if (handlers.onOpen) {
    stream.addEventListener("open", () => handlers.onOpen?.());
  }
  if (handlers.onError) {
    stream.addEventListener("error", handlers.onError);
  }
  if (handlers.onSessionUpdated) {
    stream.addEventListener("session.updated", (event) => {
      const payload = parseStreamPayload<Session>(event as MessageEvent<string>);
      if (payload) handlers.onSessionUpdated?.(payload);
    });
  }
  if (handlers.onMessageUpsert) {
    stream.addEventListener("message.upsert", (event) => {
      const payload = parseStreamPayload<ChatMessage>(event as MessageEvent<string>);
      if (payload) handlers.onMessageUpsert?.(payload);
    });
  }
  if (handlers.onMessageDeleted) {
    stream.addEventListener("message.deleted", (event) => {
      const payload = parseStreamPayload<ChatMessageDeletedEvent>(event as MessageEvent<string>);
      if (payload) handlers.onMessageDeleted?.(payload);
    });
  }
  if (handlers.onTimelineEvent) {
    stream.addEventListener("timeline.event", (event) => {
      const payload = parseStreamPayload<SessionEvent>(event as MessageEvent<string>);
      if (payload) handlers.onTimelineEvent?.(payload);
    });
  }
  if (handlers.onContextRefresh) {
    stream.addEventListener("context.refresh", (event) => {
      const payload = parseStreamPayload<SessionContextRefreshEvent>(event as MessageEvent<string>);
      if (payload) handlers.onContextRefresh?.(payload);
    });
  }

  return stream;
}

// --- Admin ---
export const adminLogin = (password: string) => api.post("/admin/login", { password });
export const getAdminSettings = () => api.get("/admin/settings");
export const updateGithubToken = (token: string) =>
  api.put("/admin/github-token", { github_token: token });
export const updateAdminSettings = (settings: Record<string, unknown>) =>
  api.put("/admin/settings", settings);
export const getAdminDashboard = () => api.get("/admin/dashboard");

export default api;

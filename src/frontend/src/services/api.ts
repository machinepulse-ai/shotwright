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

// Attach admin token if present
api.interceptors.request.use((config: InternalAxiosRequestConfig) => {
  const token = localStorage.getItem("shotwright_token");
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
export const exportProject = (sessionId: string, projectId: string) =>
  api.get(`/projects/${sessionId}/${projectId}/archive`, { responseType: "blob" });

// --- Agent ---
export const getAgentContext = (sessionId: string) => api.get(`/agent/sessions/${sessionId}/context`);
export const getAgentMessages = (sessionId: string) => api.get(`/agent/sessions/${sessionId}/messages`);
export const getAgentEvents = (sessionId: string) => api.get(`/agent/sessions/${sessionId}/events`);
export const sendChatTurn = (
  sessionId: string,
  payload: { content: string; attachments?: ChatImageAttachment[] }
) => api.post(`/agent/sessions/${sessionId}/messages`, payload);

function parseStreamPayload<T>(event: MessageEvent<string>): T | null {
  try {
    return JSON.parse(event.data) as T;
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

export function openAgentSessionStream(sessionId: string, handlers: AgentSessionStreamHandlers): EventSource {
  const streamUrl = new URL(`/api/agent/sessions/${sessionId}/stream`, window.location.origin);
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

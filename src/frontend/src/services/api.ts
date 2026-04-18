import axios, { InternalAxiosRequestConfig } from "axios";

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
export const deleteSession = (id: string) => api.delete(`/sessions/${id}`);

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
export const sendChatTurn = (sessionId: string, content: string) =>
  api.post(`/agent/sessions/${sessionId}/messages`, { content });
export const getPublicRuntimeSettings = () => api.get("/agent/runtime-settings");

// --- Admin ---
export const adminLogin = (password: string) => api.post("/admin/login", { password });
export const getAdminSettings = () => api.get("/admin/settings");
export const updateGithubToken = (token: string) =>
  api.put("/admin/github-token", { github_token: token });
export const updateAdminSettings = (settings: Record<string, unknown>) =>
  api.put("/admin/settings", settings);
export const getAdminDashboard = () => api.get("/admin/dashboard");

export default api;

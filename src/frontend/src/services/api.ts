import axios from "axios";

const api = axios.create({
  baseURL: "/api",
  timeout: 30000,
});

// Attach admin token if present
api.interceptors.request.use((config) => {
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
export const uploadProject = (sessionId: string, containerId: string, file: File) => {
  const form = new FormData();
  form.append("file", file);
  return api.post(`/projects/${sessionId}/upload?container_id=${containerId}`, form);
};
export const exportProject = (sessionId: string, containerId: string, projectId: string) =>
  api.post(
    `/projects/${sessionId}/export?container_id=${containerId}&project_id=${projectId}`,
    {},
    { responseType: "blob" }
  );
export const renderProject = (
  sessionId: string,
  containerId: string,
  aepPath: string,
  composition?: string
) =>
  api.post(`/projects/${sessionId}/render`, null, {
    params: { container_id: containerId, aep_path: aepPath, composition: composition || "Main" },
  });
export const createStream = (sessionId: string, mp4Path: string) =>
  api.post(`/projects/${sessionId}/stream`, null, { params: { mp4_path: mp4Path } });

// --- Agent ---
export const sendAgentCommand = (sessionId: string, action: string, payload: Record<string, unknown> = {}) =>
  api.post("/agent/command", { session_id: sessionId, action, payload });
export const runJsx = (sessionId: string, scriptContent: string, description?: string) =>
  api.post("/agent/jsx", { session_id: sessionId, script_content: scriptContent, description });

// --- Admin ---
export const adminLogin = (password: string) => api.post("/admin/login", { password });
export const getAdminSettings = () => api.get("/admin/settings");
export const updateGithubToken = (token: string) =>
  api.put("/admin/github-token", { github_token: token });
export const getAdminDashboard = () => api.get("/admin/dashboard");

export default api;

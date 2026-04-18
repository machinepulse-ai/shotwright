export interface Session {
  _id: string;
  name: string;
  status: "idle" | "running" | "awaiting_input" | "error" | "closed";
  copilot_model: string;
  copilot_reasoning_effort: ReasoningEffort | null;
  copilot_session_id: string | null;
  container_id: string | null;
  active_project_id: string | null;
  latest_render_path: string | null;
  latest_stream_url: string | null;
  last_error: string | null;
  created_at: string;
  updated_at: string;
}

export type ReasoningEffort = "low" | "medium" | "high" | "xhigh";

export interface CopilotModelOption {
  id: string;
  name: string;
  supports_reasoning_effort: boolean;
  supported_reasoning_efforts: ReasoningEffort[];
  default_reasoning_effort: ReasoningEffort | null;
}

export interface Container {
  _id: string;
  docker_id: string;
  session_id: string;
  image: string;
  status: "creating" | "running" | "stopped" | "error" | "removed";
  created_at: string;
  ports: Record<string, number>;
}

export interface AgentResponse {
  success: boolean;
  action: string;
  message: string;
  data: Record<string, unknown>;
}

export interface ChatMessage {
  _id: string;
  session_id: string;
  role: "user" | "assistant";
  content: string;
  created_at: string;
  metadata: Record<string, unknown>;
}

export interface SessionEvent {
  _id: string;
  session_id: string;
  type: string;
  summary: string;
  created_at: string;
  data: Record<string, unknown>;
}

export interface ProjectInfo {
  _id: string;
  session_id: string;
  filename: string;
  workspace_dir: string;
  aep_files: string[];
  created_at: string;
  status: "uploaded" | "active" | "exported";
}

export interface AgentContext {
  session: Session;
  container: Container | null;
  projects: ProjectInfo[];
  latest_render_path: string | null;
  latest_render_url: string | null;
  latest_stream_url: string | null;
}

export interface ChatTurnResult {
  assistant_message: ChatMessage;
  session_status: Session["status"];
}

export interface ChatMessageDeletedEvent {
  session_id: string;
  message_id: string;
}

export interface SessionContextRefreshEvent {
  reason: string;
  [key: string]: unknown;
}

export interface DashboardData {
  total_sessions: number;
  active_sessions: number;
  total_containers: number;
  running_containers: number;
}

export interface AdminSettings {
  github_token_set: boolean;
  default_copilot_model: string;
  default_copilot_reasoning_effort: ReasoningEffort | null;
  copilot_cli_path: string;
  copilot_workspace_root: string;
  copilot_use_logged_in_user: boolean;
  copilot_http_proxy: string;
  copilot_https_proxy: string;
  copilot_no_proxy: string;
}

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

export interface ChatImageAttachment {
  type: "image";
  mime_type: string;
  data_url: string;
  display_name?: string | null;
  width?: number | null;
  height?: number | null;
  size_bytes?: number | null;
}

export interface ReferenceVideoInfo {
  id: string;
  session_id: string;
  filename: string;
  file_path: string;
  shared_relative_path: string;
  mime_type?: string | null;
  size_bytes: number;
  duration_seconds: number;
  width?: number | null;
  height?: number | null;
  created_at: string;
}

export interface StoryboardInfo {
  id: string;
  session_id: string;
  filename: string;
  file_path: string;
  shared_relative_path: string;
  mime_type?: string | null;
  created_at: string;
  source_video_path: string;
  source_video_relative_path: string;
  source_video_filename: string;
  source_video_duration_seconds: number;
  clip_start_seconds: number;
  clip_end_seconds: number;
  clip_duration_seconds: number;
  interval_seconds: number;
  columns: number;
  rows: number;
  tile_width: number;
  estimated_frames: number;
  ffmpeg_filter: string;
}

export interface SessionEvent {
  _id: string;
  session_id: string;
  type: string;
  summary: string;
  created_at: string;
  turn_id?: string | null;
  sequence?: number | null;
  data: Record<string, unknown>;
}

export interface ProjectInfo {
  _id: string;
  session_id: string;
  filename: string;
  workspace_dir: string;
  aep_files: string[];
  entry_aep_file?: string | null;
  origin?: "uploaded" | "generated";
  created_at: string;
  status: "uploaded" | "active" | "exported";
}

export interface AgentContext {
  session: Session;
  container: Container | null;
  projects: ProjectInfo[];
  reference_videos: ReferenceVideoInfo[];
  storyboards: StoryboardInfo[];
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
  copilot_turn_timeout_seconds: number;
  copilot_cli_path: string;
  copilot_workspace_root: string;
  copilot_use_logged_in_user: boolean;
  copilot_http_proxy: string;
  copilot_https_proxy: string;
  copilot_no_proxy: string;
}

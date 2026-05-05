export interface Session {
  _id: string;
  name: string;
  status: "idle" | "running" | "awaiting_input" | "error" | "closed";
  agent_provider: AgentProvider;
  copilot_model: string;
  copilot_reasoning_effort: ReasoningEffort | null;
  copilot_session_id: string | null;
  agent_thread_id: string | null;
  codex_thread_id: string | null;
  container_id: string | null;
  active_project_id: string | null;
  latest_render_path: string | null;
  latest_stream_url: string | null;
  last_error: string | null;
  created_at: string;
  updated_at: string;
}

export type ReasoningEffort = "low" | "medium" | "high" | "xhigh";
export type AgentProvider = "copilot" | "codex";

export interface CopilotModelOption {
  id: string;
  name: string;
  provider?: string | null;
  model_provider?: string | null;
  brand?: string | null;
  family?: string | null;
  submodel?: string | null;
  display_name?: string | null;
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
  data_url?: string | null;
  display_name?: string | null;
  file_path?: string | null;
  shared_relative_path?: string | null;
  workspace_relative_path?: string | null;
  width?: number | null;
  height?: number | null;
  size_bytes?: number | null;
}

export interface SessionImageAttachmentInfo {
  file_path: string;
  display_name: string;
  mime_type?: string | null;
  shared_relative_path?: string | null;
  workspace_relative_path?: string | null;
  width?: number | null;
  height?: number | null;
  size_bytes?: number | null;
  created_at?: string | null;
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
  thumbnail_path?: string | null;
  thumbnail_shared_relative_path?: string | null;
  thumbnail_mime_type?: string | null;
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

export interface RenderOutputInfo {
  id: string;
  session_id: string;
  project_id: string;
  filename: string;
  file_path: string;
  shared_relative_path: string;
  mime_type?: string | null;
  size_bytes: number;
  created_at: string;
  composition: string;
  aep_path: string;
  aep_file?: string | null;
  project_workspace_dir?: string | null;
  work_dir?: string | null;
  stdout_path?: string | null;
  stderr_path?: string | null;
  stream_id?: string | null;
  playlist_url?: string | null;
  thumbnail_path?: string | null;
}

export interface ProjectCompositionInfo {
  name: string;
  width?: number | null;
  height?: number | null;
  duration_seconds?: number | null;
  frame_rate?: number | null;
  layer_count?: number | null;
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
  compositions: ProjectCompositionInfo[];
  composition_catalog_updated_at?: string | null;
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
  recent_image_attachments: SessionImageAttachmentInfo[];
  render_outputs: RenderOutputInfo[];
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
  agent_provider: AgentProvider;
  github_token_set: boolean;
  openai_api_key_set: boolean;
  default_copilot_model: string;
  default_copilot_reasoning_effort: ReasoningEffort | null;
  copilot_turn_timeout_seconds: number;
  copilot_cli_path: string;
  copilot_workspace_root: string;
  copilot_use_logged_in_user: boolean;
  copilot_http_proxy: string;
  copilot_https_proxy: string;
  copilot_no_proxy: string;
  codex_node_path: string;
  codex_bridge_script: string;
  codex_path_override: string;
  codex_base_url: string;
  codex_model: string;
  codex_reasoning_effort: ReasoningEffort | null;
  codex_turn_timeout_seconds: number;
  codex_workspace_root: string;
  codex_approval_policy: string;
  codex_sandbox_mode: string;
  codex_network_access_enabled: boolean;
  codex_skip_git_repo_check: boolean;
  codex_web_search_mode: string;
  codex_http_proxy: string;
  codex_https_proxy: string;
  codex_no_proxy: string;
}

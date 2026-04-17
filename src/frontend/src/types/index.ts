export interface Session {
  _id: string;
  name: string;
  status: "active" | "idle" | "rendering" | "closed";
  container_id: string | null;
  created_at: string;
  updated_at: string;
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

export interface DashboardData {
  total_sessions: number;
  active_sessions: number;
  total_containers: number;
  running_containers: number;
}

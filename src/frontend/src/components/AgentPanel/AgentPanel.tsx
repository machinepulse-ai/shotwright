import { ChangeEvent, useEffect, useMemo, useState } from "react";
import {
  createSession,
  deleteSession,
  exportProject,
  getAgentContext,
  getAgentEvents,
  getAgentMessages,
  getSessions,
  sendChatTurn,
  stopContainer,
  uploadProject,
} from "../../services/api";
import { AgentContext, ChatMessage, ProjectInfo, Session, SessionEvent } from "../../types";
import VideoPlayer from "../VideoPlayer/VideoPlayer";
import ContainerManager from "../ContainerManager/ContainerManager";
import "./AgentPanel.css";

export default function AgentPanel() {
  const [sessions, setSessions] = useState<Session[]>([]);
  const [currentSession, setCurrentSession] = useState<Session | null>(null);
  const [context, setContext] = useState<AgentContext | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [events, setEvents] = useState<SessionEvent[]>([]);
  const [prompt, setPrompt] = useState("");
  const [sending, setSending] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const sortedEvents = useMemo(
    () => [...events].sort((left, right) => new Date(left.created_at).getTime() - new Date(right.created_at).getTime()),
    [events]
  );

  const fetchSessions = async () => {
    const res = await getSessions();
    setSessions(res.data);
    if (!currentSession && res.data.length > 0) {
      setCurrentSession(res.data[0]);
    }
  };

  const loadCurrentSession = async (sessionId: string) => {
    const [contextRes, messageRes, eventRes] = await Promise.all([
      getAgentContext(sessionId),
      getAgentMessages(sessionId),
      getAgentEvents(sessionId),
    ]);
    setContext(contextRes.data);
    setMessages(messageRes.data);
    setEvents(eventRes.data);
    setError(null);
  };

  useEffect(() => {
    fetchSessions();
  }, []);

  useEffect(() => {
    if (!currentSession) {
      setContext(null);
      setMessages([]);
      setEvents([]);
      return;
    }

    loadCurrentSession(currentSession._id).catch((err) => {
      setError(err?.response?.data?.detail || "Failed to load session data.");
    });

    const timer = window.setInterval(() => {
      loadCurrentSession(currentSession._id).catch(() => {});
      fetchSessions().catch(() => {});
    }, 2500);

    return () => window.clearInterval(timer);
  }, [currentSession?._id]);

  const handleNewSession = async () => {
    const name = `Session ${sessions.length + 1}`;
    const res = await createSession(name);
    setSessions((prev: Session[]) => [res.data, ...prev]);
    setCurrentSession(res.data);
  };

  const handleSend = async () => {
    if (!currentSession || !prompt.trim()) return;
    const content = prompt.trim();
    setSending(true);
    setPrompt("");
    try {
      await sendChatTurn(currentSession._id, content);
      await loadCurrentSession(currentSession._id);
      await fetchSessions();
    } catch (err: any) {
      setError(err?.response?.data?.detail || "Failed to send prompt to Copilot agent.");
    }
    setSending(false);
  };

  const handleUpload = async (file: File) => {
    if (!currentSession) return;
    setUploading(true);
    try {
      await uploadProject(currentSession._id, file);
      await loadCurrentSession(currentSession._id);
      await fetchSessions();
    } catch (err: any) {
      setError(err?.response?.data?.detail || "Upload failed.");
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
    } catch (err: any) {
      setError(err?.response?.data?.detail || "Export failed.");
    }
  };

  const handleStopContainer = async (containerId: string) => {
    try {
      await stopContainer(containerId);
      if (currentSession) {
        await loadCurrentSession(currentSession._id);
        await fetchSessions();
      }
    } catch (err: any) {
      setError(err?.response?.data?.detail || "Failed to stop container.");
    }
  };

  const handleDeleteSession = async (sessionId: string) => {
    await deleteSession(sessionId);
    const remaining = sessions.filter((session: Session) => session._id !== sessionId);
    setSessions(remaining);
    setCurrentSession(remaining[0] ?? null);
  };

  return (
    <div className="agent-panel">
      <div className="agent-sidebar">
        <div className="sidebar-header">
          <h2>Sessions</h2>
          <button className="btn-primary" onClick={handleNewSession}>
            + New
          </button>
        </div>
        <ul className="session-list">
          {sessions.map((s) => (
            <li
              key={s._id}
              className={`session-item ${currentSession?._id === s._id ? "active" : ""}`}
              onClick={() => setCurrentSession(s)}
            >
              <div className="session-meta">
                <span className="session-name">{s.name}</span>
                {s.active_project_id && <span className="session-project">project linked</span>}
              </div>
              <span className={`status-badge status-${s.status}`}>{s.status}</span>
            </li>
          ))}
        </ul>
      </div>

      <div className="agent-content">
        {currentSession ? (
          <>
            <div className="content-header">
              <div>
                <h2>{currentSession.name}</h2>
                <p className="session-subtitle">直接和 Copilot agent 对话，资源操作通过后端 custom tools 执行。</p>
              </div>
              <div className="header-actions">
                <label className="btn-primary upload-btn">
                  {uploading ? "Uploading..." : "Upload AEP Zip"}
                  <input
                    type="file"
                    accept=".zip"
                    hidden
                    onChange={(e: ChangeEvent<HTMLInputElement>) => e.target.files?.[0] && handleUpload(e.target.files[0])}
                  />
                </label>
                <button className="btn-danger" onClick={() => handleDeleteSession(currentSession._id)}>
                  Delete Session
                </button>
              </div>
            </div>

            {error && <div className="card error-banner">{error}</div>}

            <div className="agent-grid">
              <section className="chat-column card">
                <div className="chat-header">
                  <h3>Copilot Agent</h3>
                  <span className={`status-badge status-${context?.session.status || currentSession.status}`}>
                    {context?.session.status || currentSession.status}
                  </span>
                </div>

                <div className="chat-messages">
                  {messages.length === 0 ? (
                    <div className="empty-chat">
                      <p>先上传 AEP 压缩包，然后直接告诉 agent 你的目标。</p>
                      <p>例如：请启动容器，检查我上传的工程，并渲染一版 H.264 预览。</p>
                    </div>
                  ) : (
                    messages.map((message) => (
                      <article key={message._id} className={`chat-bubble chat-${message.role}`}>
                        <header>{message.role === "user" ? "You" : "Copilot Agent"}</header>
                        <p>{message.content || "(empty response)"}</p>
                      </article>
                    ))
                  )}
                </div>

                <div className="chat-composer">
                  <textarea
                    rows={4}
                    placeholder="告诉 agent 你要对 After Effects 工程做什么，比如：把标题改成白色描边并渲染 1080p 预览。"
                    value={prompt}
                    onChange={(e: ChangeEvent<HTMLTextAreaElement>) => setPrompt(e.target.value)}
                  />
                  <div className="composer-actions">
                    <span className="composer-hint">Agent 会自行决定何时启动容器、选择工程、执行 JSX 和渲染。</span>
                    <button className="btn-primary" onClick={handleSend} disabled={sending || !prompt.trim()}>
                      {sending ? "Sending..." : "Send"}
                    </button>
                  </div>
                </div>
              </section>

              <aside className="workspace-column">
                <div className="card workspace-card">
                  <h3>Workspace</h3>
                  <div className="workspace-summary">
                    <div>
                      <span className="label">Active Project</span>
                      <strong>{context?.session.active_project_id || "None"}</strong>
                    </div>
                    <div>
                      <span className="label">Latest Render</span>
                      <strong>{context?.latest_render_path || "None"}</strong>
                    </div>
                    <div>
                      <span className="label">Copilot Session</span>
                      <strong>{context?.session.copilot_session_id || "Not started"}</strong>
                    </div>
                  </div>
                </div>

                <ContainerManager
                  containers={context?.container ? [context.container] : []}
                  onStop={handleStopContainer}
                />

                <div className="card project-card">
                  <h3>Uploaded Projects</h3>
                  {context?.projects.length ? (
                    <div className="project-list">
                      {context.projects.map((project) => (
                        <div key={project._id} className="project-item">
                          <div>
                            <div className="project-name">{project.filename}</div>
                            <div className="project-meta">
                              {project.aep_files.length ? project.aep_files.join(", ") : "No .aep detected"}
                            </div>
                          </div>
                          <div className="project-actions">
                            <span className={`status-badge status-${project.status === "active" ? "running" : "idle"}`}>
                              {project.status}
                            </span>
                            <button className="btn-primary btn-sm" onClick={() => handleDownload(project)}>
                              Export Zip
                            </button>
                          </div>
                        </div>
                      ))}
                    </div>
                  ) : (
                    <p className="empty-side">还没有上传工程文件。</p>
                  )}
                </div>

                {context?.latest_stream_url && <VideoPlayer src={context.latest_stream_url} />}

                <div className="card timeline-card">
                  <h3>Agent Timeline</h3>
                  {sortedEvents.length ? (
                    <div className="timeline-list">
                      {sortedEvents.map((event) => (
                        <div key={event._id} className="timeline-item">
                          <div className="timeline-type">{event.type}</div>
                          <div className="timeline-summary">{event.summary}</div>
                          <div className="timeline-time">{new Date(event.created_at).toLocaleTimeString()}</div>
                        </div>
                      ))}
                    </div>
                  ) : (
                    <p className="empty-side">Agent 还没有执行任何工具。</p>
                  )}
                </div>
              </aside>
            </div>
          </>
        ) : (
          <div className="empty-state">
            <p>Create a session to start collaborating with Copilot agent.</p>
          </div>
        )}
      </div>
    </div>
  );
}

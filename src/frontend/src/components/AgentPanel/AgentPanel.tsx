import { useState, useEffect, useCallback } from "react";
import {
  getSessions,
  createSession,
  createContainer,
  stopContainer,
  sendAgentCommand,
  runJsx,
  uploadProject,
  renderProject,
  createStream,
} from "../../services/api";
import { Session, Container, AgentResponse } from "../../types";
import VideoPlayer from "../VideoPlayer/VideoPlayer";
import ContainerManager from "../ContainerManager/ContainerManager";
import "./AgentPanel.css";

export default function AgentPanel() {
  const [sessions, setSessions] = useState<Session[]>([]);
  const [currentSession, setCurrentSession] = useState<Session | null>(null);
  const [containers, setContainers] = useState<Container[]>([]);
  const [log, setLog] = useState<AgentResponse[]>([]);
  const [jsxInput, setJsxInput] = useState("");
  const [streamUrl, setStreamUrl] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const fetchSessions = useCallback(async () => {
    const res = await getSessions();
    setSessions(res.data);
  }, []);

  useEffect(() => {
    fetchSessions();
  }, [fetchSessions]);

  const handleNewSession = async () => {
    const name = `Session ${sessions.length + 1}`;
    const res = await createSession(name);
    setSessions((prev) => [res.data, ...prev]);
    setCurrentSession(res.data);
  };

  const handleStartContainer = async () => {
    if (!currentSession) return;
    setLoading(true);
    try {
      const res = await createContainer(currentSession._id);
      setContainers((prev) => [...prev, res.data]);
      addLog({ success: true, action: "start_container", message: "Container started", data: res.data });
    } catch {
      addLog({ success: false, action: "start_container", message: "Failed to start container", data: {} });
    }
    setLoading(false);
  };

  const handleUpload = async (file: File) => {
    if (!currentSession || containers.length === 0) return;
    setLoading(true);
    try {
      const res = await uploadProject(currentSession._id, containers[0]._id, file);
      addLog({ success: true, action: "upload_project", message: "Project uploaded", data: res.data });
    } catch {
      addLog({ success: false, action: "upload_project", message: "Upload failed", data: {} });
    }
    setLoading(false);
  };

  const handleRender = async (aepPath: string) => {
    if (!currentSession || containers.length === 0) return;
    setLoading(true);
    try {
      const res = await renderProject(currentSession._id, containers[0]._id, aepPath);
      addLog({ success: true, action: "render_video", message: "Render complete", data: res.data });
    } catch {
      addLog({ success: false, action: "render_video", message: "Render failed", data: {} });
    }
    setLoading(false);
  };

  const handleStream = async (mp4Path: string) => {
    if (!currentSession) return;
    try {
      const res = await createStream(currentSession._id, mp4Path);
      setStreamUrl(res.data.playlist_url);
    } catch {
      addLog({ success: false, action: "stream", message: "Stream creation failed", data: {} });
    }
  };

  const handleRunJsx = async () => {
    if (!currentSession || !jsxInput.trim()) return;
    setLoading(true);
    try {
      const res = await runJsx(currentSession._id, jsxInput);
      addLog(res.data);
    } catch {
      addLog({ success: false, action: "run_jsx", message: "JSX execution failed", data: {} });
    }
    setLoading(false);
    setJsxInput("");
  };

  const addLog = (entry: AgentResponse) => {
    setLog((prev) => [entry, ...prev].slice(0, 50));
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
              <span className="session-name">{s.name}</span>
              <span className={`status-badge status-${s.status}`}>{s.status}</span>
            </li>
          ))}
        </ul>
      </div>

      <div className="agent-content">
        {currentSession ? (
          <>
            <div className="content-header">
              <h2>{currentSession.name}</h2>
              <div className="header-actions">
                <button className="btn-primary" onClick={handleStartContainer} disabled={loading}>
                  Start Container
                </button>
                <label className="btn-primary upload-btn">
                  Upload AEP
                  <input
                    type="file"
                    accept=".zip"
                    hidden
                    onChange={(e) => e.target.files?.[0] && handleUpload(e.target.files[0])}
                  />
                </label>
              </div>
            </div>

            <ContainerManager
              containers={containers}
              onStop={(id) => stopContainer(id).then(() => fetchSessions())}
            />

            <div className="card jsx-editor">
              <h3>JSX Script</h3>
              <textarea
                rows={6}
                placeholder="Enter ExtendScript / JSX code..."
                value={jsxInput}
                onChange={(e) => setJsxInput(e.target.value)}
              />
              <button className="btn-primary" onClick={handleRunJsx} disabled={loading || !jsxInput.trim()}>
                Execute
              </button>
            </div>

            {streamUrl && <VideoPlayer src={streamUrl} />}

            <div className="action-log">
              <h3>Action Log</h3>
              {log.map((entry, i) => (
                <div key={i} className={`log-entry ${entry.success ? "success" : "error"}`}>
                  <span className="log-action">[{entry.action}]</span>
                  <span className="log-message">{entry.message}</span>
                </div>
              ))}
            </div>
          </>
        ) : (
          <div className="empty-state">
            <p>Select a session or create a new one to get started.</p>
          </div>
        )}
      </div>
    </div>
  );
}

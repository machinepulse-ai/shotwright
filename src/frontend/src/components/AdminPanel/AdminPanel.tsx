import { useState, useEffect } from "react";
import {
  adminLogin,
  getAdminDashboard,
  getAdminSettings,
  updateGithubToken,
  getContainers,
  removeContainer,
  getSessions,
  deleteSession,
} from "../../services/api";
import { Container, DashboardData, Session } from "../../types";
import "./AdminPanel.css";

export default function AdminPanel() {
  const [authenticated, setAuthenticated] = useState(!!localStorage.getItem("shotwright_token"));
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [dashboard, setDashboard] = useState<DashboardData | null>(null);
  const [githubToken, setGithubToken] = useState("");
  const [tokenSet, setTokenSet] = useState(false);
  const [sessions, setSessions] = useState<Session[]>([]);
  const [containers, setContainers] = useState<Container[]>([]);

  const login = async () => {
    try {
      const res = await adminLogin(password);
      localStorage.setItem("shotwright_token", res.data.access_token);
      setAuthenticated(true);
      setError("");
    } catch {
      setError("Invalid password");
    }
  };

  const fetchData = async () => {
    try {
      const [dashRes, settingsRes, sessionsRes, containersRes] = await Promise.all([
        getAdminDashboard(),
        getAdminSettings(),
        getSessions(),
        getContainers(),
      ]);
      setDashboard(dashRes.data);
      setTokenSet(settingsRes.data.github_token_set);
      setSessions(sessionsRes.data);
      setContainers(containersRes.data);
    } catch {
      setAuthenticated(false);
      localStorage.removeItem("shotwright_token");
    }
  };

  useEffect(() => {
    if (authenticated) fetchData();
  }, [authenticated]);

  const handleTokenUpdate = async () => {
    if (!githubToken.trim()) return;
    await updateGithubToken(githubToken);
    setGithubToken("");
    setTokenSet(true);
  };

  const handleDeleteSession = async (id: string) => {
    await deleteSession(id);
    fetchData();
  };

  const handleRemoveContainer = async (id: string) => {
    await removeContainer(id);
    fetchData();
  };

  const logout = () => {
    localStorage.removeItem("shotwright_token");
    setAuthenticated(false);
  };

  if (!authenticated) {
    return (
      <div className="admin-login">
        <div className="card login-card">
          <h2>Admin Login</h2>
          <input
            type="password"
            placeholder="Enter admin password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && login()}
          />
          {error && <p className="login-error">{error}</p>}
          <button className="btn-primary" onClick={login}>
            Login
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="admin-panel">
      <div className="admin-header">
        <h2>Admin Dashboard</h2>
        <button className="btn-danger" onClick={logout}>
          Logout
        </button>
      </div>

      {dashboard && (
        <div className="stats-grid">
          <div className="card stat-card">
            <div className="stat-value">{dashboard.total_sessions}</div>
            <div className="stat-label">Total Sessions</div>
          </div>
          <div className="card stat-card">
            <div className="stat-value">{dashboard.active_sessions}</div>
            <div className="stat-label">Active Sessions</div>
          </div>
          <div className="card stat-card">
            <div className="stat-value">{dashboard.total_containers}</div>
            <div className="stat-label">Total Containers</div>
          </div>
          <div className="card stat-card">
            <div className="stat-value">{dashboard.running_containers}</div>
            <div className="stat-label">Running Containers</div>
          </div>
        </div>
      )}

      <div className="card">
        <h3>GitHub Token</h3>
        <p className="token-status">
          Status: {tokenSet ? <span className="status-badge status-active">Set</span> : <span className="status-badge status-idle">Not set</span>}
        </p>
        <div className="token-input">
          <input
            type="password"
            placeholder="ghp_..."
            value={githubToken}
            onChange={(e) => setGithubToken(e.target.value)}
          />
          <button className="btn-primary" onClick={handleTokenUpdate}>
            Update
          </button>
        </div>
      </div>

      <div className="card">
        <h3>Sessions ({sessions.length})</h3>
        <table className="admin-table">
          <thead>
            <tr>
              <th>Name</th>
              <th>Status</th>
              <th>Created</th>
              <th>Actions</th>
            </tr>
          </thead>
          <tbody>
            {sessions.map((s) => (
              <tr key={s._id}>
                <td>{s.name}</td>
                <td><span className={`status-badge status-${s.status}`}>{s.status}</span></td>
                <td>{new Date(s.created_at).toLocaleString()}</td>
                <td>
                  <button className="btn-danger btn-sm" onClick={() => handleDeleteSession(s._id)}>
                    Delete
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="card">
        <h3>Containers ({containers.length})</h3>
        <table className="admin-table">
          <thead>
            <tr>
              <th>Docker ID</th>
              <th>Session</th>
              <th>Status</th>
              <th>Actions</th>
            </tr>
          </thead>
          <tbody>
            {containers.map((c) => (
              <tr key={c._id}>
                <td className="mono">{c.docker_id.slice(0, 12)}</td>
                <td>{c.session_id.slice(0, 8)}...</td>
                <td><span className={`status-badge status-${c.status}`}>{c.status}</span></td>
                <td>
                  <button className="btn-danger btn-sm" onClick={() => handleRemoveContainer(c._id)}>
                    Remove
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

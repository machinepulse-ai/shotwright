import { useState, useEffect } from "react";
import {
  adminLogin,
  getAdminDashboard,
  getAdminSettings,
  updateGithubToken,
  updateAdminSettings,
  getContainers,
  removeContainer,
  getSessions,
  deleteSession,
} from "../../services/api";
import { AdminSettings, Container, DashboardData, Session } from "../../types";
import { useI18n } from "../../i18n";
import "./AdminPanel.css";

const defaultAdminSettings: AdminSettings = {
  github_token_set: false,
  copilot_cli_path: "",
  copilot_workspace_root: "C:/workspace",
  copilot_use_logged_in_user: false,
  copilot_http_proxy: "",
  copilot_https_proxy: "",
  copilot_no_proxy: "",
};

export default function AdminPanel() {
  const { copy, locale } = useI18n();
  const [authenticated, setAuthenticated] = useState(!!localStorage.getItem("shotwright_token"));
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [dashboard, setDashboard] = useState<DashboardData | null>(null);
  const [githubToken, setGithubToken] = useState("");
  const [tokenSet, setTokenSet] = useState(false);
  const [runtimeSettings, setRuntimeSettings] = useState<AdminSettings>(defaultAdminSettings);
  const [sessions, setSessions] = useState<Session[]>([]);
  const [containers, setContainers] = useState<Container[]>([]);
  const [savingToken, setSavingToken] = useState(false);
  const [savingSettings, setSavingSettings] = useState(false);
  const [actionMessage, setActionMessage] = useState("");
  const [actionError, setActionError] = useState("");
  const sessionStatusLabels = copy.status.session;
  const containerStatusLabels = copy.status.container;

  const resetFeedback = () => {
    setActionMessage("");
    setActionError("");
  };

  const login = async () => {
    try {
      const res = await adminLogin(password);
      localStorage.setItem("shotwright_token", res.data.access_token);
      setAuthenticated(true);
      setError("");
    } catch {
      setError(copy.errors.invalidPassword);
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
      setRuntimeSettings(settingsRes.data);
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
    resetFeedback();
    setSavingToken(true);
    try {
      await updateGithubToken(githubToken.trim());
      setGithubToken("");
      setTokenSet(true);
      setActionMessage(copy.common.saved);
    } catch {
      setActionError(copy.errors.failedUpdateGithubToken);
    } finally {
      setSavingToken(false);
    }
  };

  const handleRuntimeSettingChange = <K extends keyof AdminSettings>(key: K, value: AdminSettings[K]) => {
    resetFeedback();
    setRuntimeSettings((previous) => ({ ...previous, [key]: value }));
  };

  const handleSaveSettings = async () => {
    resetFeedback();
    setSavingSettings(true);
    try {
      const response = await updateAdminSettings({
        copilot_cli_path: runtimeSettings.copilot_cli_path,
        copilot_workspace_root: runtimeSettings.copilot_workspace_root,
        copilot_use_logged_in_user: runtimeSettings.copilot_use_logged_in_user,
        copilot_http_proxy: runtimeSettings.copilot_http_proxy,
        copilot_https_proxy: runtimeSettings.copilot_https_proxy,
        copilot_no_proxy: runtimeSettings.copilot_no_proxy,
      });
      setRuntimeSettings(response.data);
      setTokenSet(response.data.github_token_set);
      setActionMessage(copy.common.saved);
    } catch {
      setActionError(copy.errors.failedUpdateAdminSettings);
    } finally {
      setSavingSettings(false);
    }
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
      <div className="admin-shell admin-shell-login">
        <div className="admin-login">
          <div className="card login-card">
            <span className="eyebrow">{copy.admin.loginEyebrow}</span>
            <h2>{copy.admin.loginTitle}</h2>
            <p className="login-copy">{copy.admin.loginCopy}</p>
            <input
              type="password"
              placeholder={copy.admin.passwordPlaceholder}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && login()}
            />
            {error && <p className="login-error">{error}</p>}
            <button className="btn-primary" onClick={login}>
              {copy.common.login}
            </button>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="admin-shell">
      <div className="admin-panel">
        <div className="admin-header">
          <div>
            <span className="eyebrow">{copy.admin.headerEyebrow}</span>
            <h2>{copy.admin.headerTitle}</h2>
            <p>{copy.admin.headerCopy}</p>
          </div>
          <button className="btn-danger" onClick={logout}>
            {copy.common.logout}
          </button>
        </div>

        {dashboard && (
          <div className="stats-grid">
            <div className="card stat-card">
              <div className="stat-value">{dashboard.total_sessions}</div>
              <div className="stat-label">{copy.admin.stats.totalSessions}</div>
            </div>
            <div className="card stat-card">
              <div className="stat-value">{dashboard.active_sessions}</div>
              <div className="stat-label">{copy.admin.stats.activeSessions}</div>
            </div>
            <div className="card stat-card">
              <div className="stat-value">{dashboard.total_containers}</div>
              <div className="stat-label">{copy.admin.stats.totalContainers}</div>
            </div>
            <div className="card stat-card">
              <div className="stat-value">{dashboard.running_containers}</div>
              <div className="stat-label">{copy.admin.stats.runningContainers}</div>
            </div>
          </div>
        )}

        {(actionMessage || actionError) && (
          <div className={`admin-feedback ${actionError ? "error" : "success"}`}>{actionError || actionMessage}</div>
        )}

        <div className="card admin-section admin-section-hero">
          <div className="panel-heading">
            <div>
              <span className="eyebrow">{copy.admin.credentialsEyebrow}</span>
              <h3>{copy.admin.credentialsTitle}</h3>
              <p className="admin-section-copy">{copy.admin.credentialsDescription}</p>
            </div>
            <span className={`status-badge status-${tokenSet ? "active" : "idle"}`}>{tokenSet ? copy.status.token.set : copy.status.token.notSet}</span>
          </div>
          <p className="token-status">
            {copy.admin.tokenStatus}: {tokenSet ? <span className="status-badge status-active">{copy.status.token.set}</span> : <span className="status-badge status-idle">{copy.status.token.notSet}</span>}
          </p>
          <p className="field-help">{copy.admin.tokenHelp}</p>
          <div className="token-input">
            <input
              type="password"
              placeholder={copy.admin.tokenPlaceholder}
              value={githubToken}
              onChange={(e) => setGithubToken(e.target.value)}
            />
            <button className="btn-primary" onClick={handleTokenUpdate} disabled={savingToken || !githubToken.trim()}>
              {savingToken ? copy.common.saving : copy.common.update}
            </button>
          </div>
        </div>

        <div className="card admin-section">
          <div className="panel-heading">
            <div>
              <span className="eyebrow">{copy.admin.configEyebrow}</span>
              <h3>{copy.admin.configTitle}</h3>
              <p className="admin-section-copy">{copy.admin.configDescription}</p>
            </div>
          </div>

          <div className="admin-config-grid">
            <label className="form-field">
              <span className="field-label">{copy.admin.fields.workspaceRoot}</span>
              <input
                value={runtimeSettings.copilot_workspace_root}
                onChange={(event) => handleRuntimeSettingChange("copilot_workspace_root", event.target.value)}
              />
            </label>

            <label className="form-field">
              <span className="field-label">{copy.admin.fields.cliPath}</span>
              <input
                value={runtimeSettings.copilot_cli_path}
                placeholder={copy.admin.placeholders.inherit}
                onChange={(event) => handleRuntimeSettingChange("copilot_cli_path", event.target.value)}
              />
            </label>

            <label className="form-field form-field-wide checkbox-field">
              <span className="field-label">{copy.admin.fields.useLoggedInUser}</span>
              <span className="checkbox-row">
                <input
                  type="checkbox"
                  checked={runtimeSettings.copilot_use_logged_in_user}
                  onChange={(event) => handleRuntimeSettingChange("copilot_use_logged_in_user", event.target.checked)}
                />
                <span>{copy.admin.useLoggedInUserHint}</span>
              </span>
            </label>

            <label className="form-field">
              <span className="field-label">{copy.admin.fields.httpProxy}</span>
              <input
                value={runtimeSettings.copilot_http_proxy}
                placeholder={copy.admin.placeholders.inherit}
                onChange={(event) => handleRuntimeSettingChange("copilot_http_proxy", event.target.value)}
              />
            </label>

            <label className="form-field">
              <span className="field-label">{copy.admin.fields.httpsProxy}</span>
              <input
                value={runtimeSettings.copilot_https_proxy}
                placeholder={copy.admin.placeholders.inherit}
                onChange={(event) => handleRuntimeSettingChange("copilot_https_proxy", event.target.value)}
              />
            </label>

            <label className="form-field form-field-wide">
              <span className="field-label">{copy.admin.fields.noProxy}</span>
              <input
                value={runtimeSettings.copilot_no_proxy}
                placeholder={copy.admin.placeholders.inherit}
                onChange={(event) => handleRuntimeSettingChange("copilot_no_proxy", event.target.value)}
              />
            </label>
          </div>

          <p className="field-help">{copy.admin.configHint}</p>
          <div className="admin-actions">
            <button className="btn-primary" onClick={handleSaveSettings} disabled={savingSettings || !runtimeSettings.copilot_workspace_root.trim()}>
              {savingSettings ? copy.common.saving : copy.common.save}
            </button>
          </div>
        </div>

        <div className="admin-grid">
          <div className="card admin-section">
            <div className="panel-heading">
              <div>
                <span className="eyebrow">{copy.admin.sessionsEyebrow}</span>
                <h3>{copy.admin.sessionsTitle}</h3>
              </div>
              <span className="panel-count">{sessions.length}</span>
            </div>
            <div className="table-wrap">
              <table className="admin-table">
                <thead>
                  <tr>
                    <th>{copy.admin.columns.name}</th>
                    <th>{copy.admin.columns.status}</th>
                    <th>{copy.admin.columns.created}</th>
                    <th>{copy.admin.columns.actions}</th>
                  </tr>
                </thead>
                <tbody>
                  {sessions.map((s) => (
                    <tr key={s._id}>
                      <td>{s.name}</td>
                      <td><span className={`status-badge status-${s.status}`}>{sessionStatusLabels[s.status]}</span></td>
                      <td>{new Date(s.created_at).toLocaleString(locale)}</td>
                      <td>
                        <button className="btn-danger btn-sm" onClick={() => handleDeleteSession(s._id)}>
                          {copy.common.deleteSession}
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>

          <div className="card admin-section">
            <div className="panel-heading">
              <div>
                <span className="eyebrow">{copy.admin.runtimeEyebrow}</span>
                <h3>{copy.admin.runtimeTitle}</h3>
              </div>
              <span className="panel-count">{containers.length}</span>
            </div>
            <div className="table-wrap">
              <table className="admin-table">
                <thead>
                  <tr>
                    <th>{copy.admin.columns.dockerId}</th>
                    <th>{copy.admin.columns.session}</th>
                    <th>{copy.admin.columns.status}</th>
                    <th>{copy.admin.columns.actions}</th>
                  </tr>
                </thead>
                <tbody>
                  {containers.map((c) => (
                    <tr key={c._id}>
                      <td className="mono">{c.docker_id.slice(0, 12)}</td>
                      <td>{c.session_id.slice(0, 8)}...</td>
                      <td><span className={`status-badge status-${c.status}`}>{containerStatusLabels[c.status]}</span></td>
                      <td>
                        <button className="btn-danger btn-sm" onClick={() => handleRemoveContainer(c._id)}>
                          {copy.common.remove}
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

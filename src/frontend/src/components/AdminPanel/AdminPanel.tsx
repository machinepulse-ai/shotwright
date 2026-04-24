import { ChangeEvent, useEffect, useMemo, useState } from "react";
import {
  adminLogin,
  getAdminDashboard,
  getAdminSettings,
  getCopilotModelOptions,
  isRequestAbortError,
  updateGithubToken,
  updateAdminSettings,
  getContainers,
  removeContainer,
  getSessions,
  deleteSession,
} from "../../services/api";
import { AdminSettings, Container, CopilotModelOption, DashboardData, ReasoningEffort, Session } from "../../types";
import { useI18n } from "../../i18n";
import "./AdminPanel.css";

const defaultAdminSettings: AdminSettings = {
  github_token_set: false,
  default_copilot_model: "gpt-5.4",
  default_copilot_reasoning_effort: "high",
  copilot_turn_timeout_seconds: 900,
  copilot_cli_path: "",
  copilot_workspace_root: "C:/workspace",
  copilot_use_logged_in_user: false,
  copilot_http_proxy: "",
  copilot_https_proxy: "",
  copilot_no_proxy: "",
};

function normalizeAdminSettings(settings: Partial<AdminSettings> | null | undefined): AdminSettings {
  return {
    ...defaultAdminSettings,
    ...(settings || {}),
    default_copilot_model:
      typeof settings?.default_copilot_model === "string" && settings.default_copilot_model.trim()
        ? settings.default_copilot_model
        : defaultAdminSettings.default_copilot_model,
    default_copilot_reasoning_effort:
      settings && "default_copilot_reasoning_effort" in settings
        ? settings.default_copilot_reasoning_effort ?? null
        : defaultAdminSettings.default_copilot_reasoning_effort,
    copilot_turn_timeout_seconds:
      typeof settings?.copilot_turn_timeout_seconds === "number" && settings.copilot_turn_timeout_seconds > 0
        ? settings.copilot_turn_timeout_seconds
        : defaultAdminSettings.copilot_turn_timeout_seconds,
    copilot_cli_path: settings?.copilot_cli_path ?? defaultAdminSettings.copilot_cli_path,
    copilot_workspace_root: settings?.copilot_workspace_root ?? defaultAdminSettings.copilot_workspace_root,
    copilot_http_proxy: settings?.copilot_http_proxy ?? defaultAdminSettings.copilot_http_proxy,
    copilot_https_proxy: settings?.copilot_https_proxy ?? defaultAdminSettings.copilot_https_proxy,
    copilot_no_proxy: settings?.copilot_no_proxy ?? defaultAdminSettings.copilot_no_proxy,
    copilot_use_logged_in_user: settings?.copilot_use_logged_in_user ?? defaultAdminSettings.copilot_use_logged_in_user,
    github_token_set: settings?.github_token_set ?? defaultAdminSettings.github_token_set,
  };
}

function buildFallbackModelOption(settings: AdminSettings): CopilotModelOption {
  return {
    id: settings.default_copilot_model,
    name: settings.default_copilot_model,
    supports_reasoning_effort: Boolean(settings.default_copilot_reasoning_effort),
    supported_reasoning_efforts: settings.default_copilot_reasoning_effort
      ? [settings.default_copilot_reasoning_effort]
      : [],
    default_reasoning_effort: settings.default_copilot_reasoning_effort,
  };
}

export default function AdminPanel() {
  const { copy, locale } = useI18n();
  const [authenticated, setAuthenticated] = useState(!!localStorage.getItem("shotwright_token"));
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [dashboard, setDashboard] = useState<DashboardData | null>(null);
  const [githubToken, setGithubToken] = useState("");
  const [tokenSet, setTokenSet] = useState(false);
  const [runtimeSettings, setRuntimeSettings] = useState<AdminSettings>(defaultAdminSettings);
  const [modelOptions, setModelOptions] = useState<CopilotModelOption[]>([]);
  const [sessions, setSessions] = useState<Session[]>([]);
  const [containers, setContainers] = useState<Container[]>([]);
  const [modelOptionsLoading, setModelOptionsLoading] = useState(false);
  const [savingToken, setSavingToken] = useState(false);
  const [savingSettings, setSavingSettings] = useState(false);
  const [actionMessage, setActionMessage] = useState("");
  const [actionError, setActionError] = useState("");
  const sessionStatusLabels = copy.status.session;
  const containerStatusLabels = copy.status.container;

  const adminModelOptions = useMemo(() => {
    if (!runtimeSettings.default_copilot_model) return modelOptions;
    if (modelOptions.some((option) => option.id === runtimeSettings.default_copilot_model)) return modelOptions;
    return [buildFallbackModelOption(runtimeSettings), ...modelOptions];
  }, [modelOptions, runtimeSettings]);

  const selectedDefaultModel = useMemo(
    () => adminModelOptions.find((option) => option.id === runtimeSettings.default_copilot_model) ?? null,
    [adminModelOptions, runtimeSettings.default_copilot_model]
  );

  const defaultReasoningOptions = selectedDefaultModel?.supported_reasoning_efforts ?? [];
  const defaultReasoningSupported = Boolean(
    selectedDefaultModel?.supports_reasoning_effort && defaultReasoningOptions.length
  );

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
      setRuntimeSettings(normalizeAdminSettings(settingsRes.data));
      setSessions(sessionsRes.data);
      setContainers(containersRes.data);
    } catch {
      setAuthenticated(false);
      localStorage.removeItem("shotwright_token");
    }
  };

  const loadModelOptions = async (signal?: AbortSignal) => {
    setModelOptionsLoading(true);
    try {
      const response = await getCopilotModelOptions(signal);
      setModelOptions(response.data);
    } catch (error) {
      if (isRequestAbortError(error)) {
        return;
      }
      setModelOptions([]);
    } finally {
      setModelOptionsLoading(false);
    }
  };

  useEffect(() => {
    if (authenticated) {
      fetchData();
      const controller = new AbortController();
      void loadModelOptions(controller.signal);
      return () => controller.abort();
    }
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

  const handleDefaultModelChange = (event: ChangeEvent<HTMLSelectElement>) => {
    const nextModel = event.target.value;
    const nextModelOption = adminModelOptions.find((option) => option.id === nextModel) ?? null;

    let nextReasoning: ReasoningEffort | null = runtimeSettings.default_copilot_reasoning_effort;
    if (!nextModelOption?.supports_reasoning_effort || !nextModelOption.supported_reasoning_efforts.length) {
      nextReasoning = null;
    } else if (!nextReasoning || !nextModelOption.supported_reasoning_efforts.includes(nextReasoning)) {
      nextReasoning = nextModelOption.default_reasoning_effort ?? nextModelOption.supported_reasoning_efforts[0] ?? null;
    }

    resetFeedback();
    setRuntimeSettings((previous) => ({
      ...previous,
      default_copilot_model: nextModel,
      default_copilot_reasoning_effort: nextReasoning,
    }));
  };

  const handleDefaultReasoningChange = (event: ChangeEvent<HTMLSelectElement>) => {
    const nextReasoning = (event.target.value || null) as ReasoningEffort | null;
    handleRuntimeSettingChange("default_copilot_reasoning_effort", nextReasoning);
  };

  const handleSaveSettings = async () => {
    resetFeedback();
    setSavingSettings(true);
    try {
      const response = await updateAdminSettings({
        default_copilot_model: runtimeSettings.default_copilot_model,
        default_copilot_reasoning_effort: defaultReasoningSupported
          ? runtimeSettings.default_copilot_reasoning_effort
          : null,
        copilot_turn_timeout_seconds: runtimeSettings.copilot_turn_timeout_seconds,
        copilot_cli_path: runtimeSettings.copilot_cli_path,
        copilot_workspace_root: runtimeSettings.copilot_workspace_root,
        copilot_use_logged_in_user: runtimeSettings.copilot_use_logged_in_user,
        copilot_http_proxy: runtimeSettings.copilot_http_proxy,
        copilot_https_proxy: runtimeSettings.copilot_https_proxy,
        copilot_no_proxy: runtimeSettings.copilot_no_proxy,
      });
      setRuntimeSettings(normalizeAdminSettings(response.data));
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
              <span className="field-label">{copy.admin.fields.defaultModel}</span>
              <select
                value={runtimeSettings.default_copilot_model}
                onChange={handleDefaultModelChange}
                disabled={modelOptionsLoading || !adminModelOptions.length}
              >
                {adminModelOptions.length ? (
                  adminModelOptions.map((option) => (
                    <option key={option.id} value={option.id}>
                      {option.name}
                    </option>
                  ))
                ) : (
                  <option value="">{copy.agent.sessionSettingsNoOptions}</option>
                )}
              </select>
            </label>

            <label className="form-field">
              <span className="field-label">{copy.admin.fields.defaultReasoning}</span>
              <select
                value={runtimeSettings.default_copilot_reasoning_effort ?? ""}
                onChange={handleDefaultReasoningChange}
                disabled={!defaultReasoningSupported}
              >
                {defaultReasoningSupported ? (
                  defaultReasoningOptions.map((effort) => (
                    <option key={effort} value={effort}>
                      {copy.common.reasoningEfforts[effort]}
                    </option>
                  ))
                ) : (
                  <option value="">{copy.agent.sessionSettingsReasoningDisabled}</option>
                )}
              </select>
            </label>

            <label className="form-field">
              <span className="field-label">{copy.admin.fields.turnTimeout}</span>
              <input
                type="number"
                min={1}
                step={1}
                value={runtimeSettings.copilot_turn_timeout_seconds}
                onChange={(event) => {
                  const nextValue = Number.parseFloat(event.target.value);
                  handleRuntimeSettingChange(
                    "copilot_turn_timeout_seconds",
                    Number.isFinite(nextValue) && nextValue > 0 ? nextValue : 0,
                  );
                }}
              />
            </label>

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

          <p className="field-help">
            {modelOptionsLoading
              ? copy.admin.defaultSessionLoading
              : adminModelOptions.length
                ? copy.admin.defaultSessionHint
                : copy.admin.defaultSessionUnavailable}
          </p>
          <p className="field-help">{copy.admin.configHint}</p>
          <div className="admin-actions">
            <button
              className="btn-primary"
              onClick={handleSaveSettings}
              disabled={
                savingSettings ||
                !runtimeSettings.copilot_workspace_root.trim() ||
                !runtimeSettings.default_copilot_model.trim() ||
                runtimeSettings.copilot_turn_timeout_seconds <= 0
              }
            >
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

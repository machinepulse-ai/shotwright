import { ChangeEvent, useEffect, useMemo, useState } from "react";
import {
  adminLogin,
  getAdminCopilotModelOptions,
  getAdminDashboard,
  getAdminSettings,
  isRequestAbortError,
  logoffCurrentAccount,
  updateGithubToken,
  updateOpenAIKey,
  updateAdminSettings,
  getContainers,
  removeContainer,
  getSessions,
  deleteSession,
} from "../../services/api";
import { AdminSettings, Container, CopilotModelOption, DashboardData, ReasoningEffort, Session } from "../../types";
import { useI18n } from "../../i18n";
import {
  formatAgentModelLabel,
  formatModelOptionLabel,
  getAgentModelDescriptor,
  getSessionModelToneClass,
} from "../../utils/agentModel";
import "./AdminPanel.css";

const defaultAdminSettings: AdminSettings = {
  agent_provider: "copilot",
  github_token_set: false,
  openai_api_key_set: false,
  default_copilot_model: "gpt-5.4",
  default_copilot_reasoning_effort: "high",
  copilot_turn_timeout_seconds: 900,
  copilot_cli_path: "",
  copilot_workspace_root: "C:/workspace",
  copilot_use_logged_in_user: false,
  copilot_http_proxy: "",
  copilot_https_proxy: "",
  copilot_no_proxy: "",
  codex_node_path: "",
  codex_bridge_script: "",
  codex_path_override: "",
  codex_base_url: "",
  codex_model: "gpt-5.4",
  codex_reasoning_effort: "high",
  codex_turn_timeout_seconds: 900,
  codex_workspace_root: "C:/workspace",
  codex_approval_policy: "never",
  codex_sandbox_mode: "workspace-write",
  codex_network_access_enabled: false,
  codex_skip_git_repo_check: false,
  codex_web_search_mode: "",
  codex_http_proxy: "",
  codex_https_proxy: "",
  codex_no_proxy: "",
};

function normalizeAdminSettings(settings: Partial<AdminSettings> | null | undefined): AdminSettings {
  return {
    ...defaultAdminSettings,
    ...(settings || {}),
    agent_provider: settings?.agent_provider === "codex" ? "codex" : "copilot",
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
    openai_api_key_set: settings?.openai_api_key_set ?? defaultAdminSettings.openai_api_key_set,
    codex_node_path: settings?.codex_node_path ?? defaultAdminSettings.codex_node_path,
    codex_bridge_script: settings?.codex_bridge_script ?? defaultAdminSettings.codex_bridge_script,
    codex_path_override: settings?.codex_path_override ?? defaultAdminSettings.codex_path_override,
    codex_base_url: settings?.codex_base_url ?? defaultAdminSettings.codex_base_url,
    codex_model:
      typeof settings?.codex_model === "string" && settings.codex_model.trim()
        ? settings.codex_model
        : defaultAdminSettings.codex_model,
    codex_reasoning_effort:
      settings && "codex_reasoning_effort" in settings
        ? settings.codex_reasoning_effort ?? null
        : defaultAdminSettings.codex_reasoning_effort,
    codex_turn_timeout_seconds:
      typeof settings?.codex_turn_timeout_seconds === "number" && settings.codex_turn_timeout_seconds > 0
        ? settings.codex_turn_timeout_seconds
        : defaultAdminSettings.codex_turn_timeout_seconds,
    codex_workspace_root: settings?.codex_workspace_root ?? defaultAdminSettings.codex_workspace_root,
    codex_approval_policy: settings?.codex_approval_policy ?? defaultAdminSettings.codex_approval_policy,
    codex_sandbox_mode: settings?.codex_sandbox_mode ?? defaultAdminSettings.codex_sandbox_mode,
    codex_network_access_enabled:
      settings?.codex_network_access_enabled ?? defaultAdminSettings.codex_network_access_enabled,
    codex_skip_git_repo_check: settings?.codex_skip_git_repo_check ?? defaultAdminSettings.codex_skip_git_repo_check,
    codex_web_search_mode: settings?.codex_web_search_mode ?? defaultAdminSettings.codex_web_search_mode,
    codex_http_proxy: settings?.codex_http_proxy ?? defaultAdminSettings.codex_http_proxy,
    codex_https_proxy: settings?.codex_https_proxy ?? defaultAdminSettings.codex_https_proxy,
    codex_no_proxy: settings?.codex_no_proxy ?? defaultAdminSettings.codex_no_proxy,
  };
}

function buildFallbackModelOption(settings: AdminSettings): CopilotModelOption {
  const descriptor = getAgentModelDescriptor(settings.agent_provider, settings.default_copilot_model);
  return {
    id: settings.default_copilot_model,
    name: settings.default_copilot_model,
    provider: settings.agent_provider,
    brand: descriptor.brandLabel,
    submodel: descriptor.submodelLabel,
    display_name: descriptor.modelLabel,
    supports_reasoning_effort: Boolean(settings.default_copilot_reasoning_effort),
    supported_reasoning_efforts: settings.default_copilot_reasoning_effort
      ? [settings.default_copilot_reasoning_effort]
      : [],
    default_reasoning_effort: settings.default_copilot_reasoning_effort,
  };
}

const containerStatusPriority: Record<Container["status"], number> = {
  running: 0,
  creating: 1,
  error: 2,
  stopped: 3,
  removed: 4,
};

export default function AdminPanel() {
  const { copy, locale } = useI18n();
  const [authenticated, setAuthenticated] = useState(!!localStorage.getItem("shotwright_token"));
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [dashboard, setDashboard] = useState<DashboardData | null>(null);
  const [githubToken, setGithubToken] = useState("");
  const [openAIKey, setOpenAIKey] = useState("");
  const [tokenSet, setTokenSet] = useState(false);
  const [openAIKeySet, setOpenAIKeySet] = useState(false);
  const [runtimeSettings, setRuntimeSettings] = useState<AdminSettings>(defaultAdminSettings);
  const [modelOptions, setModelOptions] = useState<CopilotModelOption[]>([]);
  const [sessions, setSessions] = useState<Session[]>([]);
  const [containers, setContainers] = useState<Container[]>([]);
  const [modelOptionsLoading, setModelOptionsLoading] = useState(false);
  const [savingToken, setSavingToken] = useState(false);
  const [savingOpenAIKey, setSavingOpenAIKey] = useState(false);
  const [savingSettings, setSavingSettings] = useState(false);
  const [loggingOffCurrentAccount, setLoggingOffCurrentAccount] = useState(false);
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
  const codexReasoningOptions: ReasoningEffort[] = ["low", "medium", "high", "xhigh"];
  const isCodexProvider = runtimeSettings.agent_provider === "codex";
  const activeProviderName = isCodexProvider ? copy.admin.providers.codex : copy.admin.providers.copilot;
  const defaultModelDescriptor = getAgentModelDescriptor(
    "copilot",
    runtimeSettings.default_copilot_model,
    selectedDefaultModel,
  );
  const codexModelDescriptor = getAgentModelDescriptor("codex", runtimeSettings.codex_model);
  const settingsSaveDisabled =
    savingSettings ||
    (isCodexProvider
      ? !runtimeSettings.codex_workspace_root.trim() ||
        !runtimeSettings.codex_model.trim() ||
        runtimeSettings.codex_turn_timeout_seconds <= 0
      : !runtimeSettings.copilot_workspace_root.trim() ||
        !runtimeSettings.default_copilot_model.trim() ||
        runtimeSettings.copilot_turn_timeout_seconds <= 0);
  const sessionRuntimeRows = useMemo(() => {
    const containersBySession = new Map<string, Container[]>();

    containers.forEach((container) => {
      const existing = containersBySession.get(container.session_id);
      if (existing) {
        existing.push(container);
      } else {
        containersBySession.set(container.session_id, [container]);
      }
    });

    return sessions.map((session) => {
      const linkedContainers = [...(containersBySession.get(session._id) ?? [])];
      const directContainer = session.container_id
        ? containers.find((container) => container._id === session.container_id)
        : undefined;

      if (directContainer && !linkedContainers.some((container) => container._id === directContainer._id)) {
        linkedContainers.push(directContainer);
      }

      linkedContainers.sort((left, right) => {
        const priorityDelta = containerStatusPriority[left.status] - containerStatusPriority[right.status];
        if (priorityDelta !== 0) return priorityDelta;
        return new Date(right.created_at).getTime() - new Date(left.created_at).getTime();
      });

      return {
        session,
        primaryContainer: linkedContainers[0] ?? null,
        extraContainerCount: Math.max(0, linkedContainers.length - 1),
      };
    });
  }, [containers, sessions]);

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
      setOpenAIKeySet(settingsRes.data.openai_api_key_set);
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
      const response = await getAdminCopilotModelOptions(signal);
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

  const handleOpenAIKeyUpdate = async () => {
    if (!openAIKey.trim()) return;
    resetFeedback();
    setSavingOpenAIKey(true);
    try {
      await updateOpenAIKey(openAIKey.trim());
      setOpenAIKey("");
      setOpenAIKeySet(true);
      setActionMessage(copy.common.saved);
    } catch {
      setActionError(copy.errors.failedUpdateOpenAIKey);
    } finally {
      setSavingOpenAIKey(false);
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
        agent_provider: runtimeSettings.agent_provider,
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
        codex_node_path: runtimeSettings.codex_node_path,
        codex_bridge_script: runtimeSettings.codex_bridge_script,
        codex_path_override: runtimeSettings.codex_path_override,
        codex_base_url: runtimeSettings.codex_base_url,
        codex_model: runtimeSettings.codex_model,
        codex_reasoning_effort: runtimeSettings.codex_reasoning_effort,
        codex_turn_timeout_seconds: runtimeSettings.codex_turn_timeout_seconds,
        codex_workspace_root: runtimeSettings.codex_workspace_root,
        codex_approval_policy: runtimeSettings.codex_approval_policy,
        codex_sandbox_mode: runtimeSettings.codex_sandbox_mode,
        codex_network_access_enabled: runtimeSettings.codex_network_access_enabled,
        codex_skip_git_repo_check: runtimeSettings.codex_skip_git_repo_check,
        codex_web_search_mode: runtimeSettings.codex_web_search_mode,
        codex_http_proxy: runtimeSettings.codex_http_proxy,
        codex_https_proxy: runtimeSettings.codex_https_proxy,
        codex_no_proxy: runtimeSettings.codex_no_proxy,
      });
      setRuntimeSettings(normalizeAdminSettings(response.data));
      setTokenSet(response.data.github_token_set);
      setOpenAIKeySet(response.data.openai_api_key_set);
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

  const handleLogoffCurrentAccount = async () => {
    resetFeedback();
    setLoggingOffCurrentAccount(true);
    try {
      await logoffCurrentAccount();
    } catch {
      // The endpoint is provided by the upstream proxy in some deployments.
      // Local admin logout should still complete when the proxy route is absent.
    } finally {
      localStorage.removeItem("shotwright_token");
      if (typeof window !== "undefined") {
        window.location.reload();
        return;
      }
      setLoggingOffCurrentAccount(false);
      setAuthenticated(false);
    }
  };

  if (!authenticated) {
    return (
      <div className="admin-shell admin-shell-login">
        <div className="admin-login">
          <div className="card login-card">
            <span className="eyebrow">{copy.admin.loginEyebrow}</span>
            <h2>{copy.admin.loginTitle}</h2>
            <p className="login-copy">{copy.admin.loginCopy}</p>
            <div className="admin-login-actions">
              <button
                className="btn-danger"
                onClick={() => void handleLogoffCurrentAccount()}
                disabled={loggingOffCurrentAccount}
              >
                {loggingOffCurrentAccount ? copy.common.working : copy.admin.logoffCurrentAccount}
              </button>
            </div>
            <input
              type="text"
              className="secret-input"
              name="shotwright-admin-access-code"
              autoComplete="off"
              autoCorrect="off"
              autoCapitalize="none"
              spellCheck={false}
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
          <div className="admin-header-actions">
            <button className="ghost-button" onClick={logout}>
              {copy.admin.localLogout}
            </button>
            <button
              className="btn-danger"
              onClick={() => void handleLogoffCurrentAccount()}
              disabled={loggingOffCurrentAccount}
            >
              {loggingOffCurrentAccount ? copy.common.working : copy.admin.logoffCurrentAccount}
            </button>
          </div>
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

        <div className="settings-action-bar">
          <div className="settings-action-copy">
            <span className="eyebrow">{copy.admin.settingsActionEyebrow}</span>
            <strong>{activeProviderName}</strong>
            <p>{copy.admin.saveNotice}</p>
          </div>
          <button className="btn-primary" onClick={handleSaveSettings} disabled={settingsSaveDisabled}>
            {savingSettings ? copy.common.saving : copy.common.save}
          </button>
        </div>

        <div className="card admin-section admin-section-hero">
          <div className="panel-heading">
            <div>
              <span className="eyebrow">{copy.admin.providerEyebrow}</span>
              <h3>{copy.admin.providerTitle}</h3>
              <p className="admin-section-copy">{copy.admin.providerDescription}</p>
            </div>
          </div>
          <div className="provider-switch">
            <label className={`provider-option ${runtimeSettings.agent_provider === "copilot" ? "active" : ""}`}>
              <input
                type="radio"
                name="agent-provider"
                checked={runtimeSettings.agent_provider === "copilot"}
                onChange={() => handleRuntimeSettingChange("agent_provider", "copilot")}
              />
              <span>{copy.admin.providers.copilot}</span>
            </label>
            <label className={`provider-option ${runtimeSettings.agent_provider === "codex" ? "active" : ""}`}>
              <input
                type="radio"
                name="agent-provider"
                checked={runtimeSettings.agent_provider === "codex"}
                onChange={() => handleRuntimeSettingChange("agent_provider", "codex")}
              />
              <span>{copy.admin.providers.codex}</span>
            </label>
          </div>
        </div>

        <div className="credential-grid credential-grid-single">
          {runtimeSettings.agent_provider === "copilot" && (
          <div className="card admin-section">
            <div className="panel-heading">
              <div>
                <span className="eyebrow">{copy.admin.credentialsEyebrow}</span>
                <h3>{copy.admin.copilotCredentialsTitle}</h3>
                <p className="admin-section-copy">{copy.admin.copilotCredentialsDescription}</p>
              </div>
              <span className={`status-badge status-${tokenSet ? "active" : "idle"}`}>
                {tokenSet ? copy.status.token.set : copy.status.token.notSet}
              </span>
            </div>
            <p className="field-help">{copy.admin.tokenHelp}</p>
            <div className="token-input">
              <input
                type="text"
                className="secret-input"
                name="shotwright-github-token-manual"
                autoComplete="off"
                autoCorrect="off"
                autoCapitalize="none"
                spellCheck={false}
                placeholder={copy.admin.tokenPlaceholder}
                value={githubToken}
                onChange={(e) => setGithubToken(e.target.value)}
              />
              <button className="btn-primary" onClick={handleTokenUpdate} disabled={savingToken || !githubToken.trim()}>
                {savingToken ? copy.common.saving : copy.common.update}
              </button>
            </div>
          </div>
          )}

          {runtimeSettings.agent_provider === "codex" && (
          <div className="card admin-section">
            <div className="panel-heading">
              <div>
                <span className="eyebrow">{copy.admin.credentialsEyebrow}</span>
                <h3>{copy.admin.codexCredentialsTitle}</h3>
                <p className="admin-section-copy">{copy.admin.codexCredentialsDescription}</p>
              </div>
              <span className={`status-badge status-${openAIKeySet ? "active" : "idle"}`}>
                {openAIKeySet ? copy.status.token.set : copy.status.token.notSet}
              </span>
            </div>
            <p className="field-help">{copy.admin.openAIKeyHelp}</p>
            {openAIKeySet && <p className="credential-note">{copy.admin.openAIKeySavedNotice}</p>}
            <div className="token-input">
              <input
                type="text"
                className="secret-input"
                name="shotwright-openai-api-key-manual"
                autoComplete="off"
                autoCorrect="off"
                autoCapitalize="none"
                spellCheck={false}
                placeholder={openAIKeySet ? copy.admin.openAIKeySavedPlaceholder : copy.admin.openAIKeyPlaceholder}
                value={openAIKey}
                onChange={(e) => setOpenAIKey(e.target.value)}
              />
              <button
                className="btn-primary"
                onClick={handleOpenAIKeyUpdate}
                disabled={savingOpenAIKey || !openAIKey.trim()}
              >
                {savingOpenAIKey ? copy.common.saving : copy.common.update}
              </button>
            </div>
          </div>
          )}
        </div>

        <div className="card admin-section">
          <div className="panel-heading">
            <div>
              <span className="eyebrow">{copy.admin.configEyebrow}</span>
              <h3>{copy.admin.configTitle}</h3>
              <p className="admin-section-copy">{copy.admin.configDescription}</p>
            </div>
          </div>

          <div className="provider-config-grid provider-config-grid-single">
            {runtimeSettings.agent_provider === "copilot" && (
            <div className="provider-config-panel">
              <div className="provider-config-heading">
                <span className="eyebrow">{copy.admin.providers.copilot}</span>
                <h4>{copy.admin.copilotConfigTitle}</h4>
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
                          {formatModelOptionLabel(option)}
                        </option>
                      ))
                    ) : (
                      <option value="">{copy.agent.sessionSettingsNoOptions}</option>
                    )}
                  </select>
                  <div className="agent-model-preview">
                    <span className={`agent-model-chip ${defaultModelDescriptor.toneClass}`}>
                      {formatAgentModelLabel("copilot", runtimeSettings.default_copilot_model, selectedDefaultModel)}
                    </span>
                    <span className="agent-model-submeta">
                      {defaultModelDescriptor.brandLabel} · {defaultModelDescriptor.submodelLabel}
                    </span>
                  </div>
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
            </div>
            )}

            {runtimeSettings.agent_provider === "codex" && (
            <div className="provider-config-panel">
              <div className="provider-config-heading">
                <span className="eyebrow">{copy.admin.providers.codex}</span>
                <h4>{copy.admin.codexConfigTitle}</h4>
              </div>

              <div className="admin-config-grid">
                <label className="form-field">
                  <span className="field-label">{copy.admin.fields.codexModel}</span>
                  <input
                    value={runtimeSettings.codex_model}
                    onChange={(event) => handleRuntimeSettingChange("codex_model", event.target.value)}
                  />
                  <div className="agent-model-preview">
                    <span className={`agent-model-chip ${getSessionModelToneClass(runtimeSettings.codex_model)}`}>
                      {formatAgentModelLabel("codex", runtimeSettings.codex_model)}
                    </span>
                    <span className="agent-model-submeta">
                      {codexModelDescriptor.brandLabel} · {codexModelDescriptor.submodelLabel}
                    </span>
                  </div>
                </label>

                <label className="form-field">
                  <span className="field-label">{copy.admin.fields.codexReasoning}</span>
                  <select
                    value={runtimeSettings.codex_reasoning_effort ?? ""}
                    onChange={(event) =>
                      handleRuntimeSettingChange("codex_reasoning_effort", (event.target.value || null) as ReasoningEffort | null)
                    }
                  >
                    {codexReasoningOptions.map((effort) => (
                      <option key={effort} value={effort}>
                        {copy.common.reasoningEfforts[effort]}
                      </option>
                    ))}
                  </select>
                </label>

                <label className="form-field">
                  <span className="field-label">{copy.admin.fields.codexBaseUrl}</span>
                  <input
                    value={runtimeSettings.codex_base_url}
                    placeholder={copy.admin.placeholders.inherit}
                    onChange={(event) => handleRuntimeSettingChange("codex_base_url", event.target.value)}
                  />
                </label>

                <label className="form-field">
                  <span className="field-label">{copy.admin.fields.turnTimeout}</span>
                  <input
                    type="number"
                    min={1}
                    step={1}
                    value={runtimeSettings.codex_turn_timeout_seconds}
                    onChange={(event) => {
                      const nextValue = Number.parseFloat(event.target.value);
                      handleRuntimeSettingChange(
                        "codex_turn_timeout_seconds",
                        Number.isFinite(nextValue) && nextValue > 0 ? nextValue : 0,
                      );
                    }}
                  />
                </label>

                <label className="form-field">
                  <span className="field-label">{copy.admin.fields.workspaceRoot}</span>
                  <input
                    value={runtimeSettings.codex_workspace_root}
                    onChange={(event) => handleRuntimeSettingChange("codex_workspace_root", event.target.value)}
                  />
                </label>

                <label className="form-field">
                  <span className="field-label">{copy.admin.fields.codexNodePath}</span>
                  <input
                    value={runtimeSettings.codex_node_path}
                    placeholder={copy.admin.placeholders.node}
                    onChange={(event) => handleRuntimeSettingChange("codex_node_path", event.target.value)}
                  />
                </label>

                <label className="form-field">
                  <span className="field-label">{copy.admin.fields.codexBridgeScript}</span>
                  <input
                    value={runtimeSettings.codex_bridge_script}
                    placeholder={copy.admin.placeholders.inherit}
                    onChange={(event) => handleRuntimeSettingChange("codex_bridge_script", event.target.value)}
                  />
                </label>

                <label className="form-field">
                  <span className="field-label">{copy.admin.fields.codexPathOverride}</span>
                  <input
                    value={runtimeSettings.codex_path_override}
                    placeholder={copy.admin.placeholders.inherit}
                    onChange={(event) => handleRuntimeSettingChange("codex_path_override", event.target.value)}
                  />
                </label>

                <label className="form-field">
                  <span className="field-label">{copy.admin.fields.codexApprovalPolicy}</span>
                  <select
                    value={runtimeSettings.codex_approval_policy}
                    onChange={(event) => handleRuntimeSettingChange("codex_approval_policy", event.target.value)}
                  >
                    <option value="never">{copy.admin.approvalPolicies.never}</option>
                    <option value="on-request">{copy.admin.approvalPolicies.onRequest}</option>
                    <option value="on-failure">{copy.admin.approvalPolicies.onFailure}</option>
                  </select>
                </label>

                <label className="form-field">
                  <span className="field-label">{copy.admin.fields.codexSandboxMode}</span>
                  <select
                    value={runtimeSettings.codex_sandbox_mode}
                    onChange={(event) => handleRuntimeSettingChange("codex_sandbox_mode", event.target.value)}
                  >
                    <option value="read-only">{copy.admin.sandboxModes.readOnly}</option>
                    <option value="workspace-write">{copy.admin.sandboxModes.workspaceWrite}</option>
                    <option value="danger-full-access">{copy.admin.sandboxModes.dangerFullAccess}</option>
                  </select>
                </label>

                <label className="form-field">
                  <span className="field-label">{copy.admin.fields.codexWebSearchMode}</span>
                  <input
                    value={runtimeSettings.codex_web_search_mode}
                    placeholder={copy.admin.placeholders.inherit}
                    onChange={(event) => handleRuntimeSettingChange("codex_web_search_mode", event.target.value)}
                  />
                </label>

                <label className="form-field form-field-wide checkbox-field">
                  <span className="field-label">{copy.admin.fields.codexFlags}</span>
                  <span className="checkbox-row">
                    <input
                      type="checkbox"
                      checked={runtimeSettings.codex_network_access_enabled}
                      onChange={(event) => handleRuntimeSettingChange("codex_network_access_enabled", event.target.checked)}
                    />
                    <span>{copy.admin.fields.codexNetworkAccess}</span>
                  </span>
                  <span className="checkbox-row">
                    <input
                      type="checkbox"
                      checked={runtimeSettings.codex_skip_git_repo_check}
                      onChange={(event) => handleRuntimeSettingChange("codex_skip_git_repo_check", event.target.checked)}
                    />
                    <span>{copy.admin.fields.codexSkipGitRepoCheck}</span>
                  </span>
                </label>

                <label className="form-field">
                  <span className="field-label">{copy.admin.fields.httpProxy}</span>
                  <input
                    value={runtimeSettings.codex_http_proxy}
                    placeholder={copy.admin.placeholders.inherit}
                    onChange={(event) => handleRuntimeSettingChange("codex_http_proxy", event.target.value)}
                  />
                </label>

                <label className="form-field">
                  <span className="field-label">{copy.admin.fields.httpsProxy}</span>
                  <input
                    value={runtimeSettings.codex_https_proxy}
                    placeholder={copy.admin.placeholders.inherit}
                    onChange={(event) => handleRuntimeSettingChange("codex_https_proxy", event.target.value)}
                  />
                </label>

                <label className="form-field form-field-wide">
                  <span className="field-label">{copy.admin.fields.noProxy}</span>
                  <input
                    value={runtimeSettings.codex_no_proxy}
                    placeholder={copy.admin.placeholders.inherit}
                    onChange={(event) => handleRuntimeSettingChange("codex_no_proxy", event.target.value)}
                  />
                </label>
              </div>
            </div>
            )}
          </div>

          <p className="field-help">
            {modelOptionsLoading
              ? copy.admin.defaultSessionLoading
              : adminModelOptions.length
                ? copy.admin.defaultSessionHint
                : copy.admin.defaultSessionUnavailable}
          </p>
          <p className="field-help">{copy.admin.configHint}</p>
        </div>

        <div className="card admin-section admin-session-runtime-section">
            <div className="panel-heading">
              <div>
                <span className="eyebrow">{copy.admin.sessionsEyebrow}</span>
                <h3>{copy.admin.sessionRuntimeTitle}</h3>
                <p className="admin-section-copy">{copy.admin.sessionRuntimeDescription}</p>
              </div>
              <span className="panel-count">{sessions.length}</span>
            </div>
            <div className="table-wrap">
              <table className="admin-table admin-session-runtime-table">
                <thead>
                  <tr>
                    <th>{copy.admin.columns.name}</th>
                    <th>{copy.admin.columns.sessionStatus}</th>
                    <th>{copy.admin.columns.runtime}</th>
                    <th>{copy.admin.columns.dockerId}</th>
                    <th>{copy.admin.columns.created}</th>
                    <th>{copy.admin.columns.actions}</th>
                  </tr>
                </thead>
                <tbody>
                  {sessionRuntimeRows.map(({ session, primaryContainer, extraContainerCount }) => (
                    <tr key={session._id}>
                      <td data-label={copy.admin.columns.name}>
                        <div className="admin-session-name-cell">
                          <strong>{session.name}</strong>
                          <span className="mono">{session._id.slice(0, 8)}</span>
                        </div>
                      </td>
                      <td data-label={copy.admin.columns.sessionStatus}>
                        <span className={`status-badge status-${session.status}`}>{sessionStatusLabels[session.status]}</span>
                      </td>
                      <td data-label={copy.admin.columns.runtime}>
                        {primaryContainer ? (
                          <div className="admin-runtime-cell">
                            <span className={`status-badge status-${primaryContainer.status}`}>
                              {containerStatusLabels[primaryContainer.status]}
                            </span>
                            <span className="admin-runtime-image" title={primaryContainer.image}>{primaryContainer.image}</span>
                            {extraContainerCount ? (
                              <span className="admin-runtime-extra">
                                {copy.admin.extraContainers.replace("{count}", String(extraContainerCount))}
                              </span>
                            ) : null}
                          </div>
                        ) : (
                          <span className="status-badge status-idle">{copy.admin.noContainer}</span>
                        )}
                      </td>
                      <td className="mono" data-label={copy.admin.columns.dockerId}>
                        {primaryContainer ? primaryContainer.docker_id.slice(0, 12) : "-"}
                      </td>
                      <td data-label={copy.admin.columns.created}>{new Date(session.created_at).toLocaleString(locale)}</td>
                      <td data-label={copy.admin.columns.actions}>
                        <div className="admin-row-actions">
                          {primaryContainer ? (
                            <button className="btn-danger btn-sm" onClick={() => handleRemoveContainer(primaryContainer._id)}>
                              {copy.common.remove}
                            </button>
                          ) : null}
                          <button className="btn-danger btn-sm" onClick={() => handleDeleteSession(session._id)}>
                            {copy.common.deleteSession}
                          </button>
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
        </div>
      </div>
    </div>
  );
}

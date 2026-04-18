import { createContext, ReactNode, useContext, useEffect, useMemo, useState } from "react";

export type Locale = "zh-CN" | "en-US";

const STORAGE_KEY = "shotwright_locale";

const catalogs = {
  "zh-CN": {
    app: {
      product: "Shotwright",
      chat: "聊天",
      admin: "管理",
      workspace: "After Effects 操作台",
      primaryNavLabel: "主导航",
      languageLabel: "语言",
      languages: {
        "zh-CN": "中文",
        "en-US": "English",
      },
      local: "本地",
      agent: "Agent",
    },
    common: {
      notStarted: "未开始",
      notGenerated: "未生成",
      notSpecified: "未指定",
      none: "暂无",
      noDetectedAep: "未检测到 .aep 文件",
      newChat: "新聊天",
      createSession: "创建会话",
      deleteSession: "删除会话",
      uploadProject: "上传 AEP Zip",
      uploading: "上传中...",
      export: "导出",
      update: "更新",
      save: "保存配置",
      saving: "保存中...",
      saved: "已保存",
      login: "登录",
      logout: "退出登录",
      remove: "移除",
      send: "发送",
      working: "处理中...",
      emptyResponse: "（空响应）",
      ctrlEnterHint: "Ctrl/Cmd + Enter 发送",
      autoRefreshHint: "项目、容器和时间线会自动刷新",
      sessionPrefix: "会话",
      yesBoundProject: "已绑定工程",
      noProjectUploaded: "未上传工程",
      copilot: "Copilot",
      reasoningEfforts: {
        low: "低推理",
        medium: "中推理",
        high: "高推理",
        xhigh: "极高推理",
      },
    },
    status: {
      session: {
        idle: "待命",
        running: "执行中",
        awaiting_input: "等待输入",
        error: "异常",
        closed: "已关闭",
      },
      project: {
        uploaded: "已上传",
        active: "处理中",
        exported: "已导出",
      },
      container: {
        creating: "创建中",
        running: "运行中",
        stopped: "已停止",
        error: "异常",
        removed: "已移除",
      },
      token: {
        set: "已设置",
        notSet: "未设置",
      },
    },
    errors: {
      failedLoadSessions: "无法加载会话列表。",
      failedLoadSessionData: "无法加载当前会话数据。",
      failedCreateSession: "创建会话失败。",
      failedSendPrompt: "发送消息失败。",
      uploadFailed: "上传失败。",
      exportFailed: "导出失败。",
      failedStopContainer: "停止容器失败。",
      failedDeleteSession: "删除会话失败。",
      invalidPassword: "密码错误。",
      failedUpdateGithubToken: "更新 GitHub Token 失败。",
      failedUpdateAdminSettings: "更新 Copilot 配置失败。",
    },
    agent: {
      sidebarTitle: "会话",
      sidebarEmpty: "还没有任何会话。先新建一个会话。",
      eyebrow: "聊天",
      noActiveSession: "当前没有会话",
      noActiveProject: "当前没有工程",
      containerPrefix: "容器",
      title: {
        empty: "新聊天",
      },
      starterEyebrow: "Shotwright",
      starterTitle: "在一个对话里规划、检查并渲染 After Effects 工程。",
      starterDescription: "上传 AEP 压缩包，用自然语言描述你要的效果。Agent 会负责工程上下文、容器生命周期、JSX 执行和预览渲染。",
      emptyEyebrow: "当前没有会话",
      emptyTitle: "创建一个会话，开始处理 AE 工程。",
      emptyDescription: "先创建一个会话，然后上传工程压缩包，再让 agent 检查、修改、渲染。",
      textareaActive: "让 Shotwright 检查工程、修改合成、更新图层，或渲染预览...",
      textareaInactive: "先创建会话，再输入你的指令...",
      you: "你",
      assistant: "Shotwright",
      sessionPanelEyebrow: "当前会话",
      sessionPanelDescription: "这里集中展示当前选中会话的工程、运行状态和最近结果，避免左侧和右侧重复表达同一份信息。",
      sessionPanelFields: {
        status: "状态",
        activeProject: "当前工程",
        container: "容器",
        lastReply: "最近回复",
        latestRender: "最近渲染",
        lastSync: "最后同步",
        runtime: "运行时",
      },
      workflowEyebrow: "工作流",
      workflowTitle: "Shotwright 会话如何工作",
      workflowDescription: "当你选中某个会话后，右侧才显示该会话当前绑定的工程、容器状态、最近渲染和执行轨迹。",
      workflowSteps: [
        "先创建一个聊天会话。",
        "上传 AEP zip，建立当前工程上下文。",
        "让 agent 检查、修改或执行 JSX。",
        "渲染预览，并在右侧查看结果和时间线。",
      ],
      assetsEyebrow: "资源",
      assetsTitle: "已上传工程",
      assetsEmpty: "还没有上传工程文件。",
      executionEyebrow: "执行",
      executionTitle: "Agent 时间线",
      executionEmpty: "Agent 还没有执行任何工具。",
      prompts: [
        {
          title: "检查工程结构",
          description: "先检查工程里的主合成、入口时间线和关键素材组织方式。",
          prompt: "先检查这个工程的合成结构，并告诉我主时间线和入口合成在哪里。",
        },
        {
          title: "渲染一版预览",
          description: "启动容器、打开当前工程并产出一版 1080p H.264 预览。",
          prompt: "启动容器，打开我上传的工程，渲染一版 1080p H.264 预览。",
        },
        {
          title: "做一个视觉调整",
          description: "让 agent 直接修改图层样式，再导出一个新的预览版本。",
          prompt: "把主标题改成白色描边、轻微发光，并导出一个新的预览版本。",
        },
      ],
    },
    admin: {
      loginEyebrow: "控制平面",
      loginTitle: "管理员登录",
      loginCopy: "管理 GitHub Token、会话和容器资源。默认口令可以通过环境变量覆盖。",
      passwordPlaceholder: "输入管理员密码",
      headerEyebrow: "管理控制台",
      headerTitle: "运行面板",
      headerCopy: "监控会话、凭证和容器占用，保持 Shotwright 运行面干净可控。",
      stats: {
        totalSessions: "会话总数",
        activeSessions: "活跃会话",
        totalContainers: "容器总数",
        runningContainers: "运行中容器",
      },
      credentialsEyebrow: "凭证",
      credentialsTitle: "GitHub Token",
      credentialsDescription: "这个 Token 用于 Copilot SDK 与 GitHub Copilot 通信。",
      tokenStatus: "状态",
      tokenPlaceholder: "ghp_...",
      tokenHelp: "PAT 至少需要 Copilot Requests 权限；否则即使 token 可用，也会在真正发起对话时返回 401。",
      configEyebrow: "Copilot",
      configTitle: "运行配置",
      configDescription: "将模型、推理强度、工作目录、CLI 路径和代理统一收口到这里。保存后会重建活动中的 Copilot runtime。",
      configHint: "代理字段留空时会继承容器当前环境变量；如果你需要覆盖容器内的 HTTP_PROXY / HTTPS_PROXY，可以直接在这里填写。",
      useLoggedInUserHint: "没有 Token 时，允许 SDK 尝试使用 CLI 已登录用户。",
      fields: {
        model: "模型",
        reasoning: "推理强度",
        workspaceRoot: "工作目录",
        cliPath: "CLI 路径",
        useLoggedInUser: "使用已登录 CLI 用户",
        httpProxy: "HTTP 代理",
        httpsProxy: "HTTPS 代理",
        noProxy: "NO_PROXY",
      },
      placeholders: {
        inherit: "留空则继承容器环境",
      },
      sessionsEyebrow: "会话",
      sessionsTitle: "会话清单",
      runtimeEyebrow: "运行时",
      runtimeTitle: "容器清单",
      columns: {
        name: "名称",
        status: "状态",
        created: "创建时间",
        actions: "操作",
        dockerId: "Docker ID",
        session: "会话",
      },
    },
    container: {
      eyebrow: "运行时",
      title: "容器",
      runtimeLabel: "Docker 运行时",
      stop: "停止运行时",
    },
    video: {
      eyebrow: "输出",
      title: "渲染预览",
    },
  },
  "en-US": {
    app: {
      product: "Shotwright",
      chat: "Chat",
      admin: "Admin",
      workspace: "After Effects Operator Workspace",
      primaryNavLabel: "Primary navigation",
      languageLabel: "Language",
      languages: {
        "zh-CN": "中文",
        "en-US": "English",
      },
      local: "Local",
      agent: "Agent",
    },
    common: {
      notStarted: "Not started",
      notGenerated: "Not generated",
      notSpecified: "Not specified",
      none: "None",
      noDetectedAep: "No .aep files detected",
      newChat: "New Chat",
      createSession: "Create Session",
      deleteSession: "Delete Session",
      uploadProject: "Upload AEP Zip",
      uploading: "Uploading...",
      export: "Export",
      update: "Update",
      save: "Save settings",
      saving: "Saving...",
      saved: "Saved",
      login: "Login",
      logout: "Logout",
      remove: "Remove",
      send: "Send",
      working: "Working...",
      emptyResponse: "(empty response)",
      ctrlEnterHint: "Ctrl/Cmd + Enter to send",
      autoRefreshHint: "Project, runtime, and timeline refresh automatically",
      sessionPrefix: "Session",
      yesBoundProject: "Project linked",
      noProjectUploaded: "No project uploaded",
      copilot: "Copilot",
      reasoningEfforts: {
        low: "Low reasoning",
        medium: "Medium reasoning",
        high: "High reasoning",
        xhigh: "Extreme reasoning",
      },
    },
    status: {
      session: {
        idle: "Idle",
        running: "Running",
        awaiting_input: "Awaiting input",
        error: "Error",
        closed: "Closed",
      },
      project: {
        uploaded: "Uploaded",
        active: "Active",
        exported: "Exported",
      },
      container: {
        creating: "Creating",
        running: "Running",
        stopped: "Stopped",
        error: "Error",
        removed: "Removed",
      },
      token: {
        set: "Set",
        notSet: "Not set",
      },
    },
    errors: {
      failedLoadSessions: "Failed to load sessions.",
      failedLoadSessionData: "Failed to load session data.",
      failedCreateSession: "Failed to create a new session.",
      failedSendPrompt: "Failed to send prompt to the Copilot agent.",
      uploadFailed: "Upload failed.",
      exportFailed: "Export failed.",
      failedStopContainer: "Failed to stop container.",
      failedDeleteSession: "Failed to delete session.",
      invalidPassword: "Invalid password.",
      failedUpdateGithubToken: "Failed to update the GitHub token.",
      failedUpdateAdminSettings: "Failed to update Copilot settings.",
    },
    agent: {
      sidebarTitle: "Sessions",
      sidebarEmpty: "No sessions yet. Create one to get started.",
      eyebrow: "Chat",
      noActiveSession: "No active session",
      noActiveProject: "No active project",
      containerPrefix: "Container",
      title: {
        empty: "New Chat",
      },
      starterEyebrow: "Shotwright",
      starterTitle: "Plan, inspect, and render your After Effects project in one chat.",
      starterDescription: "Upload an AEP zip and describe what you want in natural language. The agent handles project context, runtime lifecycle, JSX execution, and preview rendering.",
      emptyEyebrow: "No active session",
      emptyTitle: "Create a chat to start working on an AE project.",
      emptyDescription: "Create a session first, then upload a project archive and ask the agent to inspect, change, or render it.",
      textareaActive: "Ask Shotwright to inspect the project, change comps, update layers, or render a preview...",
      textareaInactive: "Create a session first, then type your instruction here...",
      you: "You",
      assistant: "Shotwright",
      sessionPanelEyebrow: "Current session",
      sessionPanelDescription: "This panel is now the single place for the selected session's project, runtime state, and latest results, instead of repeating the same summary in multiple places.",
      sessionPanelFields: {
        status: "Status",
        activeProject: "Active project",
        container: "Container",
        lastReply: "Last reply",
        latestRender: "Latest render",
        lastSync: "Last sync",
        runtime: "Runtime",
      },
      workflowEyebrow: "Workflow",
      workflowTitle: "How a Shotwright session works",
      workflowDescription: "The right sidebar only appears as a live status panel for the currently selected session, including project binding, runtime state, latest render, and execution trace.",
      workflowSteps: [
        "Create a chat session.",
        "Upload an AEP zip to establish the current project context.",
        "Ask the agent to inspect, modify, or run JSX.",
        "Render a preview and review the result and timeline on the right.",
      ],
      assetsEyebrow: "Assets",
      assetsTitle: "Uploaded projects",
      assetsEmpty: "No project files have been uploaded yet.",
      executionEyebrow: "Execution",
      executionTitle: "Agent timeline",
      executionEmpty: "The agent has not run any tools yet.",
      prompts: [
        {
          title: "Inspect the project structure",
          description: "Review the main compositions, entry timeline, and source asset layout first.",
          prompt: "Inspect this project structure and tell me where the main timeline and entry composition are.",
        },
        {
          title: "Render a preview",
          description: "Start the runtime, open the current project, and produce a 1080p H.264 preview.",
          prompt: "Start the runtime, open the uploaded project, and render a 1080p H.264 preview.",
        },
        {
          title: "Apply a visual tweak",
          description: "Have the agent modify the layer styling directly, then export a fresh preview.",
          prompt: "Change the main title to a white stroke with a subtle glow and export a new preview version.",
        },
      ],
    },
    admin: {
      loginEyebrow: "Control plane",
      loginTitle: "Admin access",
      loginCopy: "Manage the GitHub Token, sessions, and runtime resources. The default password can be overridden with an environment variable.",
      passwordPlaceholder: "Enter admin password",
      headerEyebrow: "Admin console",
      headerTitle: "Operator dashboard",
      headerCopy: "Monitor sessions, credentials, and runtime usage so the Shotwright control plane stays clean and predictable.",
      stats: {
        totalSessions: "Total Sessions",
        activeSessions: "Active Sessions",
        totalContainers: "Total Containers",
        runningContainers: "Running Containers",
      },
      credentialsEyebrow: "Credentials",
      credentialsTitle: "GitHub Token",
      credentialsDescription: "This token is used by the Copilot SDK when it talks to GitHub Copilot.",
      tokenStatus: "Status",
      tokenPlaceholder: "ghp_...",
      tokenHelp: "The PAT must include the Copilot Requests permission. Otherwise token auth may look valid but real chat requests still fail with 401.",
      configEyebrow: "Copilot",
      configTitle: "Runtime settings",
      configDescription: "Control the model, reasoning effort, workspace root, CLI path, and proxy settings from one place. Saving reconnects active Copilot runtimes.",
      configHint: "Leave proxy fields empty to inherit the container environment. Fill them in here only when you need to override HTTP_PROXY / HTTPS_PROXY inside the SDK subprocess.",
      useLoggedInUserHint: "Allow the SDK to fall back to an already logged-in CLI user when no token is set.",
      fields: {
        model: "Model",
        reasoning: "Reasoning effort",
        workspaceRoot: "Workspace root",
        cliPath: "CLI path",
        useLoggedInUser: "Use logged-in CLI user",
        httpProxy: "HTTP proxy",
        httpsProxy: "HTTPS proxy",
        noProxy: "NO_PROXY",
      },
      placeholders: {
        inherit: "Leave empty to inherit container env",
      },
      sessionsEyebrow: "Sessions",
      sessionsTitle: "Session inventory",
      runtimeEyebrow: "Runtime",
      runtimeTitle: "Container inventory",
      columns: {
        name: "Name",
        status: "Status",
        created: "Created",
        actions: "Actions",
        dockerId: "Docker ID",
        session: "Session",
      },
    },
    container: {
      eyebrow: "Runtime",
      title: "Containers",
      runtimeLabel: "Docker runtime",
      stop: "Stop runtime",
    },
    video: {
      eyebrow: "Output",
      title: "Render preview",
    },
  },
} as const;

export type TranslationCopy = (typeof catalogs)[Locale];

interface I18nContextValue {
  locale: Locale;
  setLocale: (locale: Locale) => void;
  copy: TranslationCopy;
}

const I18nContext = createContext<I18nContextValue | null>(null);

function resolveInitialLocale(): Locale {
  if (typeof window === "undefined") return "zh-CN";

  const stored = window.localStorage.getItem(STORAGE_KEY);
  if (stored === "zh-CN" || stored === "en-US") return stored;

  return window.navigator.language.toLowerCase().startsWith("zh") ? "zh-CN" : "en-US";
}

export function I18nProvider({ children }: { children: ReactNode }) {
  const [locale, setLocale] = useState<Locale>(resolveInitialLocale);

  useEffect(() => {
    window.localStorage.setItem(STORAGE_KEY, locale);
  }, [locale]);

  const value = useMemo(
    () => ({
      locale,
      setLocale,
      copy: catalogs[locale] as TranslationCopy,
    }),
    [locale]
  );

  return <I18nContext.Provider value={value}>{children}</I18nContext.Provider>;
}

export function useI18n() {
  const context = useContext(I18nContext);
  if (!context) {
    throw new Error("useI18n must be used within an I18nProvider");
  }

  return context;
}
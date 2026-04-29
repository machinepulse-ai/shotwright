# Shotwright Platform

前后端分离的 Copilot Agent 运行时平台，通过 Windows 容器访问 After Effects。

验证渲染的 GIF 预览与 `validation.mp4` 产物说明见 [../README.md](../README.md) 和 [../README-cn.md](../README-cn.md)，本文件只覆盖 `src/` 下的平台层内容。

## 架构

```
src/
├── backend/          Python (uv + FastAPI + Motor)
│   ├── app/
│   │   ├── main.py           FastAPI 入口
│   │   ├── config.py         环境变量配置 (pydantic-settings)
│   │   ├── database.py       MongoDB 连接 (含 cache/session 抽象层)
│   │   ├── models/           Pydantic 数据模型
│   │   ├── routers/          API 路由
│   │   ├── services/         业务逻辑
│   │   └── middleware/       认证中间件
│   ├── codex-bridge/         Node.js bridge for @openai/codex-sdk
│   └── pyproject.toml
├── frontend/         React 18 + TypeScript + Webpack 5
│   ├── src/
│   │   ├── components/
│   │   │   ├── AgentPanel/   Agent 会话面板
│   │   │   ├── AdminPanel/   后台管理面板
│   │   │   ├── VideoPlayer/  HLS 视频播放器
│   │   │   └── ContainerManager/  容器管理
│   │   ├── services/api.ts   API 客户端
│   │   └── types/            TypeScript 类型
│   └── package.json
├── docker-compose.yml        生产部署
├── docker-compose.dev.yml    开发模式 (热重载)
├── .env.example
└── scripts/
    ├── deploy.ps1            一键部署
    ├── cleanup.ps1           垃圾清理
    └── dev.ps1               本地开发 (无 Docker)

# 根目录 Dockerfile 提供多阶段构建:
#   base       → 共用工具链 (choco + python + nodejs + ffmpeg)
#   shotwright → AE 运行时 (nexrender, aerender)
#   backend    → FastAPI API 服务
#   frontend   → 静态文件服务 (serve)
```

## 快速开始

### Docker Compose 一键部署

```powershell
cd src
copy .env.example .env
# 编辑 .env 设置 SHOTWRIGHT_SECRET_KEY 和 SHOTWRIGHT_ADMIN_PASSWORD

# 构建并启动
.\scripts\deploy.ps1 -Build -Detach

# 开发模式 (热重载)
.\scripts\deploy.ps1 -Dev -Build
```

### 本地开发 (无 Docker)

需要本地 MongoDB 运行在 `localhost:27017`。

```powershell
# 后端
cd src/backend
uv sync
uv run uvicorn app.main:app --reload --port 8000

# 前端
cd src/frontend
npm install
npm run dev
```

或使用一键脚本:

```powershell
.\src\scripts\dev.ps1
```

### 访问地址

| 服务 | 地址 |
|------|------|
| Frontend | http://localhost:3000 |
| Backend API | http://localhost:8000/api |
| API Docs (Swagger) | http://localhost:8000/api/docs |

## 功能

### Agent 面板
- 创建/管理 Agent 会话
- 以聊天方式向 Copilot agent 下达创意目标
- 展示 agent 的工具调用时间线与会话状态
- 上传 AEP 工程压缩包供 agent 选择和操作
- 由 agent 通过 backend custom tools 自主启动/复用 shotwright Windows 容器
- 由 agent 通过 JSX 和 nexrender 控制 After Effects
- HLS (m3u8) 流式视频预览
- 导出 agent 修改后的工程压缩包

### 后台管理
- 管理员密码登录
- GitHub Token 配置 (Copilot SDK，更新后会重建 runtime)
- 会话数据管理
- 容器实例管理
- 仪表盘统计

### Codex Bridge
- `src/backend/codex-bridge/bridge.mjs` 是同容器内的 Node bridge，Python 后端通过 JSONL stdin/stdout 调用它。
- bridge 使用 `@openai/codex-sdk` 的 streamed thread API，保留 `thread_id`、最终回复、usage 和中间事件。
- 后端 Python client 在 `app/services/codex_bridge.py`，Codex runtime provider 在 `app/services/codex_runtime.py`。
- Admin 页可以在 Copilot 和 Codex Bridge 之间切换全局 agent provider，并分别保存 GitHub Token 与 OpenAI API Key。
- Codex 默认值会优先参考容器可见的 `.codex/config.toml` 与 `.codex/auth.json`（可通过 `SHOTWRIGHT_CODEX_HOME` 指定），再回落到环境变量和应用默认值；API 响应只返回 Key 是否已设置，不返回密钥内容。
- 容器构建会在 backend 镜像内安装 `src/backend/codex-bridge/package-lock.json` 锁定的 Node 依赖。

## 升级路径

### MongoDB -> Redis + PostgreSQL
`database.py` 中的 `get_cache_collection()` 和 `get_session_collection()` 提供了抽象层:
- cache 操作 → 替换为 Redis 客户端
- session/container 数据 → 迁移到 PostgreSQL

### Docker Compose -> Kubernetes
- `docker-compose.yml` 中所有服务已标注 `app.kubernetes.io/*` labels
- 每个 service 对应一个 K8s Deployment + Service
- `mongo` 应升级为 StatefulSet + PVC
- named volumes 对应 PersistentVolumeClaim
- 环境变量迁移到 ConfigMap / Secret

## 垃圾清理

```powershell
# 停止服务 + 清除容器
.\scripts\cleanup.ps1

# 包括清除数据卷 (会删除 MongoDB 数据!)
.\scripts\cleanup.ps1 -All

# 完整 Docker 系统清理
.\scripts\cleanup.ps1 -Prune
```

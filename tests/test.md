# UI 测试工作流

Shotwright 的 UI 回归测试统一放在 `tests/ui/`。不要再在仓库根目录或 `src/frontend/` 下新增临时 `tmp-playwright-*.js` 脚本，也不要把调试截图写进 `validation-data/output/`。

## 运行前提

- 前后端都应先在 dev container 中启动。
- Playwright 脚本从 Windows 主机执行，默认优先使用系统 Edge；如果没有 Edge，会回退到 Playwright 自带 Chromium。
- 如果前端不是默认端口，请显式设置 `SHOTWRIGHT_BASE_URL`。

## 常用命令

在仓库根目录执行：

```powershell
npm --prefix src/frontend run ui:test:session
```

这个脚本会：

- 用固定 mock 数据渲染 session 页
- 校验 session 级模型选择和推理强度切换
- 校验右侧 sidebar 在长错误文本、长 runtime id、长时间线摘要下不会横向溢出

如需检查“新建会话后事件流没有接上”的真实页面问题：

```powershell
npm --prefix src/frontend run ui:probe:session-stream
```

这个探针会：

- 在真实前端页面里点击创建会话
- 记录 session stream 的 fetch/EventSource 建连情况
- 通过后端 API 改名新建会话，验证页面是否能在不刷新时收到 `session.updated`

如需抓当前页面截图：

```powershell
$env:SHOTWRIGHT_UI_CAPTURE = "1"
npm --prefix src/frontend run ui:capture
```

可选环境变量：

- `SHOTWRIGHT_BASE_URL`: 指向运行中的前端地址，例如 `http://127.0.0.1:3100`
- `SHOTWRIGHT_BROWSER_PATH`: 指定浏览器可执行文件路径
- `SHOTWRIGHT_UI_CAPTURE`: 设为 `1` 后，测试脚本会额外保存截图
- `SHOTWRIGHT_CAPTURE_PATH`: 自定义 `ui:capture` 的截图输出路径

## 脚本说明

- `tests/ui/playwright_shared.js`: Playwright 启动、URL 探测、截图保存的公共逻辑
- `tests/ui/capture_app.js`: 通用页面截图入口，适合快速看当前 UI
- `tests/ui/session_page_regression.js`: session 页回归脚本，重点覆盖右侧栏和会话级 Copilot 配置
- `tests/ui/session_creation_stream_probe.js`: 真实页面会话创建与事件流探针，重点覆盖“新建会话后 stream 未接上”的时序问题

## 清理约定

- 产物只允许写到 `tests/artifacts/`，该目录已加入忽略规则。
- `tests/artifacts/session-page-regression.failure.png` 仅在失败时生成，定位完问题后可直接删除。
- 如果需要新增 UI 回归脚本，优先复用 `playwright_shared.js`，不要复制旧脚本再改路径。
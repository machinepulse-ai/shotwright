import { useState } from "react";
import { BrowserRouter, NavLink, Route, Routes, useLocation } from "react-router-dom";
import AgentPanel from "./components/AgentPanel/AgentPanel";
import AdminPanel from "./components/AdminPanel/AdminPanel";
import { useI18n } from "./i18n";

function WorkbenchApp() {
  const location = useLocation();
  const { locale, setLocale, copy } = useI18n();
  const [isSessionSidebarCollapsed, setIsSessionSidebarCollapsed] = useState(false);
  const [isContextSidebarCollapsed, setIsContextSidebarCollapsed] = useState(false);
  const isAdminSection = location.pathname.startsWith("/admin");
  const currentSection = isAdminSection ? copy.app.admin : copy.app.chat;
  const showWorkbenchControls = !isAdminSection;

  return (
    <div className="workbench">
      <header className="titlebar">
        <div className="titlebar-left">
          <div className="titlebar-brand-mark" aria-hidden="true">
            <img className="titlebar-brand-icon" src="/sw-icon.svg" alt="" />
          </div>
          <span className="titlebar-product">{copy.app.product}</span>
          <nav className="titlebar-nav" aria-label={copy.app.primaryNavLabel}>
            <NavLink to="/" className={() => `titlebar-tab${!isAdminSection ? " active" : ""}`}>
              {copy.app.chat}
            </NavLink>
            <NavLink to="/admin" className={() => `titlebar-tab${isAdminSection ? " active" : ""}`}>
              {copy.app.admin}
            </NavLink>
          </nav>
        </div>
        <div className="titlebar-center">{copy.app.workspace}</div>
        <div className="titlebar-right">
          {showWorkbenchControls ? (
            <div className="titlebar-workbench-controls" aria-label={copy.app.layoutControlsLabel}>
              <button
                type="button"
                className={`titlebar-pane-toggle${isSessionSidebarCollapsed ? "" : " is-active"}`}
                data-testid="toggle-session-sidebar"
                aria-pressed={!isSessionSidebarCollapsed}
                aria-label={isSessionSidebarCollapsed ? copy.agent.showSessions : copy.agent.hideSessions}
                title={isSessionSidebarCollapsed ? copy.agent.showSessions : copy.agent.hideSessions}
                onClick={() => setIsSessionSidebarCollapsed((previous) => !previous)}
              >
                <span className={`titlebar-pane-icon layout-sessions${isSessionSidebarCollapsed ? " is-collapsed" : ""}`} aria-hidden="true" />
                <span className="titlebar-pane-toggle-text">{copy.app.sessionsShortLabel}</span>
              </button>
              <button
                type="button"
                className={`titlebar-pane-toggle${isContextSidebarCollapsed ? "" : " is-active"}`}
                data-testid="toggle-context-sidebar"
                aria-pressed={!isContextSidebarCollapsed}
                aria-label={isContextSidebarCollapsed ? copy.agent.showDetails : copy.agent.hideDetails}
                title={isContextSidebarCollapsed ? copy.agent.showDetails : copy.agent.hideDetails}
                onClick={() => setIsContextSidebarCollapsed((previous) => !previous)}
              >
                <span className={`titlebar-pane-icon layout-details${isContextSidebarCollapsed ? " is-collapsed" : ""}`} aria-hidden="true" />
                <span className="titlebar-pane-toggle-text">{copy.app.detailsShortLabel}</span>
              </button>
            </div>
          ) : null}
          <label className="titlebar-language" aria-label={copy.app.languageLabel}>
            <select value={locale} onChange={(event) => setLocale(event.target.value as typeof locale)}>
              <option value="zh-CN">{copy.app.languages["zh-CN"]}</option>
              <option value="en-US">{copy.app.languages["en-US"]}</option>
            </select>
          </label>
        </div>
      </header>

      <div className="workbench-body">
        <main className="app-main">
          <Routes>
            <Route
              path="/"
              element={
                <AgentPanel
                  isSessionSidebarCollapsed={isSessionSidebarCollapsed}
                  isContextSidebarCollapsed={isContextSidebarCollapsed}
                />
              }
            />
            <Route
              path="/sessions/:sessionId"
              element={
                <AgentPanel
                  isSessionSidebarCollapsed={isSessionSidebarCollapsed}
                  isContextSidebarCollapsed={isContextSidebarCollapsed}
                />
              }
            />
            <Route path="/admin" element={<AdminPanel />} />
          </Routes>
        </main>
      </div>

      <footer className="statusbar">
        <div className="statusbar-group">
          <span>main*</span>
          <span>{copy.app.local}</span>
          <span>{copy.app.product}</span>
        </div>
        <div className="statusbar-group">
          <span>{copy.app.agent}</span>
          <span>{copy.common.copilot}</span>
          <span>{currentSection}</span>
        </div>
      </footer>
    </div>
  );
}

export default function App() {
  return (
    <BrowserRouter>
      <WorkbenchApp />
    </BrowserRouter>
  );
}

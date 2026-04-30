import { useEffect, useState } from "react";
import { BrowserRouter, NavLink, Route, Routes, useLocation } from "react-router-dom";
import AgentPanel from "./components/AgentPanel/AgentPanel";
import AdminPanel from "./components/AdminPanel/AdminPanel";
import { useI18n } from "./i18n";

const MOBILE_DRAWER_QUERY = "(max-width: 820px)";
const NARROW_CONTEXT_QUERY = "(max-width: 1100px)";

function mediaQueryMatches(query: string) {
  return typeof window !== "undefined" ? window.matchMedia(query).matches : false;
}

function WorkbenchApp() {
  const location = useLocation();
  const { locale, setLocale, copy } = useI18n();
  const [isMobileDrawerLayout, setIsMobileDrawerLayout] = useState(() => mediaQueryMatches(MOBILE_DRAWER_QUERY));
  const [isNarrowContextLayout, setIsNarrowContextLayout] = useState(() => mediaQueryMatches(NARROW_CONTEXT_QUERY));
  const [isSessionSidebarCollapsed, setIsSessionSidebarCollapsed] = useState(() => mediaQueryMatches(MOBILE_DRAWER_QUERY));
  const [isContextSidebarCollapsed, setIsContextSidebarCollapsed] = useState(() => mediaQueryMatches(NARROW_CONTEXT_QUERY));
  const isAdminSection = location.pathname.startsWith("/admin");
  const showWorkbenchControls = !isAdminSection;

  useEffect(() => {
    const root = document.documentElement;
    let frameId = 0;

    const syncVisualViewport = () => {
      window.cancelAnimationFrame(frameId);
      frameId = window.requestAnimationFrame(() => {
        const visualViewport = window.visualViewport;
        const keyboardInset = visualViewport
          ? Math.max(0, window.innerHeight - visualViewport.height - visualViewport.offsetTop)
          : 0;

        root.style.setProperty("--app-keyboard-inset-bottom", `${Math.round(keyboardInset)}px`);
        root.classList.toggle("is-visual-keyboard-open", keyboardInset > 80);
      });
    };

    syncVisualViewport();
    window.addEventListener("resize", syncVisualViewport);
    window.addEventListener("orientationchange", syncVisualViewport);
    window.visualViewport?.addEventListener("resize", syncVisualViewport);
    window.visualViewport?.addEventListener("scroll", syncVisualViewport);

    return () => {
      window.cancelAnimationFrame(frameId);
      window.removeEventListener("resize", syncVisualViewport);
      window.removeEventListener("orientationchange", syncVisualViewport);
      window.visualViewport?.removeEventListener("resize", syncVisualViewport);
      window.visualViewport?.removeEventListener("scroll", syncVisualViewport);
      root.style.removeProperty("--app-keyboard-inset-bottom");
      root.classList.remove("is-visual-keyboard-open");
    };
  }, []);

  useEffect(() => {
    const mobileQuery = window.matchMedia(MOBILE_DRAWER_QUERY);
    const narrowContextQuery = window.matchMedia(NARROW_CONTEXT_QUERY);

    const syncLayoutMode = () => {
      const nextMobileLayout = mobileQuery.matches;
      const nextNarrowContextLayout = narrowContextQuery.matches;

      setIsMobileDrawerLayout(nextMobileLayout);
      setIsNarrowContextLayout(nextNarrowContextLayout);

      if (nextMobileLayout) {
        setIsSessionSidebarCollapsed(true);
        setIsContextSidebarCollapsed(true);
        return;
      }

      if (nextNarrowContextLayout) {
        setIsContextSidebarCollapsed(true);
      }
    };

    syncLayoutMode();
    mobileQuery.addEventListener("change", syncLayoutMode);
    narrowContextQuery.addEventListener("change", syncLayoutMode);
    return () => {
      mobileQuery.removeEventListener("change", syncLayoutMode);
      narrowContextQuery.removeEventListener("change", syncLayoutMode);
    };
  }, []);

  const toggleSessionSidebar = () => {
    const nextCollapsed = !isSessionSidebarCollapsed;
    setIsSessionSidebarCollapsed(nextCollapsed);
    if (!nextCollapsed && isMobileDrawerLayout) {
      setIsContextSidebarCollapsed(true);
    }
  };

  const toggleContextSidebar = () => {
    const nextCollapsed = !isContextSidebarCollapsed;
    setIsContextSidebarCollapsed(nextCollapsed);
    if (!nextCollapsed && (isMobileDrawerLayout || isNarrowContextLayout)) {
      setIsSessionSidebarCollapsed(true);
    }
  };

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
                onClick={toggleSessionSidebar}
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
                onClick={toggleContextSidebar}
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
                  onRequestCloseSessionSidebar={isMobileDrawerLayout ? () => setIsSessionSidebarCollapsed(true) : undefined}
                />
              }
            />
            <Route
              path="/sessions/:sessionId"
              element={
                <AgentPanel
                  isSessionSidebarCollapsed={isSessionSidebarCollapsed}
                  isContextSidebarCollapsed={isContextSidebarCollapsed}
                  onRequestCloseSessionSidebar={isMobileDrawerLayout ? () => setIsSessionSidebarCollapsed(true) : undefined}
                />
              }
            />
            <Route path="/admin" element={<AdminPanel />} />
          </Routes>
        </main>
      </div>

      <footer className="statusbar" aria-label={copy.app.statusbarLabel}>
        <div className="statusbar-group statusbar-group-left">
          <span className="statusbar-item statusbar-item-branch" title={copy.app.gitBranchTitle}>
            <span className="statusbar-icon statusbar-icon-branch" aria-hidden="true" />
            <span>main*</span>
          </span>
          <span className="statusbar-item" title={copy.app.localWorkspace}>
            <span className="statusbar-dot is-online" aria-hidden="true" />
            <span>{copy.app.localWorkspace}</span>
          </span>
        </div>
        <div className="statusbar-group statusbar-group-right">
          <span className="statusbar-item statusbar-item-subtle">{copy.app.uiEndpoint}</span>
          <span className="statusbar-item statusbar-item-subtle">{copy.app.apiEndpoint}</span>
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

import { useEffect, useState } from "react";
import { BrowserRouter, NavLink, Route, Routes, useLocation } from "react-router-dom";
import AgentPanel from "./components/AgentPanel/AgentPanel";
import AdminPanel from "./components/AdminPanel/AdminPanel";
import { useI18n } from "./i18n";

const MOBILE_DRAWER_QUERY = "(max-width: 820px)";
const NARROW_CONTEXT_QUERY = "(max-width: 1100px)";
const KEYBOARD_INSET_THRESHOLD = 80;
const MAX_KEYBOARD_INSET_RATIO = 0.52;
const KEYBOARD_STABLE_FRAME_COUNT = 12;
const THEME_STORAGE_KEY = "shotwright_theme";

type ColorTheme = "light" | "dark";
type VisualKeyboardState = {
  keyboardInset: number;
  composerBottom: number;
  fixedOffsetY: number;
};

function mediaQueryMatches(query: string) {
  return typeof window !== "undefined" ? window.matchMedia(query).matches : false;
}

function getInitialColorTheme(): ColorTheme {
  if (typeof window === "undefined") return "light";

  try {
    const storedTheme = window.localStorage.getItem(THEME_STORAGE_KEY);
    if (storedTheme === "light" || storedTheme === "dark") {
      return storedTheme;
    }
  } catch {
    return "light";
  }

  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

function isAppleTouchViewport() {
  if (typeof navigator === "undefined") return false;
  const userAgent = navigator.userAgent.toLowerCase();
  return /iphone|ipad|ipod/.test(userAgent) || (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);
}

function isKeyboardTextTarget(element: Element | null) {
  if (!(element instanceof HTMLElement)) return false;
  if (element.isContentEditable) return true;

  const tagName = element.tagName.toLowerCase();
  if (tagName === "textarea" || tagName === "select") return true;
  if (tagName !== "input") return false;

  const inputType = ((element as HTMLInputElement).type || "text").toLowerCase();
  return !["button", "checkbox", "color", "file", "hidden", "image", "radio", "range", "reset", "submit"].includes(inputType);
}

function getVisualKeyboardState(layoutViewportHeight: number): VisualKeyboardState {
  const visualViewport = window.visualViewport;
  if (!visualViewport || !isKeyboardTextTarget(document.activeElement)) {
    return { keyboardInset: 0, composerBottom: 0, fixedOffsetY: 0 };
  }

  const viewportHeight = visualViewport.height;
  const rootHeight = document.documentElement.clientHeight || 0;
  const innerHeight = window.innerHeight || 0;
  const layoutHeight = Math.max(viewportHeight, layoutViewportHeight, rootHeight, innerHeight);
  const visualBottom = Math.min(layoutHeight, Math.max(0, visualViewport.offsetTop) + viewportHeight);
  const rawInset = Math.max(0, layoutHeight - visualBottom);
  const maxInset = Math.round(layoutHeight * MAX_KEYBOARD_INSET_RATIO);
  const keyboardInset = Math.min(rawInset, maxInset);
  const resolvedKeyboardInset = keyboardInset > KEYBOARD_INSET_THRESHOLD ? Math.round(keyboardInset) : 0;
  const fixedOffsetY = resolvedKeyboardInset > 0 ? Math.round(visualBottom - layoutHeight) : 0;
  const useFixedOffset = isAppleTouchViewport();

  return {
    keyboardInset: resolvedKeyboardInset,
    composerBottom: resolvedKeyboardInset > 0 && !useFixedOffset ? resolvedKeyboardInset : 0,
    fixedOffsetY: useFixedOffset ? fixedOffsetY : 0,
  };
}

function WorkbenchApp() {
  const location = useLocation();
  const { locale, setLocale, copy } = useI18n();
  const [isMobileDrawerLayout, setIsMobileDrawerLayout] = useState(() => mediaQueryMatches(MOBILE_DRAWER_QUERY));
  const [isNarrowContextLayout, setIsNarrowContextLayout] = useState(() => mediaQueryMatches(NARROW_CONTEXT_QUERY));
  const [isSessionSidebarCollapsed, setIsSessionSidebarCollapsed] = useState(() => mediaQueryMatches(MOBILE_DRAWER_QUERY));
  const [isContextSidebarCollapsed, setIsContextSidebarCollapsed] = useState(() => mediaQueryMatches(NARROW_CONTEXT_QUERY));
  const [colorTheme, setColorTheme] = useState<ColorTheme>(() => getInitialColorTheme());
  const isAdminSection = location.pathname.startsWith("/admin");
  const showWorkbenchControls = !isAdminSection;

  useEffect(() => {
    const root = document.documentElement;
    root.dataset.theme = colorTheme;
    root.style.colorScheme = colorTheme;
    try {
      window.localStorage.setItem(THEME_STORAGE_KEY, colorTheme);
    } catch {
      // Ignore storage failures; the in-memory theme still applies.
    }
  }, [colorTheme]);

  useEffect(() => {
    const root = document.documentElement;
    let frameId = 0;
    let layoutViewportHeight = window.innerHeight || document.documentElement.clientHeight || 0;
    let layoutViewportWidth = window.innerWidth || document.documentElement.clientWidth || 0;
    let lastKeyboardSignature = "";
    let stableKeyboardFrames = 0;
    root.classList.toggle("is-apple-touch-viewport", isAppleTouchViewport());

    const applyKeyboardState = (keyboardState: VisualKeyboardState) => {
      root.style.setProperty("--app-keyboard-inset-bottom", `${keyboardState.keyboardInset}px`);
      root.style.setProperty("--app-keyboard-composer-bottom", `${keyboardState.composerBottom}px`);
      root.style.setProperty("--app-keyboard-fixed-offset-y", `${keyboardState.fixedOffsetY}px`);
      root.classList.toggle("is-visual-keyboard-open", keyboardState.keyboardInset > 0);
    };

    const resetKeyboardSettle = () => {
      lastKeyboardSignature = "";
      stableKeyboardFrames = 0;
    };

    const getKeyboardViewportSignature = () => {
      const viewport = window.visualViewport;
      return [
        Math.round(viewport?.width ?? window.innerWidth),
        Math.round(viewport?.height ?? window.innerHeight),
        Math.round(viewport?.offsetTop ?? 0),
        Math.round(window.innerHeight || document.documentElement.clientHeight || 0),
      ].join(":");
    };

    const runVisualViewportSync = () => {
      frameId = 0;
      const currentWidth = window.innerWidth || document.documentElement.clientWidth || layoutViewportWidth;
      const currentHeight = window.innerHeight || document.documentElement.clientHeight || layoutViewportHeight;
      if (!isKeyboardTextTarget(document.activeElement) || currentWidth !== layoutViewportWidth) {
        layoutViewportHeight = currentHeight;
        layoutViewportWidth = currentWidth;
      }

      const keyboardState = getVisualKeyboardState(layoutViewportHeight);
      if (keyboardState.keyboardInset <= 0) {
        resetKeyboardSettle();
        applyKeyboardState(keyboardState);
        return;
      }

      const keyboardSignature = getKeyboardViewportSignature();
      if (keyboardSignature === lastKeyboardSignature) {
        stableKeyboardFrames += 1;
      } else {
        lastKeyboardSignature = keyboardSignature;
        stableKeyboardFrames = 1;
      }

      if (stableKeyboardFrames < KEYBOARD_STABLE_FRAME_COUNT) {
        frameId = window.requestAnimationFrame(runVisualViewportSync);
        return;
      }

      applyKeyboardState(keyboardState);
    };

    const syncVisualViewport = () => {
      if (frameId) {
        window.cancelAnimationFrame(frameId);
      }
      frameId = window.requestAnimationFrame(runVisualViewportSync);
    };

    syncVisualViewport();
    window.addEventListener("focusin", syncVisualViewport);
    window.addEventListener("focusout", syncVisualViewport);
    window.addEventListener("resize", syncVisualViewport);
    window.addEventListener("orientationchange", syncVisualViewport);
    window.visualViewport?.addEventListener("resize", syncVisualViewport);
    window.visualViewport?.addEventListener("scroll", syncVisualViewport);

    return () => {
      window.cancelAnimationFrame(frameId);
      window.removeEventListener("focusin", syncVisualViewport);
      window.removeEventListener("focusout", syncVisualViewport);
      window.removeEventListener("resize", syncVisualViewport);
      window.removeEventListener("orientationchange", syncVisualViewport);
      window.visualViewport?.removeEventListener("resize", syncVisualViewport);
      window.visualViewport?.removeEventListener("scroll", syncVisualViewport);
      root.style.removeProperty("--app-keyboard-inset-bottom");
      root.style.removeProperty("--app-keyboard-composer-bottom");
      root.style.removeProperty("--app-keyboard-fixed-offset-y");
      root.classList.remove("is-visual-keyboard-open");
      root.classList.remove("is-apple-touch-viewport");
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

  const toggleColorTheme = () => {
    setColorTheme((currentTheme) => (currentTheme === "dark" ? "light" : "dark"));
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
          <button
            type="button"
            className={`titlebar-theme-toggle is-${colorTheme}`}
            data-testid="toggle-color-theme"
            aria-pressed={colorTheme === "dark"}
            aria-label={colorTheme === "dark" ? copy.app.lightModeLabel : copy.app.darkModeLabel}
            title={colorTheme === "dark" ? copy.app.lightModeLabel : copy.app.darkModeLabel}
            onClick={toggleColorTheme}
          >
            <span className="theme-toggle-icon" aria-hidden="true" />
            <span className="theme-toggle-text">{colorTheme === "dark" ? copy.app.darkModeShortLabel : copy.app.lightModeShortLabel}</span>
          </button>
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
                  onRequestCloseContextSidebar={isMobileDrawerLayout || isNarrowContextLayout ? () => setIsContextSidebarCollapsed(true) : undefined}
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
                  onRequestCloseContextSidebar={isMobileDrawerLayout || isNarrowContextLayout ? () => setIsContextSidebarCollapsed(true) : undefined}
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

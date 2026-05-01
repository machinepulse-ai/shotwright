import { useEffect, useRef, useState } from "react";
import { BrowserRouter, NavLink, Route, Routes, useLocation } from "react-router-dom";
import AgentPanel from "./components/AgentPanel/AgentPanel";
import AdminPanel from "./components/AdminPanel/AdminPanel";
import { useI18n } from "./i18n";

const MOBILE_DRAWER_QUERY = "(max-width: 820px)";
const NARROW_CONTEXT_QUERY = "(max-width: 1100px)";
const KEYBOARD_INSET_THRESHOLD = 80;
const MAX_KEYBOARD_INSET_RATIO = 0.52;
const IOS_MAX_KEYBOARD_INSET_RATIO = 0.49;
const KEYBOARD_STABLE_FRAME_COUNT = 4;
const KEYBOARD_SETTLE_FALLBACK_MS = 320;
const IOS_KEYBOARD_CHIN_PADDING_PX = 24;
const THEME_STORAGE_KEY = "shotwright_theme";

type ColorTheme = "light" | "dark";
type WorkbenchStatusItem = {
  key: string;
  label: string;
  value: string;
  tone?: "primary" | "accent" | "neutral" | "muted" | "danger" | "success";
};
type VisualKeyboardState = {
  keyboardInset: number;
  composerBottom: number;
  fixedOffsetY: number;
  layoutHeight: number;
  visualBottom: number;
  rawInset: number;
  source: string;
  settling: boolean;
};
type KeyboardDiagnostic = {
  stableFrames: number;
  thresholdFrames: number;
  keyboardInset: number;
  composerBottom: number;
  fixedOffsetY: number;
  layoutHeight: number;
  visualBottom: number;
  rawInset: number;
  visualHeight: number;
  visualOffsetTop: number;
  innerHeight: number;
  source: string;
  settling: boolean;
  open: boolean;
};

const EMPTY_KEYBOARD_DIAGNOSTIC: KeyboardDiagnostic = {
  stableFrames: 0,
  thresholdFrames: KEYBOARD_STABLE_FRAME_COUNT,
  keyboardInset: 0,
  composerBottom: 0,
  fixedOffsetY: 0,
  layoutHeight: 0,
  visualBottom: 0,
  rawInset: 0,
  visualHeight: 0,
  visualOffsetTop: 0,
  innerHeight: 0,
  source: "none",
  settling: false,
  open: false,
};

const WORKBENCH_STATUS_EVENT = "shotwright:statusbar";

function getWorkbenchStatusItems(detail: unknown): WorkbenchStatusItem[] {
  if (!detail || typeof detail !== "object" || !("items" in detail)) {
    return [];
  }

  const items = (detail as { items?: unknown }).items;
  if (!Array.isArray(items)) {
    return [];
  }

  return items
    .filter((item): item is WorkbenchStatusItem => {
      if (!item || typeof item !== "object") return false;
      const candidate = item as Partial<WorkbenchStatusItem>;
      return Boolean(candidate.key && candidate.label && candidate.value);
    })
    .slice(0, 5);
}

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
  const viewportHeight = visualViewport?.height ?? window.innerHeight ?? 0;
  const visualOffsetTop = Math.max(0, visualViewport?.offsetTop ?? 0);
  const rootHeight = document.documentElement.clientHeight || 0;
  const innerHeight = window.innerHeight || 0;
  const layoutHeight = Math.max(viewportHeight, layoutViewportHeight, rootHeight, innerHeight);
  const visualBottom = Math.min(layoutHeight, visualOffsetTop + viewportHeight);
  const bottomInset = Math.max(0, layoutHeight - visualBottom);

  if (!visualViewport || !isKeyboardTextTarget(document.activeElement)) {
    return {
      keyboardInset: 0,
      composerBottom: 0,
      fixedOffsetY: 0,
      layoutHeight,
      visualBottom,
      rawInset: bottomInset,
      source: "none",
      settling: false,
    };
  }

  const useFixedOffset = isAppleTouchViewport();
  const heightShrinkInset = Math.max(0, layoutHeight - viewportHeight);
  const rawInset = useFixedOffset ? Math.max(bottomInset, visualOffsetTop, heightShrinkInset) : bottomInset;
  const maxInsetBase = useFixedOffset ? visualBottom || layoutHeight : layoutHeight;
  const maxInsetRatio = useFixedOffset ? IOS_MAX_KEYBOARD_INSET_RATIO : MAX_KEYBOARD_INSET_RATIO;
  const maxInset = Math.round(maxInsetBase * maxInsetRatio);
  const keyboardInset = Math.min(rawInset, maxInset);
  const resolvedKeyboardInset = keyboardInset > KEYBOARD_INSET_THRESHOLD ? Math.round(keyboardInset) : 0;
  const fixedOffsetY =
    useFixedOffset && resolvedKeyboardInset > 0
      ? Math.max(0, Math.round(visualOffsetTop - resolvedKeyboardInset + IOS_KEYBOARD_CHIN_PADDING_PX))
      : 0;

  return {
    keyboardInset: resolvedKeyboardInset,
    composerBottom: resolvedKeyboardInset > 0 && !useFixedOffset ? resolvedKeyboardInset : 0,
    fixedOffsetY,
    layoutHeight: Math.round(layoutHeight),
    visualBottom: Math.round(visualBottom),
    rawInset: Math.round(rawInset),
    source: useFixedOffset ? "ios-visual" : "bottom",
    settling: false,
  };
}

function getVisualKeyboardDiagnostic(state: VisualKeyboardState, stableFrames: number): KeyboardDiagnostic {
  const visualViewport = window.visualViewport;
  return {
    stableFrames,
    thresholdFrames: KEYBOARD_STABLE_FRAME_COUNT,
    keyboardInset: state.keyboardInset,
    composerBottom: state.composerBottom,
    fixedOffsetY: state.fixedOffsetY,
    layoutHeight: state.layoutHeight,
    visualBottom: state.visualBottom,
    rawInset: state.rawInset,
    visualHeight: Math.round(visualViewport?.height ?? window.innerHeight ?? 0),
    visualOffsetTop: Math.round(visualViewport?.offsetTop ?? 0),
    innerHeight: Math.round(window.innerHeight || document.documentElement.clientHeight || 0),
    source: state.source,
    settling: state.settling,
    open: state.keyboardInset > 0,
  };
}

function formatKeyboardDiagnostic(diagnostic: KeyboardDiagnostic) {
  return [
    `KB ${diagnostic.open ? "open" : diagnostic.settling ? "settle" : "idle"}`,
    `${diagnostic.stableFrames}/${diagnostic.thresholdFrames}`,
    `k${diagnostic.keyboardInset}`,
    `y${diagnostic.fixedOffsetY}`,
    `vv ${diagnostic.visualHeight}@${diagnostic.visualOffsetTop}`,
    `b${diagnostic.visualBottom}`,
  ].join(" ");
}

function WorkbenchApp() {
  const location = useLocation();
  const { locale, setLocale, copy } = useI18n();
  const [isMobileDrawerLayout, setIsMobileDrawerLayout] = useState(() => mediaQueryMatches(MOBILE_DRAWER_QUERY));
  const [isNarrowContextLayout, setIsNarrowContextLayout] = useState(() => mediaQueryMatches(NARROW_CONTEXT_QUERY));
  const [isSessionSidebarCollapsed, setIsSessionSidebarCollapsed] = useState(() => mediaQueryMatches(MOBILE_DRAWER_QUERY));
  const [isContextSidebarCollapsed, setIsContextSidebarCollapsed] = useState(() => mediaQueryMatches(NARROW_CONTEXT_QUERY));
  const [colorTheme, setColorTheme] = useState<ColorTheme>(() => getInitialColorTheme());
  const [workbenchStatusItems, setWorkbenchStatusItems] = useState<WorkbenchStatusItem[]>([]);
  const keyboardDiagnosticRef = useRef<HTMLSpanElement | null>(null);
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
    const handleWorkbenchStatus = (event: Event) => {
      setWorkbenchStatusItems(getWorkbenchStatusItems((event as CustomEvent).detail));
    };

    window.addEventListener(WORKBENCH_STATUS_EVENT, handleWorkbenchStatus);
    return () => window.removeEventListener(WORKBENCH_STATUS_EVENT, handleWorkbenchStatus);
  }, []);

  useEffect(() => {
    const root = document.documentElement;
    let frameId = 0;
    let layoutViewportHeight = window.innerHeight || document.documentElement.clientHeight || 0;
    let layoutViewportWidth = window.innerWidth || document.documentElement.clientWidth || 0;
    let lastKeyboardSignature = "";
    let stableKeyboardFrames = 0;
    let keyboardCandidateStartedAt = 0;
    root.classList.toggle("is-apple-touch-viewport", isAppleTouchViewport());

    const updateKeyboardDiagnostic = (diagnostic: KeyboardDiagnostic) => {
      const node = keyboardDiagnosticRef.current;
      if (!node) return;

      const nextText = formatKeyboardDiagnostic(diagnostic);
      if (node.textContent !== nextText) {
        node.textContent = nextText;
      }

      const nextTitle = JSON.stringify(diagnostic);
      if (node.title !== nextTitle) {
        node.title = nextTitle;
      }
    };

    const applyKeyboardState = (keyboardState: VisualKeyboardState) => {
      root.style.setProperty("--app-keyboard-inset-bottom", `${keyboardState.keyboardInset}px`);
      root.style.setProperty("--app-keyboard-composer-bottom", `${keyboardState.composerBottom}px`);
      root.style.setProperty("--app-keyboard-fixed-offset-y", `${keyboardState.fixedOffsetY}px`);
      root.classList.toggle("is-visual-keyboard-open", keyboardState.keyboardInset > 0);
      updateKeyboardDiagnostic(getVisualKeyboardDiagnostic(keyboardState, stableKeyboardFrames));
    };

    const resetKeyboardSettle = () => {
      lastKeyboardSignature = "";
      stableKeyboardFrames = 0;
      keyboardCandidateStartedAt = 0;
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

      if (!keyboardCandidateStartedAt) {
        keyboardCandidateStartedAt = performance.now();
      }

      const keyboardSignature = getKeyboardViewportSignature();
      if (keyboardSignature === lastKeyboardSignature) {
        stableKeyboardFrames += 1;
      } else {
        lastKeyboardSignature = keyboardSignature;
        stableKeyboardFrames = 1;
        keyboardCandidateStartedAt = performance.now();
      }

      const keyboardWaitedLongEnough = performance.now() - keyboardCandidateStartedAt >= KEYBOARD_SETTLE_FALLBACK_MS;
      if (stableKeyboardFrames < KEYBOARD_STABLE_FRAME_COUNT && !keyboardWaitedLongEnough) {
        updateKeyboardDiagnostic(
          getVisualKeyboardDiagnostic(
            { ...keyboardState, keyboardInset: 0, composerBottom: 0, fixedOffsetY: 0, settling: true },
            stableKeyboardFrames,
          ),
        );
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
            <span>Git main*</span>
          </span>
          <span className="statusbar-item statusbar-local-workspace" title={copy.app.localWorkspace}>
            <span className="statusbar-dot is-online" aria-hidden="true" />
            <span>{copy.app.localWorkspace}</span>
          </span>
          {workbenchStatusItems.map((item) => (
            <span
              key={item.key}
              className={`statusbar-item statusbar-dynamic-item tone-${item.tone || "neutral"}`}
              title={`${item.label}: ${item.value}`}
            >
              <span className="statusbar-mini-label">{item.label}</span>
              <span className="statusbar-value">{item.value}</span>
            </span>
          ))}
        </div>
        <div className="statusbar-group statusbar-group-right">
          <span
            ref={keyboardDiagnosticRef}
            className="statusbar-item statusbar-item-subtle statusbar-keyboard-diagnostic"
            data-testid="keyboard-diagnostic"
            title={JSON.stringify(EMPTY_KEYBOARD_DIAGNOSTIC)}
          >
            {formatKeyboardDiagnostic(EMPTY_KEYBOARD_DIAGNOSTIC)}
          </span>
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

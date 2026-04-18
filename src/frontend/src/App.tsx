import { useEffect, useState } from "react";
import { BrowserRouter, NavLink, Route, Routes, useLocation } from "react-router-dom";
import AgentPanel from "./components/AgentPanel/AgentPanel";
import AdminPanel from "./components/AdminPanel/AdminPanel";
import { useI18n } from "./i18n";
import { getPublicRuntimeSettings } from "./services/api";
import { PublicRuntimeSettings } from "./types";

function WorkbenchApp() {
  const location = useLocation();
  const { locale, setLocale, copy } = useI18n();
  const currentSection = location.pathname === "/admin" ? copy.app.admin : copy.app.chat;
  const [runtimeSettings, setRuntimeSettings] = useState<PublicRuntimeSettings | null>(null);

  const loadRuntimeSettings = () => {
    getPublicRuntimeSettings()
      .then((response) => {
        setRuntimeSettings(response.data);
      })
      .catch(() => {
        setRuntimeSettings(null);
      });
  };

  useEffect(() => {
    loadRuntimeSettings();

    const handleRuntimeSettingsUpdated = () => {
      loadRuntimeSettings();
    };

    window.addEventListener("shotwright-runtime-settings-updated", handleRuntimeSettingsUpdated);

    return () => {
      window.removeEventListener("shotwright-runtime-settings-updated", handleRuntimeSettingsUpdated);
    };
  }, []);

  const runtimeLabel = runtimeSettings
    ? `${runtimeSettings.copilot_model} · ${copy.common.reasoningEfforts[runtimeSettings.copilot_reasoning_effort]}`
    : copy.common.copilot;

  return (
    <div className="workbench">
      <header className="titlebar">
        <div className="titlebar-left">
          <div className="titlebar-brand-mark">SW</div>
          <span className="titlebar-product">{copy.app.product}</span>
          <nav className="titlebar-nav" aria-label={copy.app.primaryNavLabel}>
            <NavLink to="/" end className={({ isActive }) => `titlebar-tab${isActive ? " active" : ""}`}>
              {copy.app.chat}
            </NavLink>
            <NavLink to="/admin" className={({ isActive }) => `titlebar-tab${isActive ? " active" : ""}`}>
              {copy.app.admin}
            </NavLink>
          </nav>
        </div>
        <div className="titlebar-center">{copy.app.workspace}</div>
        <div className="titlebar-right">
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
            <Route path="/" element={<AgentPanel />} />
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
          <span>{runtimeLabel}</span>
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

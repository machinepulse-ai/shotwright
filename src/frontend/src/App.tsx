import { BrowserRouter, Route, Routes, Link } from "react-router-dom";
import AgentPanel from "./components/AgentPanel/AgentPanel";
import AdminPanel from "./components/AdminPanel/AdminPanel";

export default function App() {
  return (
    <BrowserRouter>
      <div className="app">
        <nav className="app-nav">
          <h1 className="app-logo">Shotwright</h1>
          <div className="app-links">
            <Link to="/">Agent</Link>
            <Link to="/admin">Admin</Link>
          </div>
        </nav>
        <main className="app-main">
          <Routes>
            <Route path="/" element={<AgentPanel />} />
            <Route path="/admin" element={<AdminPanel />} />
          </Routes>
        </main>
      </div>
    </BrowserRouter>
  );
}

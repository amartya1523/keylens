import { useEffect, useState } from "react";
import { Navigate, NavLink, Route, Routes, useNavigate } from "react-router-dom";
import { api, getToken, post, setToken } from "./api";
import Auth from "./pages/Auth";
import Overview from "./pages/Overview";
import Keys from "./pages/Keys";
import Apps from "./pages/Apps";
import Requests from "./pages/Requests";
import Insights from "./pages/Insights";
import Playground from "./pages/Playground";
import Scanner from "./pages/Scanner";
import Privacy from "./pages/Privacy";

export type User = { id: number; email: string; name: string };

const NAV = [
  ["/", "◎", "Overview"],
  ["/keys", "⚿", "API keys"],
  ["/apps", "▦", "Apps & virtual keys"],
  ["/requests", "≡", "Request log"],
  ["/insights", "✦", "Insights"],
  ["/playground", "▷", "Browser playground"],
  ["/scanner", "⌕", "Key scanner"],
  ["/privacy", "◍", "Privacy"],
];

export default function App() {
  const [user, setUser] = useState<User | null>(null);
  const [ready, setReady] = useState(false);
  const navigate = useNavigate();

  useEffect(() => {
    if (!getToken()) {
      setReady(true);
      return;
    }
    api<User>("/api/auth/me")
      .then(setUser)
      .catch(() => setToken(null))
      .finally(() => setReady(true));
  }, []);

  if (!ready) return null;
  if (!user)
    return (
      <Routes>
        <Route path="*" element={<Auth onAuth={(u) => { setUser(u); navigate("/"); }} />} />
      </Routes>
    );

  const logout = async () => {
    await post("/api/auth/logout").catch(() => {});
    setToken(null);
    setUser(null);
    navigate("/login");
  };

  return (
    <div className="layout">
      <aside className="side">
        <div className="brand">
          <img src="/favicon.svg" alt="" /> KeyLens
        </div>
        <nav className="nav">
          {NAV.map(([to, ico, label]) => (
            <NavLink key={to} to={to} end={to === "/"}>
              <span className="ico">{ico}</span>
              {label}
            </NavLink>
          ))}
        </nav>
        <div className="foot">
          <ProxyStatus />
          <div>{user.name}</div>
          <div>{user.email}</div>
          <button className="btn small mt" onClick={logout}>Sign out</button>
        </div>
      </aside>
      <main className="main">
        <Routes>
          <Route path="/" element={<Overview />} />
          <Route path="/keys" element={<Keys />} />
          <Route path="/apps" element={<Apps />} />
          <Route path="/requests" element={<Requests />} />
          <Route path="/insights" element={<Insights />} />
          <Route path="/playground" element={<Playground />} />
          <Route path="/scanner" element={<Scanner />} />
          <Route path="/privacy" element={<Privacy />} />
          <Route path="*" element={<Navigate to="/" />} />
        </Routes>
      </main>
    </div>
  );
}

function ProxyStatus() {
  const [up, setUp] = useState<boolean | null>(null);
  useEffect(() => {
    const ping = () => api("/health").then(() => setUp(true)).catch(() => setUp(false));
    ping();
    const t = setInterval(ping, 15000);
    return () => clearInterval(t);
  }, []);
  return (
    <div className="proxy">
      <span className={`dot ${up ? "ok" : "bad"}`} />
      {up === null ? "Checking proxy…" : up ? "Proxy online · /v1" : "Proxy offline"}
    </div>
  );
}

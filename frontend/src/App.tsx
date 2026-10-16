import { useEffect, useState } from "react";
import { NavLink, Route, Routes, useNavigate } from "react-router-dom";
import { api, setUnauthorizedHandler } from "./api";
import { Spinner } from "./ui";
import Login from "./pages/Login";
import Dashboard from "./pages/Dashboard";
import Upload from "./pages/Upload";
import Review from "./pages/Review";
import Collection from "./pages/Collection";
import PokemonDetail from "./pages/PokemonDetail";
import Recommendations from "./pages/Recommendations";
import Scans from "./pages/Scans";
import Settings from "./pages/Settings";

const NAV = [
  { to: "/", label: "Home", icon: "M3 11l9-8 9 8v9a1 1 0 01-1 1h-5v-6H9v6H4a1 1 0 01-1-1z" },
  { to: "/upload", label: "Scan", icon: "M12 16V4m0 0l-5 5m5-5l5 5M4 20h16" },
  { to: "/review", label: "Review", icon: "M9 12l2 2 4-4M5 4h14v16H5z" },
  { to: "/collection", label: "Vault", icon: "M4 6h16M4 12h16M4 18h10" },
  { to: "/insights", label: "Insights", icon: "M5 19V9m7 10V5m7 14v-7" },
];

function Icon({ d }: { d: string }) {
  return (
    <svg viewBox="0 0 24 24" className="h-5 w-5" fill="none" stroke="currentColor" strokeWidth={1.8} strokeLinecap="round" strokeLinejoin="round">
      <path d={d} />
    </svg>
  );
}

export default function App() {
  const [auth, setAuth] = useState<boolean | null>(null);
  const [pending, setPending] = useState(0);
  const nav = useNavigate();

  useEffect(() => {
    setUnauthorizedHandler(() => setAuth(false));
    api<{ authenticated: boolean }>("/api/auth/me").then((r) => setAuth(r.authenticated)).catch(() => setAuth(false));
  }, []);

  useEffect(() => {
    if (!auth) return;
    const load = () => api<any[]>("/api/review").then((g) => setPending(g.length)).catch(() => {});
    load();
    const t = window.setInterval(load, 8000);
    return () => window.clearInterval(t);
  }, [auth]);

  if (auth === null) return <div className="flex h-screen items-center justify-center"><Spinner /></div>;
  if (!auth) return <Login onDone={() => { setAuth(true); nav("/"); }} />;

  return (
    <div className="mx-auto flex min-h-screen max-w-6xl">
      <aside className="sticky top-0 hidden h-screen w-56 shrink-0 flex-col gap-1 border-r border-slate-200/70 p-4 dark:border-white/5 md:flex">
        <Brand />
        <nav className="mt-6 flex flex-col gap-1">
          {NAV.map((n) => (
            <NavLink key={n.to} to={n.to} end={n.to === "/"}
              className={({ isActive }) => `flex items-center gap-3 rounded-xl px-3 py-2 text-sm font-medium ${isActive ? "bg-vault-500/10 text-vault-600 dark:text-vault-300" : "text-slate-600 hover:bg-slate-100 dark:text-slate-300 dark:hover:bg-white/5"}`}>
              <Icon d={n.icon} /> {n.label}
              {n.to === "/review" && pending > 0 && <span className="ml-auto chip bg-amber-500 text-white">{pending}</span>}
            </NavLink>
          ))}
          <NavLink to="/scans" className={({ isActive }) => `rounded-xl px-3 py-2 text-sm ${isActive ? "text-vault-500" : "text-slate-500"}`}>Scan history</NavLink>
          <NavLink to="/settings" className={({ isActive }) => `rounded-xl px-3 py-2 text-sm ${isActive ? "text-vault-500" : "text-slate-500"}`}>Privacy & settings</NavLink>
        </nav>
      </aside>

      <main className="min-w-0 flex-1 px-4 pb-28 pt-[max(1rem,env(safe-area-inset-top))] md:px-8 md:pb-10 md:pt-8">
        <div className="mb-4 flex items-center justify-between md:hidden">
          <Brand />
          <NavLink to="/settings" className="rounded-full p-2 text-slate-500" aria-label="Settings">
            <Icon d="M12 15a3 3 0 100-6 3 3 0 000 6zM19.4 15a1.7 1.7 0 00.3 1.8l.1.1a2 2 0 11-2.8 2.8l-.1-.1a1.7 1.7 0 00-2.9 1.2V21a2 2 0 01-4 0v-.1a1.7 1.7 0 00-2.9-1.2l-.1.1a2 2 0 11-2.8-2.8l.1-.1a1.7 1.7 0 00-1.2-2.9H3a2 2 0 010-4h.1a1.7 1.7 0 001.2-2.9l-.1-.1a2 2 0 112.8-2.8l.1.1a1.7 1.7 0 002.9-1.2V3a2 2 0 014 0v.1a1.7 1.7 0 002.9 1.2l.1-.1a2 2 0 112.8 2.8l-.1.1a1.7 1.7 0 001.2 2.9H21a2 2 0 010 4h-.1a1.7 1.7 0 00-1.5 1z" />
          </NavLink>
        </div>
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/upload" element={<Upload />} />
          <Route path="/review" element={<Review onChange={setPending} />} />
          <Route path="/review/scan/:scanId" element={<Review onChange={setPending} />} />
          <Route path="/collection" element={<Collection />} />
          <Route path="/collection/:id" element={<PokemonDetail />} />
          <Route path="/insights" element={<Recommendations />} />
          <Route path="/scans" element={<Scans />} />
          <Route path="/settings" element={<Settings onLogout={() => setAuth(false)} />} />
          <Route path="*" element={<Dashboard />} />
        </Routes>
      </main>

      <nav className="fixed inset-x-0 bottom-0 z-20 border-t border-slate-200/70 bg-white/85 pb-[env(safe-area-inset-bottom)] backdrop-blur-xl dark:border-white/5 dark:bg-[#0b0d14]/85 md:hidden">
        <div className="mx-auto grid max-w-md grid-cols-5">
          {NAV.map((n) => (
            <NavLink key={n.to} to={n.to} end={n.to === "/"}
              className={({ isActive }) => `relative flex flex-col items-center gap-0.5 py-2.5 text-[10px] font-semibold ${isActive ? "text-vault-500" : "text-slate-500"}`}>
              <Icon d={n.icon} />
              {n.label}
              {n.to === "/review" && pending > 0 && <span className="absolute right-4 top-1.5 h-2 w-2 rounded-full bg-amber-500" />}
            </NavLink>
          ))}
        </div>
      </nav>
    </div>
  );
}

function Brand() {
  return (
    <div className="flex items-center gap-2">
      <img src="/favicon.svg" alt="" className="h-7 w-7" />
      <span className="text-lg font-bold tracking-tight">Poké<span className="text-vault-500">Vault</span></span>
    </div>
  );
}

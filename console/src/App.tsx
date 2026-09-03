import { AnimatePresence, motion } from "framer-motion";
import { useEffect } from "react";
import { NavLink, Navigate, Route, Routes, useLocation, useNavigate } from "react-router-dom";
import { adminToken, setAdminToken } from "./api";
import { StoreProvider, useStore } from "./store";
import Overview from "./pages/Overview";
import Catalog from "./pages/Catalog";
import Offers from "./pages/Offers";
import Sessions from "./pages/Sessions";
import Audit from "./pages/Audit";
import Playground from "./pages/Playground";
import { ErrorBoundary } from "./components/ui";

const NAV = [
  { to: "/overview", label: "Overview", k: "1" },
  { to: "/catalog", label: "Catalog", k: "2" },
  { to: "/offers", label: "Offers & policy", k: "3" },
  { to: "/sessions", label: "Sessions", k: "4" },
  { to: "/audit", label: "Audit", k: "5" },
  { to: "/playground", label: "Playground", k: "6" },
];

function Mark() {
  return (
    <span className="relative inline-flex w-7 h-7 items-center justify-center">
      <span className="absolute inset-0 rounded-full bg-accent" />
      <span className="relative w-2.5 h-2.5 rounded-full bg-paper" />
    </span>
  );
}

function Shell() {
  const { merchants, merchantId, setMerchantId, theme, toggleTheme, toasts, online } = useStore();
  const loc = useLocation();
  const nav = useNavigate();
  const current = merchants.find((m) => m.merchant_id === merchantId);

  useEffect(() => {
    const label = NAV.find((n) => loc.pathname.startsWith(n.to))?.label;
    document.title = label ? `Bazaar — ${label}` : "Bazaar Console";
  }, [loc.pathname]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      const t = e.target as HTMLElement | null;
      if (t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.tagName === "SELECT" || t.isContentEditable)) return;
      const hit = NAV.find((n) => n.k === e.key);
      if (hit) nav(hit.to);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [nav]);
  return (
    <div className="min-h-full flex">
      <aside className="hidden md:flex w-[232px] shrink-0 flex-col border-r hairline sticky top-0 h-screen">
        <div className="px-5 pt-6 pb-5 flex items-center gap-3">
          <Mark />
          <div>
            <div className="display text-[20px] leading-none">Bazaar</div>
            <div className="text-[10.5px] uppercase tracking-[0.18em] text-muted mt-1">merchant console</div>
          </div>
        </div>
        <nav className="px-3 mt-2 flex flex-col gap-0.5">
          {NAV.map((n) => (
            <NavLink key={n.to} to={n.to} className={({ isActive }) => `group flex items-center justify-between rounded-md px-3 h-9 text-[13.5px] transition-colors ${isActive ? "bg-paper-2 text-ink" : "text-ink-2 hover:text-ink hover:bg-paper-2/60"}`}>
              {({ isActive }) => (
                <>
                  <span className="flex items-center gap-2.5">
                    <span className={`w-1.5 h-1.5 rounded-full transition-colors ${isActive ? "bg-accent" : "bg-transparent group-hover:bg-line-strong"}`} />
                    {n.label}
                  </span>
                  <span className="kbd opacity-0 group-hover:opacity-100 transition-opacity">{n.k}</span>
                </>
              )}
            </NavLink>
          ))}
        </nav>
        <div className="mt-auto p-4 border-t hairline">
          <div className="text-[10.5px] uppercase tracking-[0.16em] text-muted mb-1.5">Merchant</div>
          <select value={merchantId} onChange={(e) => setMerchantId(e.target.value)} className="input h-9 text-[13px]">
            {merchants.map((m) => (
              <option key={m.merchant_id} value={m.merchant_id}>
                {m.name} · {m.city}
              </option>
            ))}
          </select>
          <div className="flex items-center justify-between mt-3">
            <span className={`text-[11.5px] flex items-center gap-1.5 ${online ? "text-ok" : "text-danger"}`}>
              <span className={`w-1.5 h-1.5 rounded-full ${online ? "bg-ok" : "bg-danger"}`} /> {online ? "gateway live" : "gateway offline"}
            </span>
            <button className="btn btn-quiet h-7 px-2 text-[12px]" onClick={toggleTheme} title="Toggle theme">
              {theme === "dark" ? "Light" : "Dark"}
            </button>
          </div>
          {current?.kill_switch && <div className="chip chip-danger mt-3 w-full justify-center">agent disabled</div>}
          <details className="mt-3">
            <summary className="text-[10.5px] uppercase tracking-[0.16em] text-muted cursor-pointer select-none">Admin token</summary>
            <input
              type="password"
              className="input h-8 mt-1.5 mono text-[12px]"
              defaultValue={adminToken()}
              onChange={(e) => setAdminToken(e.target.value)}
              placeholder="BAZAAR_ADMIN_TOKEN"
              autoComplete="off"
            />
            <div className="text-[10.5px] text-muted mt-1">gates compile, publish, rules, policy, kill switch</div>
          </details>
        </div>
      </aside>
      <main className="flex-1 min-w-0">
        <div className="md:hidden flex items-center gap-2 px-4 py-3 border-b hairline">
          <Mark />
          <span className="display text-[18px]">Bazaar</span>
          <select value={merchantId} onChange={(e) => setMerchantId(e.target.value)} className="input h-8 ml-auto max-w-[55%] text-[12px]">
            {merchants.map((m) => (
              <option key={m.merchant_id} value={m.merchant_id}>
                {m.name}
              </option>
            ))}
          </select>
        </div>
        <div className="md:hidden flex gap-1 px-2 py-2 overflow-x-auto no-scrollbar border-b hairline">
          {NAV.map((n) => (
            <NavLink key={n.to} to={n.to} className={({ isActive }) => `chip ${isActive ? "chip-accent" : ""}`}>
              {n.label}
            </NavLink>
          ))}
        </div>
        <AnimatePresence mode="wait">
          <motion.div key={loc.pathname} initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0, transition: { duration: 0.12 } }} transition={{ duration: 0.2 }}>
            <ErrorBoundary>
              <Routes location={loc}>
                <Route path="/" element={<Navigate to="/overview" replace />} />
                <Route path="/overview" element={<Overview />} />
                <Route path="/catalog" element={<Catalog />} />
                <Route path="/offers" element={<Offers />} />
                <Route path="/sessions" element={<Sessions />} />
                <Route path="/audit" element={<Audit />} />
                <Route path="/playground" element={<Playground />} />
                <Route path="*" element={<Navigate to="/overview" replace />} />
              </Routes>
            </ErrorBoundary>
          </motion.div>
        </AnimatePresence>
      </main>
      <div className="fixed bottom-5 right-5 flex flex-col gap-2 z-50 max-w-[min(92vw,420px)]" role="status" aria-live="polite">
        <AnimatePresence>
          {toasts.map((t) => (
            <motion.div key={t.id} initial={{ opacity: 0, y: 8, scale: 0.98 }} animate={{ opacity: 1, y: 0, scale: 1 }} exit={{ opacity: 0, y: 8 }} className={`card px-4 py-2.5 text-[13px] ${t.kind === "ok" ? "border-ok/40" : t.kind === "danger" ? "border-danger/40" : ""}`}>
              <span className={`inline-block w-1.5 h-1.5 rounded-full mr-2 ${t.kind === "ok" ? "bg-ok" : t.kind === "danger" ? "bg-danger" : "bg-accent"}`} />
              {t.msg}
            </motion.div>
          ))}
        </AnimatePresence>
      </div>
    </div>
  );
}

export default function App() {
  return (
    <StoreProvider>
      <Shell />
    </StoreProvider>
  );
}

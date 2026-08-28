import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { api, type MerchantRow } from "./api";

type Ctx = {
  merchants: MerchantRow[];
  merchantId: string;
  setMerchantId: (id: string) => void;
  refreshMerchants: () => Promise<void>;
  theme: "light" | "dark";
  toggleTheme: () => void;
  toast: (msg: string, kind?: "ok" | "danger" | "info") => void;
  toasts: { id: number; msg: string; kind: "ok" | "danger" | "info" }[];
  online: boolean;
};

const C = createContext<Ctx | null>(null);

export function StoreProvider({ children }: { children: ReactNode }) {
  const [merchants, setMerchants] = useState<MerchantRow[]>([]);
  const [merchantId, setMerchantIdState] = useState<string>(() => {
    try {
      return localStorage.getItem("bazaar-merchant") ?? "";
    } catch {
      return "";
    }
  });
  const [theme, setTheme] = useState<"light" | "dark">(() => (document.documentElement.getAttribute("data-theme") === "dark" ? "dark" : "light"));
  const [toasts, setToasts] = useState<Ctx["toasts"]>([]);
  const [online, setOnline] = useState(true);

  const refreshMerchants = useCallback(async () => {
    try {
      const ms = await api.merchants();
      setMerchants(ms);
      setOnline(true);
      if (ms.length && !ms.some((m) => m.merchant_id === merchantId)) setMerchantIdState(ms[0].merchant_id);
    } catch {
      setOnline(false);
    }
  }, [merchantId]);

  useEffect(() => {
    void refreshMerchants();
    const t = setInterval(() => void refreshMerchants(), 15000);
    return () => clearInterval(t);
  }, [refreshMerchants]);

  const setMerchantId = (id: string) => {
    setMerchantIdState(id);
    try {
      localStorage.setItem("bazaar-merchant", id);
    } catch {
      /* ignore */
    }
  };

  const toggleTheme = () => {
    const next = theme === "dark" ? "light" : "dark";
    setTheme(next);
    if (next === "dark") document.documentElement.setAttribute("data-theme", "dark");
    else document.documentElement.removeAttribute("data-theme");
    try {
      localStorage.setItem("bazaar-theme", next);
    } catch {
      /* ignore */
    }
  };

  const toast = useCallback((msg: string, kind: "ok" | "danger" | "info" = "info") => {
    const id = Date.now() + Math.random();
    setToasts((t) => [...t, { id, msg, kind }]);
    setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), 3800);
  }, []);

  const value = useMemo(() => ({ merchants, merchantId, setMerchantId, refreshMerchants, theme, toggleTheme, toast, toasts, online }), [merchants, merchantId, refreshMerchants, theme, toast, toasts, online]);
  return <C.Provider value={value}>{children}</C.Provider>;
}

export function useStore() {
  const v = useContext(C);
  if (!v) throw new Error("StoreProvider missing");
  return v;
}

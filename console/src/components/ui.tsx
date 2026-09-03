import { motion, type Variants } from "framer-motion";
import { Component, type ErrorInfo, type ReactNode } from "react";
import type { Check } from "../api";

export const stagger: Variants = { hidden: {}, show: { transition: { staggerChildren: 0.06, delayChildren: 0.05 } } };
export const rise: Variants = {
  hidden: { opacity: 0, y: 10, filter: "blur(2px)" },
  show: { opacity: 1, y: 0, filter: "blur(0px)", transition: { duration: 0.42, ease: [0.22, 1, 0.36, 1] } },
};

export function Page({ title, kicker, children, actions }: { title: string; kicker?: string; children: ReactNode; actions?: ReactNode }) {
  return (
    <motion.div variants={stagger} initial="hidden" animate="show" className="max-w-[1180px] mx-auto px-6 md:px-10 py-8 md:py-10">
      <motion.header variants={rise} className="flex items-end justify-between gap-6 mb-8">
        <div>
          {kicker && <div className="text-[11.5px] uppercase tracking-[0.18em] text-muted mb-2">{kicker}</div>}
          <h1 className="text-[34px] md:text-[42px] leading-[1.05]">{title}</h1>
        </div>
        {actions && <div className="flex items-center gap-2 shrink-0">{actions}</div>}
      </motion.header>
      {children}
    </motion.div>
  );
}

export function Card({ children, className = "", title, aside, pad = true }: { children: ReactNode; className?: string; title?: ReactNode; aside?: ReactNode; pad?: boolean }) {
  return (
    <motion.section variants={rise} className={`card min-w-0 ${pad ? "p-5" : ""} ${className}`}>
      {(title || aside) && (
        <div className={`flex flex-wrap items-center justify-between gap-x-3 gap-y-2 ${pad ? "mb-4" : "px-5 pt-5 pb-3"}`}>
          {title && <h3 className="text-[17px] min-w-0">{title}</h3>}
          {aside && <div className="flex flex-wrap items-center gap-2 min-w-0 ml-auto justify-end">{aside}</div>}
        </div>
      )}
      {children}
    </motion.section>
  );
}

export function Stat({ label, value, sub, accent }: { label: string; value: ReactNode; sub?: ReactNode; accent?: boolean }) {
  return (
    <motion.div variants={rise} className="card p-5">
      <div className="text-[11.5px] uppercase tracking-[0.16em] text-muted">{label}</div>
      <div className={`display mt-2 text-[34px] leading-none num ${accent ? "text-accent" : ""}`}>{value}</div>
      {sub && <div className="mt-2 text-[12.5px] text-ink-2">{sub}</div>}
    </motion.div>
  );
}

export function Chip({ children, kind = "" }: { children: ReactNode; kind?: "" | "ok" | "warn" | "danger" | "accent" }) {
  return <span className={`chip ${kind ? `chip-${kind}` : ""}`}>{children}</span>;
}

export function StatusChip({ status }: { status: string }) {
  const map: Record<string, "" | "ok" | "warn" | "danger" | "accent"> = { completed: "ok", in_progress: "accent", ready_for_payment: "accent", awaiting_merchant_review: "warn", open: "", canceled: "", declined: "danger" };
  return <Chip kind={map[status] ?? ""}>{status.replaceAll("_", " ")}</Chip>;
}

export function Ring({ value, size = 120, stroke = 9, label }: { value: number; size?: number; stroke?: number; label?: string }) {
  const r = (size - stroke) / 2;
  const c = 2 * Math.PI * r;
  const off = c - (Math.max(0, Math.min(100, value)) / 100) * c;
  return (
    <div className="relative" style={{ width: size, height: size }}>
      <svg width={size} height={size} className="-rotate-90">
        <circle cx={size / 2} cy={size / 2} r={r} stroke="var(--line)" strokeWidth={stroke} fill="none" />
        <motion.circle cx={size / 2} cy={size / 2} r={r} stroke={value >= 80 ? "var(--ok)" : value >= 60 ? "var(--haldi)" : "var(--accent)"} strokeWidth={stroke} fill="none" strokeLinecap="round" strokeDasharray={c} initial={{ strokeDashoffset: c }} animate={{ strokeDashoffset: off }} transition={{ duration: 1.1, ease: [0.22, 1, 0.36, 1] }} />
      </svg>
      <div className="absolute inset-0 flex flex-col items-center justify-center">
        <div className="display num text-[30px] leading-none">{Math.round(value)}</div>
        {label && <div className="text-[10.5px] uppercase tracking-[0.16em] text-muted mt-1">{label}</div>}
      </div>
    </div>
  );
}

export function Bar({ label, value, max, hint }: { label: string; value: number; max: number; hint?: string }) {
  const pct = max ? Math.round((value / max) * 100) : 0;
  return (
    <div className="py-2">
      <div className="flex justify-between text-[12.5px] mb-1">
        <span className="text-ink-2">{label}</span>
        <span className="num text-muted">
          {value}/{max}
        </span>
      </div>
      <div className="h-[6px] rounded-full bg-paper-3 overflow-hidden">
        <motion.div className="h-full rounded-full" style={{ background: pct >= 100 ? "var(--ok)" : pct >= 50 ? "var(--haldi)" : "var(--accent)" }} initial={{ width: 0 }} animate={{ width: `${pct}%` }} transition={{ duration: 0.8, ease: [0.22, 1, 0.36, 1] }} />
      </div>
      {hint && <div className="text-[11.5px] text-muted mt-1">{hint}</div>}
    </div>
  );
}

export function Checks({ checks, compact = false }: { checks: Check[]; compact?: boolean }) {
  if (!checks?.length) return null;
  const failed = checks.filter((c) => !c.passed);
  return (
    <div className={compact ? "flex flex-wrap gap-1.5" : "grid grid-cols-1 md:grid-cols-2 gap-x-6 gap-y-1"}>
      {checks.map((c) => (
        <div key={c.name} className={`flex items-start gap-2 text-[12.5px] ${compact ? "chip " + (c.passed ? "chip-ok" : "chip-danger") : ""}`} title={c.detail}>
          {!compact && <span className={`mt-[5px] inline-block w-[7px] h-[7px] rounded-full ${c.passed ? "bg-ok" : "bg-danger"}`} />}
          <span className={c.passed ? "text-ink-2" : "text-danger font-medium"}>{c.name.replaceAll("_", " ")}</span>
          {!compact && c.detail && <span className="text-muted num truncate">{c.detail}</span>}
        </div>
      ))}
      {!compact && (
        <div className="col-span-full text-[12px] text-muted mt-1">
          {checks.length - failed.length}/{checks.length} passed
        </div>
      )}
    </div>
  );
}

export function Empty({ title, hint, action }: { title: string; hint?: string; action?: ReactNode }) {
  return (
    <div className="text-center py-14 px-6">
      <div className="mx-auto w-10 h-10 rounded-full border border-line-strong flex items-center justify-center mb-4">
        <span className="w-3 h-3 rounded-full bg-accent" />
      </div>
      <div className="display text-[20px]">{title}</div>
      {hint && <div className="text-[13px] text-muted mt-1 max-w-md mx-auto">{hint}</div>}
      {action && <div className="mt-4">{action}</div>}
    </div>
  );
}

export function Spinner({ className = "" }: { className?: string }) {
  return <span className={`inline-block w-3.5 h-3.5 rounded-full border-2 border-line-strong border-t-accent animate-spin ${className}`} />;
}

export function Field({ label, children, hint }: { label: string; children: ReactNode; hint?: string }) {
  return (
    <label className="block">
      <div className="text-[11.5px] uppercase tracking-[0.14em] text-muted mb-1.5">{label}</div>
      {children}
      {hint && <div className="text-[11.5px] text-muted mt-1">{hint}</div>}
    </label>
  );
}

export function Toggle({ on, onChange, label }: { on: boolean; onChange: (v: boolean) => void; label?: string }) {
  return (
    <button type="button" onClick={() => onChange(!on)} className="flex items-center gap-3 group text-left rounded-md" aria-pressed={on}>
      <span className={`relative inline-block w-10 h-6 rounded-full transition-colors duration-200 ${on ? "bg-accent" : "bg-paper-3 border border-line-strong"}`}>
        <motion.span layout className="absolute top-[3px] w-[18px] h-[18px] rounded-full bg-paper shadow" style={{ left: on ? 19 : 3 }} transition={{ type: "spring", stiffness: 500, damping: 32 }} />
      </span>
      {label && <span className="text-[13.5px] text-ink-2 group-hover:text-ink">{label}</span>}
    </button>
  );
}

export function Money({ paise, className = "" }: { paise: number; className?: string }) {
  const v = paise / 100;
  const frac = Math.abs(v % 1) > 1e-9;
  return <span className={`num ${className}`}>₹{v.toLocaleString("en-IN", { minimumFractionDigits: frac ? 2 : 0, maximumFractionDigits: frac ? 2 : 0 })}</span>;
}

export function Hash({ v }: { v: string }) {
  return (
    <span className="mono text-[11.5px] text-muted" title={v}>
      {v.slice(0, 10)}…
    </span>
  );
}

export class ErrorBoundary extends Component<{ children: ReactNode }, { error: Error | null }> {
  state = { error: null as Error | null };
  static getDerivedStateFromError(error: Error) {
    return { error };
  }
  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error("[bazaar-console]", error, info.componentStack);
  }
  componentDidUpdate(prev: { children: ReactNode }) {
    if (prev.children !== this.props.children && this.state.error) this.setState({ error: null });
  }
  render() {
    if (this.state.error) {
      return (
        <div className="max-w-[720px] mx-auto px-6 py-16">
          <div className="text-[11.5px] uppercase tracking-[0.18em] text-danger mb-2">something broke on this page</div>
          <h1 className="text-[30px] mb-3">The rest of the console still works.</h1>
          <pre className="mono text-[12px] text-ink-2 whitespace-pre-wrap card p-4">{String(this.state.error?.message ?? this.state.error)}</pre>
        </div>
      );
    }
    return this.props.children;
  }
}

import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, type AuditEntry } from "../api";
import { Card, Chip, Empty, Page, Stat } from "../components/ui";
import { useStore } from "../store";

export default function Audit() {
  const { merchantId } = useStore();
  const [d, setD] = useState<{ chain_ok: boolean; first_bad_seq: number; merkle_root: string; entries: AuditEntry[] } | null>(null);
  const [kind, setKind] = useState("all");
  useEffect(() => {
    if (!merchantId) return;
    const load = () => api.audit(merchantId).then(setD).catch(() => {});
    load();
    const t = setInterval(load, 6000);
    return () => clearInterval(t);
  }, [merchantId]);
  const kinds = ["all", ...Array.from(new Set(d?.entries.map((e) => e.kind) ?? []))];
  const rows = (d?.entries ?? []).filter((e) => kind === "all" || e.kind === kind).slice().reverse();
  return (
    <Page kicker="audit" title="Tamper-evident by construction">
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4 mb-6">
        <Stat label="Chain" value={d ? (d.chain_ok ? "intact" : `broken @${d.first_bad_seq}`) : "—"} sub="SHA-256 hash-chained JSONL" />
        <Stat label="Merkle root" value={<span className="mono text-[16px]">{d?.merkle_root.slice(0, 12) ?? "—"}…</span>} sub="anchor daily to an immutable store" />
        <Stat label="Entries here" value={d?.entries.length ?? "—"} sub="this merchant's sessions and console actions" />
        <Stat label="Money events" value={d?.entries.filter((e) => e.kind === "money").length ?? "—"} accent sub="payment links, captures, refunds" />
      </div>
      <Card pad={false} title="Entries" aside={
        <div className="flex gap-1 flex-wrap justify-end">
          {kinds.map((k) => <button key={k} className={`chip ${kind === k ? "chip-accent" : ""}`} onClick={() => setKind(k)}>{k}</button>)}
        </div>
      }>
        {rows.length === 0 ? (
          <Empty title="Nothing recorded yet" />
        ) : (
          <div className="overflow-auto max-h-[65vh]">
            <table className="w-full text-[12.5px]">
              <thead className="text-[11px] uppercase tracking-[0.12em] text-muted sticky top-0 thead-bg">
                <tr>
                  <th className="text-left font-medium px-5 py-2">#</th>
                  <th className="text-left font-medium px-2 py-2">When</th>
                  <th className="text-left font-medium px-2 py-2">Kind</th>
                  <th className="text-left font-medium px-2 py-2">Action</th>
                  <th className="text-left font-medium px-2 py-2">Outcome</th>
                  <th className="text-left font-medium px-2 py-2">Detail</th>
                  <th className="text-left font-medium px-5 py-2">Hash</th>
                </tr>
              </thead>
              <tbody className="divide-y hairline">
                {rows.map((e) => (
                  <tr key={e.audit_id} className="row-hover align-top">
                    <td className="px-5 py-2 num text-muted">{e.seq}</td>
                    <td className="px-2 py-2 num text-muted whitespace-nowrap">{new Date(e.at).toLocaleTimeString()}</td>
                    <td className="px-2 py-2"><Chip kind={e.kind === "money" ? "accent" : ""}>{e.kind}</Chip></td>
                    <td className="px-2 py-2">{(e.action ?? "").replaceAll("_", " ")}</td>
                    <td className={`px-2 py-2 ${e.outcome === "declined" || e.outcome === "failed" ? "text-danger" : "text-ink-2"}`}>{e.outcome ?? ""}</td>
                    <td className="px-2 py-2 text-ink-2 max-w-[360px]">
                      {e.money && Object.keys(e.money).length > 0 && <div className="mono text-[11px]">{Object.entries(e.money).map(([k, v]) => `${k}=${String(v)}`).join("  ")}</div>}
                      {e.note}
                      {e.session && <Link to={`/sessions?s=${e.session}`} className="ml-2 mono text-[11px] text-accent">{e.session}</Link>}
                    </td>
                    <td className="px-5 py-2 mono text-[11px] text-muted">{(e.hash ?? "").slice(0, 12)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </Page>
  );
}

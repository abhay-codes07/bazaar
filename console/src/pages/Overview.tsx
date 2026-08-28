import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, rupees, type MerchantDetail, type Stats } from "../api";
import { Bar, Card, Chip, Empty, Page, Ring, Stat, StatusChip } from "../components/ui";
import { useStore } from "../store";

const LABELS: Record<string, string> = { prices_trusted: "Prices trusted", units_clear: "Units clear", stock_known: "Stock known", gst_known: "GST known", serviceability: "Serviceability", offer_rules: "Offer rules", descriptions: "Descriptions", clean_text: "Clean text" };
const MAX: Record<string, number> = { prices_trusted: 25, units_clear: 15, stock_known: 10, gst_known: 10, serviceability: 15, offer_rules: 10, descriptions: 10, clean_text: 5 };

export default function Overview() {
  const { merchantId, merchants } = useStore();
  const [d, setD] = useState<MerchantDetail | null>(null);
  const [stats, setStats] = useState<Stats | null>(null);
  useEffect(() => {
    if (!merchantId) return;
    let alive = true;
    const load = () => {
      api.merchant(merchantId).then((x) => alive && setD(x)).catch(() => {});
      api.stats().then((x) => alive && setStats(x)).catch(() => {});
    };
    load();
    const t = setInterval(load, 6000);
    return () => {
      alive = false;
      clearInterval(t);
    };
  }, [merchantId]);
  const m = d?.merchant;
  const mine = d?.sessions ?? [];
  const completed = mine.filter((s) => s.status === "completed");
  const gmv = completed.reduce((a, s) => a + (s.quote?.total_paise ?? 0), 0);
  const row = merchants.find((x) => x.merchant_id === merchantId);

  return (
    <Page kicker={m ? `${m.vertical.replaceAll("_", " ")} · ${m.city} · ${m.base_pincode}` : "loading"} title={m?.name ?? "…"} actions={row?.kill_switch ? <Chip kind="danger">agent disabled</Chip> : <Chip kind="ok">agent live</Chip>}>
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4 mb-6">
        <Stat label="Agent orders" value={completed.length} sub={`${mine.length} sessions with agents`} />
        <Stat label="Agent GMV" value={rupees(gmv)} accent sub="captured via Razorpay" />
        <Stat label="Network" value={stats ? `${stats.completed}/${stats.sessions}` : "—"} sub={stats ? `${stats.merchants} merchants · ${stats.agents} agents · ${rupees(stats.gmv_paise)}` : ""} />
        <Stat label="Audit chain" value={stats ? (stats.chain_ok ? "intact" : "BROKEN") : "—"} sub={stats ? `${stats.audit_entries} hash-chained entries · ${stats.ledger.inconsistencies} fairness inconsistencies` : ""} />
      </div>
      <div className="grid lg:grid-cols-[360px_minmax(0,1fr)] gap-4">
        <Card title="Agent-readiness">
          {d ? (
            <div>
              <div className="flex items-center gap-5">
                <Ring value={d.readiness.score} label="score" />
                <div className="text-[13px] text-ink-2">
                  <div className="display text-[18px] text-ink mb-1">{d.readiness.score >= 80 ? "Ready for agents" : d.readiness.score >= 60 ? "Almost there" : "Needs work"}</div>
                  {d.products} products · {m?.offer_rules.length ?? 0} offer rules · {m?.serviceability.pincode_prefixes.join(", ")}xx
                </div>
              </div>
              <div className="mt-4">
                {Object.entries(d.readiness.components).map(([k, v]) => (
                  <Bar key={k} label={LABELS[k] ?? k} value={v} max={MAX[k] ?? v} />
                ))}
              </div>
              {d.readiness.fixes.length > 0 && (
                <div className="mt-4 border-t hairline pt-3">
                  <div className="text-[11.5px] uppercase tracking-[0.14em] text-muted mb-2">Fix next</div>
                  <ul className="text-[13px] space-y-1.5">
                    {d.readiness.fixes.map((f) => (
                      <li key={f} className="flex gap-2">
                        <span className="text-accent">→</span>
                        <span>{f}</span>
                      </li>
                    ))}
                  </ul>
                </div>
              )}
            </div>
          ) : (
            <Empty title="Loading" />
          )}
        </Card>
        <div className="flex flex-col gap-4 min-w-0">
          <Card title="Endpoints agents use" aside={<span className="text-[12px] text-muted">one compile, every protocol</span>}>
            <div className="grid sm:grid-cols-2 gap-2 text-[13px]">
              {[
                ["Bazaar manifest", `/bazaar/v1/merchants/${merchantId}/manifest`],
                ["UCP profile", `/bazaar/v1/merchants/${merchantId}/ucp`],
                ["ACP product feed", `/bazaar/v1/merchants/${merchantId}/acp/feed`],
                ["llms.txt", `/bazaar/v1/merchants/${merchantId}/llms.txt`],
                ["MCP server", `/mcp/${merchantId}`],
                ["All exports", `/bazaar/v1/merchants/${merchantId}/exports`],
              ].map(([l, u]) => (
                <a key={u} href={u} target="_blank" rel="noreferrer" className="row-hover rounded-md border hairline px-3 py-2 flex items-center justify-between gap-3 group min-w-0">
                  <span>{l}</span>
                  <span className="mono text-[11px] text-muted group-hover:text-accent truncate min-w-0">{u}</span>
                </a>
              ))}
            </div>
          </Card>
          <Card title="Recent agent sessions" aside={<Link to="/sessions" className="text-[12.5px] text-accent">all sessions →</Link>} pad={false}>
            {mine.length === 0 ? (
              <Empty title="No agent sessions yet" hint="Open the Playground to let a demo buyer agent talk to this merchant." action={<Link to="/playground" className="btn btn-primary">Open playground</Link>} />
            ) : (
              <div className="divide-y hairline">
                {mine
                  .slice(-6)
                  .reverse()
                  .map((s) => (
                    <Link key={s.session_id} to={`/sessions?s=${s.session_id}`} className="row-hover flex items-center gap-3 px-5 py-3 text-[13px]">
                      <span className="mono text-[11.5px] text-muted w-[130px] truncate">{s.session_id}</span>
                      <StatusChip status={s.status} />
                      <span className="text-ink-2 truncate flex-1 min-w-0">{s.turns.at(-1)?.explanation ?? ""}</span>
                      <span className="num">{s.quote ? rupees(s.quote.total_paise) : ""}</span>
                    </Link>
                  ))}
              </div>
            )}
          </Card>
        </div>
      </div>
    </Page>
  );
}

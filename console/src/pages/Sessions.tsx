import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api, rupees, type Session, type Timeline } from "../api";
import { Card, Checks, Empty, Hash, Page, StatusChip } from "../components/ui";
import { useStore } from "../store";

export default function Sessions() {
  const { merchantId, toast } = useStore();
  const [list, setList] = useState<Session[]>([]);
  const [params, setParams] = useSearchParams();
  const sel = params.get("s") ?? "";
  const [s, setS] = useState<Session | null>(null);
  const [tl, setTl] = useState<{ chain_ok: boolean; timeline: Timeline[] } | null>(null);

  const load = () => {
    if (!merchantId) return;
    api.merchant(merchantId).then((d) => setList(d.sessions.slice().reverse())).catch(() => {});
  };
  useEffect(() => {
    load();
    const t = setInterval(load, 5000);
    return () => clearInterval(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [merchantId]);

  useEffect(() => {
    if (!sel) {
      setS(null);
      setTl(null);
      return;
    }
    api.session(sel).then(setS).catch(() => setS(null));
    api.replay(sel).then(setTl).catch(() => setTl(null));
  }, [sel, list]);

  const approve = async () => {
    if (!s) return;
    try {
      await api.approve(merchantId, s.session_id);
      toast("Approved — payment link issued to the buyer agent", "ok");
      load();
    } catch (e) {
      toast((e as Error).message, "danger");
    }
  };

  return (
    <Page kicker="agent sessions" title="Every conversation, every rupee, replayable">
      <div className="grid lg:grid-cols-[minmax(0,1fr)_minmax(0,1.2fr)] gap-4">
        <Card pad={false} title={`${list.length} sessions`}>
          {list.length === 0 ? (
            <Empty title="No sessions yet" hint="Buyer agents show up here the moment they talk to your Seller Agent." />
          ) : (
            <div className="divide-y hairline max-h-[70vh] overflow-auto">
              {list.map((x) => (
                <button key={x.session_id} onClick={() => setParams({ s: x.session_id })} className={`row-hover w-full text-left px-5 py-3 flex items-center gap-2.5 text-[13px] ${sel === x.session_id ? "bg-paper-2" : ""}`}>
                  <span className="mono text-[11.5px] text-muted w-[96px] shrink-0 truncate">{x.session_id}</span>
                  <StatusChip status={x.status} />
                  <span className="text-muted text-[11.5px] whitespace-nowrap truncate min-w-0">T{x.tier} · {x.source}</span>
                  <span className="ml-auto num shrink-0">{x.quote ? rupees(x.quote.total_paise) : "—"}</span>
                </button>
              ))}
            </div>
          )}
        </Card>
        <div key={sel || "none"} className="min-w-0">
          {s ? (
            <div className="flex flex-col gap-4">
              <Card title={<span className="mono text-[14px]">{s.session_id}</span>} aside={
                <div className="flex items-center gap-2">
                  <StatusChip status={s.status} />
                  {s.status === "awaiting_merchant_review" && <button className="btn btn-primary h-8" onClick={approve}>Approve & issue payment link</button>}
                  {s.status === "in_progress" && s.source === "playground" && <button className="btn h-8" onClick={() => api.pgPay(s.session_id).then(() => { toast("Buyer paid (sandbox) — webhook captured", "ok"); load(); }).catch((e) => toast((e as Error).message, "danger"))}>Simulate buyer payment</button>}
                </div>
              }>
                <div className="text-[12.5px] text-muted mb-3">agent <span className="mono">{s.agent_keyid || "unsigned"}</span> · tier {s.tier} · segment {s.segment} · {s.language}</div>
                <div className="space-y-2">
                  {s.turns.map((t, i) => (
                    <div key={i} className={`rounded-lg px-3 py-2 text-[13px] border ${t.ok ? "hairline" : "border-danger/40 bg-danger-soft"}`}>
                      <div className="flex items-center gap-2 text-[11px] uppercase tracking-[0.12em] text-muted mb-1">
                        <span className={t.ok ? "text-ok" : "text-danger"}>{t.action.replaceAll("_", " ")}</span>
                        <span className="mono normal-case tracking-normal">{t.audit_id}</span>
                      </div>
                      {t.explanation}
                    </div>
                  ))}
                </div>
              </Card>
              {s.quote && (
                <Card title="Quote">
                  <table className="w-full text-[13px]">
                    <tbody className="divide-y hairline">
                      {s.quote.lines.map((l) => (
                        <tr key={l.sku}>
                          <td className="py-1.5">{l.qty} × {l.name} <span className="text-muted">({l.pack_size} {l.unit})</span></td>
                          <td className="py-1.5 text-right num">{rupees(l.subtotal_paise)}</td>
                        </tr>
                      ))}
                      {s.quote.applied_offers.map((o) => (
                        <tr key={o.rule_id} className="text-ok">
                          <td className="py-1.5">offer <span className="mono">{o.rule_id}</span> <Hash v={o.inputs_hash} /></td>
                          <td className="py-1.5 text-right num">−{rupees(o.discount_paise)}</td>
                        </tr>
                      ))}
                      <tr><td className="py-1.5 text-ink-2">GST</td><td className="py-1.5 text-right num">{rupees(s.quote.gst_paise)}</td></tr>
                      <tr><td className="py-1.5 text-ink-2">Delivery to {s.quote.pincode || "—"} · ~{s.quote.eta_hours} h</td><td className="py-1.5 text-right num">{rupees(s.quote.delivery_fee_paise)}</td></tr>
                      <tr className="display text-[18px]"><td className="pt-2">Total</td><td className="pt-2 text-right num">{rupees(s.quote.total_paise)}</td></tr>
                    </tbody>
                  </table>
                  {s.payment_url && <div className="mt-3 text-[12.5px]">Razorpay order <span className="mono">{s.order_id}</span> · <a className="text-accent" href={s.payment_url} target="_blank" rel="noreferrer">payment link</a>{s.payment_id && <span> · paid <span className="mono">{s.payment_id}</span></span>}</div>}
                </Card>
              )}
              {s.last_checks.length > 0 && (
                <Card title="Checkout policy checks">
                  <Checks checks={s.last_checks} />
                </Card>
              )}
              {tl && (
                <Card title="Replay" aside={<span className={`chip ${tl.chain_ok ? "chip-ok" : "chip-danger"}`}>{tl.chain_ok ? "chain intact" : "chain broken"}</span>}>
                  <ol className="relative border-l hairline ml-2 space-y-3">
                    {tl.timeline.map((e) => (
                      <li key={e.audit_id} className="pl-4">
                        <span className={`absolute -left-[5px] mt-[6px] w-[9px] h-[9px] rounded-full ${e.kind === "money" ? "bg-accent" : e.outcome === "declined" || e.checks_failed.length ? "bg-danger" : "bg-ok"}`} />
                        <div className="text-[12.5px] flex flex-wrap items-center gap-x-2">
                          <span className="font-medium">{e.action.replaceAll("_", " ")}</span>
                          <span className="text-muted">{e.outcome}</span>
                          {e.checks_failed.length > 0 && <span className="text-danger">failed: {e.checks_failed.join(", ")}</span>}
                          {e.money && Object.keys(e.money).length > 0 && <span className="mono text-[11px] text-ink-2">{Object.entries(e.money).map(([k, v]) => `${k}=${String(v)}`).join(" ")}</span>}
                          <span className="mono text-[11px] text-muted">#{e.seq} {e.hash}</span>
                        </div>
                        {e.note && <div className="text-[12px] text-muted">{e.note}</div>}
                      </li>
                    ))}
                  </ol>
                </Card>
              )}
            </div>
          ) : (
            <Card>
              <Empty title="Select a session" hint="See the conversation, the itemised quote, every policy check and the hash-chained replay." />
            </Card>
          )}
        </div>
      </div>
    </Page>
  );
}

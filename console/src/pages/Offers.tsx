import { useEffect, useState } from "react";
import { api, rupees, type Fairness, type MerchantDetail, type OfferRule, type Policy } from "../api";
import { Card, Chip, Field, Page, Spinner, Toggle } from "../components/ui";
import { useStore } from "../store";

const NEW_RULE = (): OfferRule => ({ rule_id: "NEW_RULE", version: 1, type: "percent", value: 5, min_cart_paise: 0, min_qty: 0, segment: "any", max_discount_paise: 0, stackable: false, valid_until: null, description: "" });

export default function Offers() {
  const { merchantId, toast, refreshMerchants } = useStore();
  const [d, setD] = useState<MerchantDetail | null>(null);
  const [rules, setRules] = useState<OfferRule[]>([]);
  const [policy, setPolicy] = useState<Policy | null>(null);
  const [fair, setFair] = useState<Fairness | null>(null);
  const [busy, setBusy] = useState<string>("");

  const load = () => {
    if (!merchantId) return;
    api.merchant(merchantId).then((x) => {
      setD(x);
      setRules(x.merchant.offer_rules);
      setPolicy(x.merchant.policy);
    });
    api.fairness(merchantId).then(setFair).catch(() => {});
  };
  useEffect(load, [merchantId]);

  const publishRules = async () => {
    setBusy("rules");
    try {
      const r = await api.putRules(merchantId, rules);
      setFair({ ...r.fairness, ledger: fair?.ledger ?? { entries: 0, distinct_rules: 0, inconsistencies: 0 } });
      toast(`Rules published · fairness audit passed on ${r.fairness.cohorts.toLocaleString()} cohorts`, "ok");
      void refreshMerchants();
    } catch (e) {
      const err = e as Error & { data?: { detail?: { findings?: { rule_id: string; kind: string; detail: string }[] } } };
      const f = err.data?.detail?.findings;
      toast(f ? `Blocked by fairness audit: ${f.map((x) => `${x.rule_id} ${x.kind}`).join(", ")}` : err.message, "danger");
    } finally {
      setBusy("");
    }
  };

  const savePolicy = async () => {
    if (!policy) return;
    setBusy("policy");
    try {
      await api.putPolicy(merchantId, policy);
      toast("Policy saved", "ok");
      void refreshMerchants();
    } catch (e) {
      toast((e as Error).message, "danger");
    } finally {
      setBusy("");
    }
  };

  const kill = async (on: boolean) => {
    await api.killSwitch(merchantId, on);
    setPolicy((p) => p && { ...p, kill_switch: on });
    toast(on ? "Agent disabled. Nothing will be quoted or charged." : "Agent re-enabled.", on ? "danger" : "ok");
    void refreshMerchants();
  };

  const upd = (i: number, patch: Partial<OfferRule>) => setRules((rs) => rs.map((r, j) => (j === i ? { ...r, ...patch } : r)));

  return (
    <Page kicker="offers & policy" title="Bounded, provable, yours" actions={policy ? <button className={`btn ${policy.kill_switch ? "btn-primary" : ""}`} onClick={() => kill(!policy.kill_switch)}>{policy.kill_switch ? "Re-enable agent" : "Kill switch"}</button> : null}>
      <div className="grid lg:grid-cols-[1fr_380px] gap-4">
        <Card title="Pre-approved offer rules" aside={
          <div className="flex items-center gap-2">
            {fair && <Chip kind={fair.passed ? "ok" : "danger"}>{fair.passed ? `fair · ${fair.cohorts.toLocaleString()} cohorts` : `${fair.findings.length} finding(s)`}</Chip>}
            <button className="btn h-8" onClick={() => setRules((r) => [...r, NEW_RULE()])}>Add rule</button>
            <button className="btn btn-primary h-8" onClick={publishRules} disabled={busy === "rules"}>{busy === "rules" ? <Spinner /> : null} Publish</button>
          </div>
        }>
          <p className="text-[13px] text-ink-2 mb-4">The agent can only <em>select</em> one of these by id. It never sets a price or invents a discount. Every publish runs a fairness audit: identical carts in the same segment must get identical outcomes regardless of agent, language, time or buyer.</p>
          <div className="space-y-3">
            {rules.map((r, i) => (
              <div key={i} className="rounded-lg border hairline p-3 grid grid-cols-2 sm:grid-cols-3 xl:grid-cols-[1.4fr_1fr_0.8fr_0.9fr_1fr_1.1fr] gap-3 items-end">
                <Field label="Rule id"><input className="input h-8 mono text-[12px]" value={r.rule_id} onChange={(e) => upd(i, { rule_id: e.target.value.toUpperCase().replace(/\s+/g, "_") })} /></Field>
                <Field label="Type">
                  <select className="input h-8" value={r.type} onChange={(e) => upd(i, { type: e.target.value as OfferRule["type"] })}>
                    <option value="percent">percent</option>
                    <option value="flat">flat ₹</option>
                    <option value="free_delivery">free delivery</option>
                  </select>
                </Field>
                <Field label={r.type === "flat" ? "₹ off" : r.type === "percent" ? "% off" : "—"}>
                  <input className="input h-8 num" type="number" disabled={r.type === "free_delivery"} value={r.type === "flat" ? r.value / 100 : r.value} onChange={(e) => upd(i, { value: r.type === "flat" ? Math.round(Number(e.target.value) * 100) : Number(e.target.value) })} />
                </Field>
                <Field label="Min cart ₹"><input className="input h-8 num" type="number" value={r.min_cart_paise / 100} onChange={(e) => upd(i, { min_cart_paise: Math.round(Number(e.target.value) * 100) })} /></Field>
                <Field label="Segment">
                  <select className="input h-8" value={r.segment} onChange={(e) => upd(i, { segment: e.target.value })}>
                    {["any", "new", "returning", "b2b"].map((s) => <option key={s}>{s}</option>)}
                  </select>
                </Field>
                <div className="flex items-center gap-2">
                  <Field label="Cap ₹"><input className="input h-8 num" type="number" value={r.max_discount_paise / 100} onChange={(e) => upd(i, { max_discount_paise: Math.round(Number(e.target.value) * 100) })} /></Field>
                  <button className="btn btn-quiet h-8 text-danger" title="Remove" onClick={() => setRules((rs) => rs.filter((_, j) => j !== i))}>×</button>
                </div>
                <div className="col-span-full">
                  <input className="input h-8" placeholder="Description buyers may see (no urgency, no invented claims)" value={r.description} onChange={(e) => upd(i, { description: e.target.value })} />
                </div>
              </div>
            ))}
          </div>
          {fair && fair.findings.length > 0 && (
            <div className="mt-4 rounded-md bg-danger-soft p-3 text-[12.5px] text-danger">
              {fair.findings.map((f, i) => <div key={i}>{f.rule_id}: {f.kind} — {f.detail}</div>)}
            </div>
          )}
        </Card>
        <div className="flex flex-col gap-4">
          <Card title="Merchant policy" aside={<button className="btn btn-primary h-8" onClick={savePolicy} disabled={busy === "policy" || !policy}>{busy === "policy" ? <Spinner /> : null} Save</button>}>
            {policy && (
              <div className="space-y-4">
                <Toggle on={policy.review_first} onChange={(v) => setPolicy({ ...policy, review_first: v })} label="Review-first: I approve every checkout before a payment link is issued" />
                <Field label="Minimum agent tier for checkout" hint="T0 unsigned · T1 signed · T2 verified operator · T3 Razorpay-vetted">
                  <select className="input" value={policy.min_tier_for_checkout} onChange={(e) => setPolicy({ ...policy, min_tier_for_checkout: Number(e.target.value) })}>
                    {[1, 2, 3].map((t) => <option key={t} value={t}>T{t}</option>)}
                  </select>
                </Field>
                <div className="grid grid-cols-2 gap-3">
                  <Field label="Negotiation rounds"><input className="input num" type="number" min={0} max={5} value={policy.max_negotiation_rounds} onChange={(e) => setPolicy({ ...policy, max_negotiation_rounds: Number(e.target.value) })} /></Field>
                  <Field label="Max order ₹"><input className="input num" type="number" value={policy.max_order_paise / 100} onChange={(e) => setPolicy({ ...policy, max_order_paise: Math.round(Number(e.target.value) * 100) })} /></Field>
                  <Field label="Refunds / hour"><input className="input num" type="number" value={policy.refunds_per_hour} onChange={(e) => setPolicy({ ...policy, refunds_per_hour: Number(e.target.value) })} /></Field>
                  <Field label="Agent allowlist" hint="empty = tier rules only"><input className="input mono text-[12px]" placeholder="ak_… ak_…" value={policy.agent_allowlist.join(" ")} onChange={(e) => setPolicy({ ...policy, agent_allowlist: e.target.value.split(/\s+/).filter(Boolean) })} /></Field>
                </div>
              </div>
            )}
          </Card>
          <Card title="Delivery & tax">
            {d && (
              <div className="text-[13px] space-y-1.5 text-ink-2">
                <div>Serves pincodes <span className="mono text-ink">{d.merchant.serviceability.pincode_prefixes.map((p) => p + "xx").join(", ")}</span></div>
                <div>ETA ~{d.merchant.serviceability.eta_hours} h · fee {rupees(d.merchant.serviceability.delivery_fee_paise)}{d.merchant.serviceability.free_delivery_above_paise ? ` · free above ${rupees(d.merchant.serviceability.free_delivery_above_paise)}` : ""}</div>
                <div>COD {d.merchant.serviceability.cod_allowed ? "allowed" : "not allowed"} · GSTIN {d.merchant.gstin || "unregistered"}</div>
              </div>
            )}
          </Card>
        </div>
      </div>
    </Page>
  );
}

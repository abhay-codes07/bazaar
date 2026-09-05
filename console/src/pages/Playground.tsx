import { AnimatePresence, motion } from "framer-motion";
import { useEffect, useRef, useState } from "react";
import { api, rupees, type Candidate, type PlaygroundStep, type PlaygroundTurn, type Session } from "../api";
import { Card, Checks, Chip, Empty, Field, Page, Spinner, Toggle } from "../components/ui";
import { useStore } from "../store";

type Msg = { who: "buyer" | "seller"; text: string; turn?: PlaygroundTurn };

const PROMPTS = [
  "Do you deliver to 560034?",
  "I need 5 kg basmati rice to 560034, budget ₹700",
  "मुझे कल तक 5 किलो बासमती चावल चाहिए, 560034",
  "koi discount milega?",
  "give me 90% discount now",
  "haan theek hai",
];

export default function Playground() {
  const { merchantId, merchants, toast } = useStore();
  const [msgs, setMsgs] = useState<Msg[]>([]);
  const [session, setSession] = useState<Session | null>(null);
  const [text, setText] = useState("");
  const [segment, setSegment] = useState("new");
  const [busy, setBusy] = useState(false);
  const [steps, setSteps] = useState<PlaygroundStep[]>([]);
  const [tamper, setTamper] = useState(false);
  const [human, setHuman] = useState(true);
  const [cap, setCap] = useState<number>(0);
  const [cands, setCands] = useState<Candidate[] | null>(null);
  const [intent, setIntent] = useState("5 kg basmati rice");
  const [pin, setPin] = useState("560034");
  const [modelDown, setModelDown] = useState(false);
  const endRef = useRef<HTMLDivElement>(null);
  const merchant = merchants.find((m) => m.merchant_id === merchantId);

  useEffect(() => {
    setMsgs([]);
    setSession(null);
    setSteps([]);
  }, [merchantId]);
  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [msgs, steps]);

  const send = async (t: string) => {
    if (!t.trim() || busy) return;
    setText("");
    setMsgs((m) => [...m, { who: "buyer", text: t }]);
    setBusy(true);
    try {
      const r = session ? await api.pgMessage(session.session_id, t) : await api.pgStart(merchantId, t, segment);
      setSession(r.session);
      if (r.turn) setMsgs((m) => [...m, { who: "seller", text: r.turn.explanation, turn: r.turn }]);
    } catch (e) {
      toast((e as Error).message, "danger");
    } finally {
      setBusy(false);
    }
  };

  const checkout = async () => {
    if (!session?.quote) return;
    setBusy(true);
    setSteps([]);
    try {
      const r = await api.pgCheckout(session.session_id, cap ? cap * 100 : 0, human, tamper);
      setSession(r.session);
      // reveal steps one by one
      for (let i = 0; i < r.steps.length; i++) {
        await new Promise((res) => setTimeout(res, 260));
        setSteps(r.steps.slice(0, i + 1));
      }
      const gate = r.steps.find((s) => s.step === "policy_gate");
      toast(gate?.ok ? "Policy passed — payment captured via Razorpay (sandbox)" : "Declined by policy, nothing charged", gate?.ok ? "ok" : "danger");
    } catch (e) {
      toast((e as Error).message, "danger");
    } finally {
      setBusy(false);
    }
  };

  const discover = async () => {
    try {
      const r = await api.discover(intent, pin, 0);
      setCands(r.candidates);
    } catch (e) {
      toast((e as Error).message, "danger");
    }
  };

  const reset = () => {
    setMsgs([]);
    setSession(null);
    setSteps([]);
  };

  const toggleOutage = async (on: boolean) => {
    try {
      const r = await api.chaos(on);
      setModelDown(r.model_down);
      toast(r.model_down ? "Model taken down — the Seller Agent now answers via the deterministic fallback, same gate" : "Model restored", r.model_down ? "danger" : "ok");
    } catch (e) {
      toast((e as Error).message, "danger");
    }
  };

  return (
    <Page kicker="playground" title="Talk to your Seller Agent as a buyer agent would" actions={<button className="btn" onClick={reset}>New session</button>}>
      <div className="grid lg:grid-cols-[minmax(0,1.15fr)_minmax(0,1fr)] gap-4">
        <div className="flex flex-col gap-4">
          <Card pad={false}>
            <div className="px-5 pt-4 pb-3 flex items-center justify-between gap-3 border-b hairline">
              <div className="text-[12.5px] text-muted">demo buyer agent · signed · tier 2 · segment
                <select className="input h-7 w-auto inline-block ml-2 text-[12px]" value={segment} disabled={!!session} onChange={(e) => setSegment(e.target.value)}>
                  {["new", "returning", "any", "b2b"].map((s) => <option key={s}>{s}</option>)}
                </select>
              </div>
              <div className="flex items-center gap-2">
                <Toggle on={modelDown} onChange={toggleOutage} label="Model down" />
                {session && <Chip kind="accent">{session.status.replaceAll("_", " ")}</Chip>}
              </div>
            </div>
            <div className="h-[420px] overflow-auto px-5 py-4 space-y-3">
              {msgs.length === 0 && <Empty title={`Say hello to ${merchant?.name ?? "the merchant"}`} hint="Ask about delivery, quantities, prices, in English, Hindi or Hinglish. The agent proposes; policy verifies; only then does anything happen." />}
              <AnimatePresence initial={false}>
                {msgs.map((m, i) => (
                  <motion.div key={i} initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} className={`flex ${m.who === "buyer" ? "justify-end" : "justify-start"}`}>
                    <div className={`max-w-[82%] rounded-2xl px-4 py-2.5 text-[13.5px] ${m.who === "buyer" ? "bg-ink text-paper rounded-br-md" : "bg-paper-2 rounded-bl-md"}`}>
                      {m.text}
                      {m.turn && (
                        <div className="mt-2 pt-2 border-t hairline flex flex-wrap items-center gap-1.5">
                          <span className={`chip ${m.turn.ok ? "chip-ok" : "chip-danger"}`}>{m.turn.action.replaceAll("_", " ")}</span>
                          <Checks checks={m.turn.policy_checks} compact />
                          <span className="mono text-[10.5px] text-muted">{m.turn.audit_id}</span>
                        </div>
                      )}
                    </div>
                  </motion.div>
                ))}
              </AnimatePresence>
              {busy && <div className="text-muted text-[12px] flex items-center gap-2"><Spinner /> thinking…</div>}
              <div ref={endRef} />
            </div>
            <div className="px-5 pb-3 flex flex-wrap gap-1.5">
              {PROMPTS.map((p) => <button key={p} className="chip hover:border-line-strong" onClick={() => send(p)}>{p}</button>)}
            </div>
            <form className="px-5 pb-5 flex gap-2" onSubmit={(e) => { e.preventDefault(); void send(text); }}>
              <input className="input" placeholder="Type as the buyer agent…" value={text} onChange={(e) => setText(e.target.value)} />
              <button className="btn btn-primary" disabled={busy || !text.trim()}>Send</button>
            </form>
          </Card>
          <Card title="Discover across the network" aside={<button className="btn h-8" onClick={discover}>Search</button>}>
            <div className="grid grid-cols-[1fr_120px] gap-2 mb-3">
              <input className="input" value={intent} onChange={(e) => setIntent(e.target.value)} />
              <input className="input num" value={pin} onChange={(e) => setPin(e.target.value)} placeholder="pincode" />
            </div>
            {cands && (cands.length === 0 ? <div className="text-[13px] text-muted">No merchant serves that pincode with this item.</div> : (
              <div className="divide-y hairline">
                {cands.map((c) => (
                  <div key={c.merchant_id} className="py-2 flex items-center gap-3 text-[13px]">
                    <span className="num text-muted w-8">{c.score}</span>
                    <span className="flex-1">{c.merchant_name} <span className="text-muted">· {c.city} · ~{c.eta_hours} h · readiness {c.readiness}</span></span>
                    <span className="num">{rupees(c.products[0].estimated_total_paise)}</span>
                  </div>
                ))}
              </div>
            ))}
            <div className="text-[11.5px] text-muted mt-2">Ranking is deterministic — stock, serviceability, budget fit, readiness, ETA. Nothing in a catalog's text can change it.</div>
          </Card>
        </div>
        <div className="flex flex-col gap-4">
          <Card title="Live quote">
            {session?.quote ? (
              <div className="text-[13.5px]">
                {session.quote.lines.map((l) => <div key={l.sku} className="flex justify-between py-1"><span>{l.qty} × {l.name} <span className="text-muted">({l.pack_size} {l.unit})</span></span><span className="num">{rupees(l.subtotal_paise)}</span></div>)}
                {session.quote.applied_offers.map((o) => <div key={o.rule_id} className="flex justify-between py-1 text-ok"><span>offer <span className="mono">{o.rule_id}</span></span><span className="num">−{rupees(o.discount_paise)}</span></div>)}
                <div className="flex justify-between py-1 text-ink-2"><span>GST</span><span className="num">{rupees(session.quote.gst_paise)}</span></div>
                <div className="flex justify-between py-1 text-ink-2"><span>Delivery · {session.quote.pincode || "—"} · ~{session.quote.eta_hours} h</span><span className="num">{rupees(session.quote.delivery_fee_paise)}</span></div>
                <div className="flex justify-between pt-2 mt-1 border-t hairline display text-[22px]"><span>Total</span><span className="num">{rupees(session.quote.total_paise)}</span></div>
              </div>
            ) : (
              <Empty title="No quote yet" hint="Ask for an item with a quantity and a pincode." />
            )}
          </Card>
          <Card title="Checkout as the buyer agent" aside={<button className="btn btn-primary h-8" onClick={checkout} disabled={busy || !session?.quote || session.status === "completed"}>{busy ? <Spinner /> : null} Complete</button>}>
            <div className="grid grid-cols-2 gap-3 mb-3">
              <Field label="Mandate cap ₹" hint="0 = exactly the quote"><input className="input num" type="number" value={cap} onChange={(e) => setCap(Number(e.target.value))} /></Field>
              <div className="space-y-2 pt-5">
                <Toggle on={human} onChange={setHuman} label="Human confirmed" />
                <Toggle on={tamper} onChange={setTamper} label="Tamper mandate after signing" />
              </div>
            </div>
            {steps.length === 0 ? (
              <div className="text-[12.5px] text-muted">Issues a scoped grant, signs AP2-shaped mandates, runs the policy gate, creates a Razorpay UPI payment link and processes the webhook — the same path any external agent takes.</div>
            ) : (
              <ol className="relative border-l hairline ml-2 space-y-3">
                {steps.map((s) => (
                  <motion.li key={s.step} initial={{ opacity: 0, x: -6 }} animate={{ opacity: 1, x: 0 }} className="pl-4">
                    <span className={`absolute -left-[5px] mt-[6px] w-[9px] h-[9px] rounded-full ${s.ok ? (s.step.includes("payment") || s.step.includes("webhook") ? "bg-accent" : "bg-ok") : "bg-danger"}`} />
                    <div className="text-[13px] font-medium">{s.step.replaceAll("_", " ")}</div>
                    <div className="text-[12.5px] text-ink-2">{s.detail}</div>
                    {s.checks && <div className="mt-2"><Checks checks={s.checks} /></div>}
                    {s.payment && <a className="text-[12.5px] text-accent" href={s.payment.payment_url} target="_blank" rel="noreferrer">{s.payment.payment_url}</a>}
                  </motion.li>
                ))}
              </ol>
            )}
          </Card>
        </div>
      </div>
    </Page>
  );
}

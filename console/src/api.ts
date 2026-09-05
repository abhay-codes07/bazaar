// Thin typed client for the Bazaar gateway. All money is integer paise.

const BASE = (import.meta.env.VITE_API_BASE as string | undefined) ?? "";

// Merchant-mutating routes require the gateway's admin token (BAZAAR_ADMIN_TOKEN).
// The local default matches the gateway's dev default so the sandbox works out of the box;
// on a real deploy the merchant pastes their token once (sidebar) and it sticks.
export function adminToken(): string {
  try {
    return localStorage.getItem("bazaar-admin-token") ?? "dev-admin-token";
  } catch {
    return "dev-admin-token";
  }
}
export function setAdminToken(t: string) {
  try {
    localStorage.setItem("bazaar-admin-token", t);
  } catch {
    /* ignore */
  }
}

async function req<T>(method: string, path: string, body?: unknown, headers: Record<string, string> = {}): Promise<T> {
  const r = await fetch(BASE + path, {
    method,
    headers: { "content-type": "application/json", "x-admin-token": adminToken(), ...headers },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const text = await r.text();
  let data: unknown = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    data = text;
  }
  if (!r.ok) {
    const d = data as { detail?: unknown; reason?: string } | null;
    const err = new Error(typeof d?.detail === "string" ? d.detail : d?.reason || JSON.stringify(d?.detail ?? data)) as Error & { status: number; data: unknown };
    err.status = r.status;
    err.data = data;
    throw err;
  }
  return data as T;
}

export const rupees = (paise: number | undefined | null) => {
  const v = (paise ?? 0) / 100;
  const frac = Math.abs(v % 1) > 1e-9;
  return "₹" + v.toLocaleString("en-IN", { minimumFractionDigits: frac ? 2 : 0, maximumFractionDigits: frac ? 2 : 0 });
};

export type MerchantRow = { merchant_id: string; name: string; vertical: string; city: string; skus: number; readiness: number; kill_switch: boolean; review_first: boolean };
export type Readiness = { score: number; components: Record<string, number>; fixes: string[] };
export type OfferRule = { rule_id: string; version: number; type: "percent" | "flat" | "free_delivery"; value: number; min_cart_paise: number; min_qty: number; segment: string; max_discount_paise: number; stackable: boolean; valid_until: string | null; description: string };
export type Policy = { review_first: boolean; kill_switch: boolean; agent_allowlist: string[]; min_tier_for_checkout: number; max_negotiation_rounds: number; max_order_paise: number; refunds_per_hour: number; allowed_languages: string[] };
export type Session = {
  session_id: string; merchant_id: string; agent_keyid: string; tier: number; segment: string; language: string; status: string;
  quote: Quote | null; order_id: string; payment_url: string; payment_id: string; last_checks: Check[]; source: string; created_at: string; updated_at: string;
  turns: { action: string; ok: boolean; explanation: string; audit_id: string; language: string }[];
};
export type Check = { name: string; passed: boolean; detail: string };
export type Quote = { quote_id: string; lines: { sku: string; name: string; qty: number; unit: string; pack_size: number; unit_price_paise: number; subtotal_paise: number; gst_paise: number }[]; subtotal_paise: number; discount_paise: number; delivery_fee_paise: number; gst_paise: number; total_paise: number; applied_offers: { rule_id: string; discount_paise: number; inputs_hash: string }[]; pincode: string; eta_hours: number; cod_allowed: boolean; valid_until: string };
export type MerchantDetail = { merchant: { merchant_id: string; name: string; vertical: string; city: string; base_pincode: string; gstin: string; serviceability: { pincode_prefixes: string[]; pincodes: string[]; delivery_fee_paise: number; free_delivery_above_paise: number; eta_hours: number; cod_allowed: boolean }; offer_rules: OfferRule[]; policy: Policy; languages: string[] }; products: number; readiness: Readiness; review_queue: ReviewItem[]; pending_products: number; sessions: Session[] };
export type ReviewItem = { sku: string; field: string; source_value: string; proposed_value: string; confidence: number; reason: string };
export type Product = { sku: string; name: string; source_name: string; description: string; category: string; unit: string; pack_size: number; price_paise: number; stock: number; synonyms: string[]; use_case_tags: string[]; buyer_highlights: string[]; gst_rate_bp: number; flags: string[]; confidence: Record<string, number> };
export type AuditEntry = { seq: number; at: string; audit_id: string; session: string; kind: string; action: string; outcome: string; note: string; money: Record<string, unknown> | null; hash: string };
export type Timeline = { seq: number; at: string; audit_id: string; kind: string; action: string; outcome: string; checks_passed: number; checks_failed: string[]; money: Record<string, unknown>; note: string; hash: string };
export type Stats = { merchants: number; agents: number; sessions: number; completed: number; gmv_paise: number; audit_entries: number; chain_ok: boolean; ledger: { entries: number; distinct_rules: number; inconsistencies: number }; llm?: { backend: string; degraded: boolean; total_failovers?: number; last_error?: string } };
export type Fairness = { merchant_id: string; cohorts: number; rules_checked: number; findings: { rule_id: string; kind: string; detail: string }[]; passed: boolean; ledger: Stats["ledger"] };
export type Candidate = { merchant_id: string; merchant_name: string; city: string; vertical: string; serves_pincode: boolean | null; eta_hours: number; readiness: number; score: number; products: { sku: string; name: string; price_paise: number; unit: string; pack_size: number; in_stock: boolean; estimated_total_paise: number }[]; parsed: Record<string, unknown> };
export type PlaygroundTurn = { action: string; ok: boolean; explanation: string; policy_checks: Check[]; audit_id: string; language: string };
export type PlaygroundStep = { step: string; ok: boolean; detail: string; checks?: Check[]; payment?: { order_id: string; payment_url: string } | null };

export const api = {
  stats: () => req<Stats>("GET", "/bazaar/v1/stats"),
  merchants: () => req<MerchantRow[]>("GET", "/bazaar/v1/merchants"),
  merchant: (id: string) => req<MerchantDetail>("GET", `/bazaar/v1/merchants/${id}`),
  catalog: (id: string) => req<{ products: Product[] }>("GET", `/bazaar/v1/merchants/${id}/catalog`),
  manifest: (id: string) => req<unknown>("GET", `/bazaar/v1/merchants/${id}/manifest`),
  exports: (id: string) => req<Record<string, unknown>>("GET", `/bazaar/v1/merchants/${id}/exports`),
  compile: (id: string, csv: string) => req<{ products: number; review_queue: ReviewItem[]; stripped_injections: number; readiness: Readiness; preview: Product[] }>("POST", `/bazaar/v1/merchants/${id}/compile`, { csv }),
  // Token-free stateless compile — the judge sandbox. Nothing is stored or published.
  compilePreview: (csv: string) => req<{ products: number; review_queue: ReviewItem[]; stripped_injections: number; preview: Product[]; note: string }>("POST", `/bazaar/v1/dev/compile-preview`, { csv }),
  chaos: (model_down: boolean) => req<{ model_down: boolean; llm: { backend: string; degraded: boolean } }>("POST", `/bazaar/v1/dev/chaos`, { model_down }),
  reviewApply: (id: string, sku: string, field: string, value: string) => req<{ remaining: number }>("POST", `/bazaar/v1/merchants/${id}/review/apply`, { sku, field, value }),
  publish: (id: string) => req<{ products: number; readiness: Readiness; endpoints: Record<string, string> }>("POST", `/bazaar/v1/merchants/${id}/publish`),
  putPolicy: (id: string, p: Policy) => req<Policy>("PUT", `/bazaar/v1/merchants/${id}/policy`, p),
  killSwitch: (id: string, on: boolean) => req<{ kill_switch: boolean }>("POST", `/bazaar/v1/merchants/${id}/kill-switch?on=${on}`),
  putRules: (id: string, rules: OfferRule[]) => req<{ rules: OfferRule[]; fairness: Fairness }>("PUT", `/bazaar/v1/merchants/${id}/rules`, { rules }),
  fairness: (id: string) => req<Fairness>("GET", `/bazaar/v1/merchants/${id}/fairness`),
  audit: (id: string) => req<{ chain_ok: boolean; first_bad_seq: number; merkle_root: string; entries: AuditEntry[] }>("GET", `/bazaar/v1/merchants/${id}/audit?limit=200`),
  session: (sid: string) => req<Session>("GET", `/bazaar/v1/sessions/${sid}`),
  replay: (sid: string) => req<{ chain_ok: boolean; timeline: Timeline[] }>("GET", `/bazaar/v1/sessions/${sid}/replay`),
  approve: (mid: string, sid: string) => req<Session>("POST", `/bazaar/v1/merchants/${mid}/review-sessions/${sid}/approve`),
  discover: (intent: string, pincode: string, budget_paise: number) => req<{ candidates: Candidate[] }>("POST", "/bazaar/v1/discover", { intent, pincode, budget_paise }),
  // dev playground (server-held demo buyer agent; fake payments only)
  pgStart: (merchant_id: string, message: string, segment: string) => req<{ session: Session; turn: PlaygroundTurn }>("POST", "/bazaar/v1/dev/playground/sessions", { merchant_id, message, segment }),
  pgMessage: (sid: string, message: string) => req<{ session: Session; turn: PlaygroundTurn }>("POST", `/bazaar/v1/dev/playground/sessions/${sid}/messages`, { message }),
  pgPay: (sid: string) => req<{ session: Session }>("POST", `/bazaar/v1/dev/playground/sessions/${sid}/pay`),
  pgCheckout: (sid: string, max_amount_paise: number, human_confirmation: boolean, tamper: boolean) => req<{ session: Session; steps: PlaygroundStep[] }>("POST", `/bazaar/v1/dev/playground/sessions/${sid}/checkout`, { max_amount_paise, human_confirmation, tamper }),
};

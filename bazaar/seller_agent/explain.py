"""Plain-language explanations (EN / HI / Hinglish) for every agent action. Templated — no model
output is ever shown to the buyer without passing through here, so tone rules (no urgency, no
confirm-shaming) hold by construction."""

from __future__ import annotations

from bazaar.seller_agent.offer_engine import Quote


def rs(paise: int) -> str:
    return f"₹{paise / 100:,.0f}" if paise % 100 == 0 else f"₹{paise / 100:,.2f}"


def quote_text(q: Quote, lang: str = "en") -> str:
    items = ", ".join(f"{ln.qty} × {ln.name} ({ln.pack_size:g} {ln.unit}) @ {rs(ln.unit_price_paise)}" for ln in q.lines)
    offers = "; ".join(f"{a.rule_id}: −{rs(a.discount_paise)}" for a in q.applied_offers if a.discount_paise)
    if lang == "hi":
        s = f"कोटेशन: {items}। उप-योग {rs(q.subtotal_paise)}"
        if q.discount_paise:
            s += f", छूट −{rs(q.discount_paise)} ({offers})"
        s += f", GST {rs(q.gst_paise)}, डिलीवरी {rs(q.delivery_fee_paise)} → कुल {rs(q.total_paise)}।"
        if q.pincode:
            s += f" {q.pincode} तक लगभग {q.eta_hours} घंटे में।"
        return s
    if lang == "hi-Latn":
        s = f"Quote: {items}. Subtotal {rs(q.subtotal_paise)}"
        if q.discount_paise:
            s += f", chhoot −{rs(q.discount_paise)} ({offers})"
        s += f", GST {rs(q.gst_paise)}, delivery {rs(q.delivery_fee_paise)} → total {rs(q.total_paise)}."
        if q.pincode:
            s += f" {q.pincode} tak lagbhag {q.eta_hours} ghante mein."
        return s
    s = f"Quote: {items}. Subtotal {rs(q.subtotal_paise)}"
    if q.discount_paise:
        s += f", discount −{rs(q.discount_paise)} ({offers})"
    s += f", GST {rs(q.gst_paise)}, delivery {rs(q.delivery_fee_paise)} → total {rs(q.total_paise)}."
    if q.pincode:
        s += f" Delivery to {q.pincode} in about {q.eta_hours} h."
    return s


def serviceability_text(serves: bool, pincode: str, eta_hours: int, fee_paise: int, cod: bool, lang: str = "en") -> str:
    if lang == "hi":
        return (f"हाँ, हम {pincode} पर डिलीवर करते हैं — लगभग {eta_hours} घंटे, शुल्क {rs(fee_paise)}, COD {'उपलब्ध' if cod else 'उपलब्ध नहीं'}।" if serves else f"क्षमा करें, हम {pincode} पर डिलीवर नहीं करते।")
    if lang == "hi-Latn":
        return (f"Haan, hum {pincode} par deliver karte hain — lagbhag {eta_hours} ghante, fee {rs(fee_paise)}, COD {'available' if cod else 'nahi'}." if serves else f"Sorry, hum {pincode} par deliver nahi karte.")
    return (f"Yes, we deliver to {pincode} — about {eta_hours} h, fee {rs(fee_paise)}, COD {'available' if cod else 'not available'}." if serves else f"Sorry, we do not deliver to {pincode}.")


def decline_text(reason: str, lang: str = "en") -> str:
    if lang == "hi":
        return f"यह संभव नहीं है: {reason}"
    if lang == "hi-Latn":
        return f"Yeh possible nahi hai: {reason}"
    return f"Can't do that: {reason}"


def no_offer_text(reasons: list[str], lang: str = "en") -> str:
    r = "; ".join(reasons) if reasons else "no eligible offer"
    if lang == "hi":
        return f"इस कार्ट पर कोई अतिरिक्त छूट लागू नहीं होती ({r})। कीमत पहले जैसी है।"
    if lang == "hi-Latn":
        return f"Is cart par koi extra chhoot lagu nahi hoti ({r}). Price same rahegi."
    return f"No additional offer applies to this cart ({r}). The price stands."

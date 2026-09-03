# Razorpay test-mode evidence (real APIs, not the sandbox client)

Run at 2026-09-03T23:52:38+00:00 against Razorpay **test mode** via the official SDK (`BAZAAR_RAZORPAY=razorpay`). The buyer was a registered Ed25519 agent making RFC 9421-signed requests; the checkout passed the full policy gate, was parked by the review-first merchant, approved, and paid on Razorpay's hosted page. Every webhook below arrived over a public Cloudflare tunnel and was HMAC-verified — including a real `payment.failed` (international card declined) before the successful capture, exercising the retry path.

| artefact | id |
|---|---|
| session | `sess_37ad5a2d087a22` |
| payment link | `plink_TXkKsUwJQzY8kJ` |
| Razorpay order | `order_TXkUiwOPKhZxiQ` |
| payment | `pay_TXkgZDVbhR09Kg` |
| amount | ₹598.50 |
| chain intact | True |

Audit-chain money events for this session (from `/replay`):

```
#139 payment_link_created ok {"order_id": "", "payment_link_id": "plink_TXkKsUwJQzY8kJ", "amount_paise": 59850, "currency": "INR"}
#140 payment_failed failed {"order_id": "order_TXkUiwOPKhZxiQ", "payment_id": "pay_TXkX8IJ6b1nkn4"}
#142 payment_captured ok {"order_id": "order_TXkUiwOPKhZxiQ", "payment_id": "pay_TXkgZDVbhR09Kg", "amount_paise": 59850, "method": "netbanking"}
```

Dashboard screenshot: `results/razorpay_dashboard.png` (Payments view showing the captured test payment).

# Deploying Bazaar

One process serves the API, the adapters, the global MCP server and the merchant console. It runs anywhere a container runs; `fly.toml` and a `Dockerfile` are included.

## Fly.io (recommended)

`fly.toml` ships with `BAZAAR_ENV=prod`, which makes the gateway **refuse to boot on the dev admin token or webhook secret** — so a public deployment is safe by default, but you must set two secrets before it will start.

```bash
# 1. install flyctl and sign in
#    (install: https://fly.io/docs/flyctl/install/)
flyctl auth login

# 2. create the app (first time only). --no-deploy so we can set secrets first.
#    the app name in fly.toml is "bazaar-agentic"; pick your own if it is taken.
flyctl launch --copy-config --no-deploy

# 3. set the two REQUIRED secrets (prod refuses to boot without real ones)
flyctl secrets set \
  BAZAAR_ADMIN_TOKEN="$(openssl rand -hex 24)" \
  RAZORPAY_WEBHOOK_SECRET="$(openssl rand -hex 24)"

# 4. deploy
flyctl deploy

# 5. open it
flyctl open            # the console + API on your *.fly.dev URL
```

That boots on the deterministic `fake` backend — every page, the playground, discovery, quotes, the audit trail and the whole policy gate work with no API keys, fully reproducible.

### Add a real model (optional, free)

To run the Seller Agent on a real model, set a backend and its key as secrets. Groq's free tier means this costs nothing:

```bash
flyctl secrets set BAZAAR_LLM=groq GROQ_API_KEY=gsk_...          # free tier, gpt-oss-120b
#   or gpt-4o:
flyctl secrets set BAZAAR_LLM=openai OPENAI_API_KEY=sk-proj-...
```

### Add real Razorpay test mode (optional)

```bash
flyctl secrets set BAZAAR_RAZORPAY=razorpay \
  RAZORPAY_KEY_ID=rzp_test_... RAZORPAY_KEY_SECRET=...
```

Point a Razorpay **test-mode webhook** at `https://<your-app>.fly.dev/webhooks/razorpay` with the same `RAZORPAY_WEBHOOK_SECRET` you set above, subscribed to `payment_link.paid` / `payment.captured`.

### Notes for a live payment demo

`fly.toml` sets `min_machines_running = 0` (auto-stop to save cost). A cold start empties the in-memory session and nonce state, so **while a payment link is outstanding, keep one machine warm**:

```bash
flyctl scale count 1 --region sin        # keep a machine up during the demo
```

Redis/Postgres for durable multi-instance state is the Phase-1 item; `gateway/state.py` is the single swap point.

## Docker (any host)

```bash
docker build -t bazaar .
docker run -p 8000:8000 \
  -e BAZAAR_ADMIN_TOKEN=$(openssl rand -hex 24) \
  -e RAZORPAY_WEBHOOK_SECRET=$(openssl rand -hex 24) \
  bazaar
# open http://localhost:8000
```

Omit `BAZAAR_ENV=prod` locally and the dev defaults are accepted; the image defaults to the `fake` backend, so it runs with no keys at all.

## Environment reference

| variable | values | purpose |
|---|---|---|
| `BAZAAR_LLM` | `fake` · `openai` · `groq` · `anthropic` | Seller-Agent / compiler backend (`fake` = deterministic, no keys) |
| `BAZAAR_RAZORPAY` | `fake` · `razorpay` | in-memory sandbox, or Razorpay test-mode APIs |
| `BAZAAR_ENV` | `dev` · `prod` | `prod` refuses dev admin token / webhook secret at boot |
| `BAZAAR_ADMIN_TOKEN` | secret | gates every merchant-mutating route |
| `RAZORPAY_WEBHOOK_SECRET` | secret | HMAC-verifies incoming webhooks |
| `OPENAI_API_KEY` / `GROQ_API_KEY` / `ANTHROPIC_API_KEY` | secret | model keys (only the selected backend's is needed) |
| `RAZORPAY_KEY_ID` / `RAZORPAY_KEY_SECRET` | `rzp_test_…` | Razorpay test-mode credentials |
| `PORT` | default `8000` | listen port |

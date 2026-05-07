# Yogurt Watch — public web page

Static landing page + Stripe-powered $0.99/mo subscription. Deployed on
Vercel. Reads stock status from `status.json` (committed to this repo by
the daily GitHub Actions workflow), and sends a daily digest to active
subscribers via Resend (email) or the Telegram bot.

## Architecture

```
GH Actions (daily, 9 AM CR)
   └─> yogurt_monitor.py check
         ├─ writes status.json   ──── git commit & push ──┐
         └─ pings personal Telegram                       │
                                                          ▼
                                  https://raw.githubusercontent.com/.../status.json
                                                          │
Vercel (this directory)                                   │
   ├─ /                       ──── fetch status.json ─────┘
   │   shows live stock + subscribe form
   ├─ /api/checkout           ──── creates Stripe Checkout session
   ├─ /api/stripe-webhook     ──── persists subscriber in Vercel KV
   └─ /api/digest             ──── Vercel cron (9:30 AM CR)
                                   reads subs from KV, sends email/Telegram
```

## Setup (one-time)

### 1. Stripe

1. Create a product called "Yogurt Watch — Daily Updates".
2. Add a recurring price: **$0.99 / month**. Copy the `price_…` ID.
3. Get your secret key (`sk_live_…` or `sk_test_…`) from Developers → API keys.
4. Webhooks → Add endpoint → URL: `https://<your-vercel-domain>/api/stripe-webhook`
   Listen for: `checkout.session.completed`, `customer.subscription.deleted`,
   `customer.subscription.updated`. Copy the signing secret (`whsec_…`).

### 2. Resend (email)

1. Sign up at https://resend.com.
2. Verify a sending domain (or use the sandbox domain to start).
3. Copy your API key.

### 3. Telegram bot

Reuse the same bot already used by `yogurt_monitor.py`. Each subscriber will
need to message the bot at least once before they can receive notifications;
they paste their numeric chat ID into the subscribe form (find it via
[@userinfobot](https://t.me/userinfobot)).

### 4. Deploy to Vercel

```bash
cd web
npx vercel link              # link to a new Vercel project
npx vercel kv create yogurt  # create KV store and link it (this sets KV_* env vars)
```

Set the remaining env vars (Vercel dashboard → Project → Settings → Environment Variables):

- `STRIPE_SECRET_KEY`
- `STRIPE_WEBHOOK_SECRET`
- `STRIPE_PRICE_ID`
- `RESEND_API_KEY`
- `FROM_EMAIL`
- `TELEGRAM_BOT_TOKEN`
- `CRON_SECRET` (any long random string)

Then deploy:

```bash
npx vercel --prod
```

### 5. Wire the GH Actions workflow

The existing `.github/workflows/yogurt.yml` already commits `status.json`
back to `main` after each daily check. No additional work — but if your
default branch isn't `main`, update `STATUS_URL` in `web/script.js` and
`web/api/digest.js`.

## Testing locally

```bash
npm install
npx vercel dev
```

Use Stripe test mode (`sk_test_…`, `whsec_…` from `stripe listen --forward-to localhost:3000/api/stripe-webhook`) and put the test price ID in `STRIPE_PRICE_ID`.

## Notes / future work

- The cron runs at 9:30 AM CR (15:30 UTC) — 30 min after the GH Actions check
  commits `status.json`, so the page reads the freshest data.
- `customer.subscription.deleted` is the canonical "cancelled" signal — we
  also handle `subscription.updated` for fail-to-pay cases.
- Stripe fees on $0.99 charges are ~$0.32, leaving ~$0.67 net per sub/month.
- Auto-Telegram chat-id capture (via bot `/start` deep link) would remove
  the manual chat-ID step but requires standing up a bot webhook handler.

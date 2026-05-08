# Yogurt Watch — public web page

Static landing page hosted on **GitHub Pages**. Stripe handles billing via
a Payment Link (no backend), and the daily digest is sent by the existing
GitHub Actions workflow that already runs `yogurt_monitor.py`.

## Architecture

```
GH Actions  (daily, 9 AM CR)
   ├─ yogurt_monitor.py check     → writes status.json, commits to main
   └─ digest.py                   → lists active Stripe subscriptions,
                                     sends email (Resend) or Telegram

GH Pages
   └─ web/ deployed automatically on push (workflows/pages.yml)
        ├─ index.html  fetches status.json from raw.githubusercontent.com
        └─ Subscribe button → Stripe Payment Link (Stripe-hosted checkout)
```

No serverless functions. No database. Stripe is the subscriber list.

## Setup

### 1. Create the Stripe Payment Link

1. Stripe Dashboard → Products → **+ Add product**
   - Name: *Yogurt Watch — Daily Updates*
   - Price: **$0.99 / month**, recurring
2. From the new price, click **Create payment link**.
3. Under **More options**, add **Custom fields**:
   - Field key: `telegram_chat_id` (must match exactly — the digest reads this key)
   - Type: Text, Optional
   - Label: *Telegram chat ID (optional, for Telegram updates)*
4. Save and copy the link (looks like `https://buy.stripe.com/XXXXXX`).
5. Edit `web/index.html` and replace the placeholder in `window.YOGURT_CONFIG.stripePaymentLink` with that URL.
6. Get your secret key from **Developers → API keys** (`sk_live_…` or `sk_test_…`).

### 2. Create a Resend account

1. Sign up at https://resend.com.
2. Verify a sending domain (or use the default sandbox while testing).
3. Copy your API key.

### 3. Add the GitHub repository secrets

Settings → Secrets and variables → Actions → **New repository secret**:

| Secret | Value |
|---|---|
| `STRIPE_SECRET_KEY` | `sk_live_…` |
| `RESEND_API_KEY` | `re_…` |
| `FROM_EMAIL` | e.g. `Yogurt Watch <updates@your-domain.com>` |
| `TELEGRAM_BOT_TOKEN` | Already set (reused from monitor) |

### 4. Turn on GitHub Pages

Repository → Settings → Pages → Build and deployment → **Source: GitHub Actions**.

The next push to `web/**` (or a manual run of the *Deploy GitHub Pages*
workflow) publishes to `https://<username>.github.io/<repo>/`.

### 5. (Optional) Custom domain

Settings → Pages → Custom domain → enter your domain, then add a `CNAME`
DNS record pointing to `<username>.github.io`.

## Updating the look / copy

Just edit `web/index.html` and `web/style.css` and push to `main`. The
Pages workflow redeploys automatically.

## Notes

- The first email goes out on the next daily run (9 AM CR), not instantly
  after subscribing — for a daily-update product that's the right cadence.
- Stripe fees on $0.99/mo subscriptions are ~$0.32, leaving ~$0.67 net.
- If `STRIPE_SECRET_KEY` is unset, `digest.py` no-ops cleanly so the
  monitor keeps working before billing is wired up.
- To watch a different product, edit the `PRODUCT` dict in
  `yogurt_monitor.py` (root of repo).

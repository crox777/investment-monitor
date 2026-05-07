# Investment Price Monitor

Automated price monitoring for investment alerts. Runs via GitHub Actions (no laptop needed), sends alerts to Telegram + macOS.

## Setup

### 1. Create GitHub Repository

```bash
gh repo create investment-monitor --public --source=. --remote=origin --push
```

### 2. Add Telegram Secrets

Get your bot token and chat ID, then:

```bash
gh secret set TELEGRAM_BOT_TOKEN --body "8688635125:AAEGGq0XvmCMJDSZswiAuxaQl3vE64u7Bl4"
gh secret set TELEGRAM_CHAT_ID --body "1686017"
```

### 3. Done

GitHub Actions will run automatically on schedule:
- **7 AM weekdays** (morning digest)
- **8:30 AM, 10 AM, 12 PM, 2 PM, 3 PM** (hourly checks)
- **4 PM** (afternoon digest)
- **10 PM** (after-hours crypto check)

## Local Testing

```bash
python monitor.py status        # Show current prices
python monitor.py check         # Run price check
python monitor.py digest        # Run digest
python monitor.py test          # Test Telegram
```

## Alert Levels

| Ticker | Watch | Action | Direction |
|--------|-------|--------|-----------|
| VIX    | 35    | 40     | above     |
| S&P 500 | 6400 | 6100   | below     |
| BTC    | 50k   | 40k    | below     |
| NVDA   | 165   | 148    | below     |
| ADBE   | 250   | 220    | below     |

Modify in `monitor.py` ALERTS section.

## Yogurt Stock Monitor

A second monitor (`yogurt_monitor.py`) reuses the same Telegram secrets to
watch a PriceSmart Costa Rica product page once daily at 9 AM CR time and
ping when the Member's Selection Greek Yogurt is back in stock.

Workflow: `.github/workflows/yogurt.yml` (cron `0 15 * * *`).

Behavior:
- 🚨 Sends an **alert** the first day it transitions back to in-stock
- 🥛 Sends a soft "still in stock" reminder on subsequent days
- 📦 Sends a "still out of stock" message when unavailable
- ❓ Sends a "status unclear" message if the page loads but markup changed

PriceSmart's product page is a Nuxt SPA — the static HTML only contains
i18n label templates ("Out of Stock", "Add to Cart") that are present on
every page regardless of actual stock state, and the SKU-specific inventory
is fetched by JS. The script therefore uses **Playwright** (headless
Chromium) to render the page, then reads the visible body text and matches
it against marker phrases (`"agregar a carrito"` for in-stock,
`"fuera de stock"` / `"agotado"` / etc. for out-of-stock).

Local testing:

```bash
pip install playwright requests
playwright install chromium
python yogurt_monitor.py check    # run a real check
python yogurt_monitor.py test     # ping Telegram only
python yogurt_monitor.py status   # print last saved state
```

Set `DEBUG=1` to dump the rendered visible text into the Telegram message
(useful when PriceSmart changes their wording and detection breaks).

To watch a different product, edit the `PRODUCT` dict at the top of
`yogurt_monitor.py`, or set `YOGURT_URL` / `YOGURT_NAME` env vars.

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

PriceSmart blocks plain `requests` (TLS fingerprinting), so the script uses
[`curl_cffi`](https://pypi.org/project/curl-cffi/) to impersonate Chrome.

Local testing:

```bash
pip install curl_cffi requests
python yogurt_monitor.py check    # run a real check
python yogurt_monitor.py test     # ping Telegram only
python yogurt_monitor.py status   # print last saved state
```

To watch a different product, edit the `PRODUCT` dict at the top of
`yogurt_monitor.py`.

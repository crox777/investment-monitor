#!/usr/bin/env python3
"""
Investment Price Monitor
========================
Checks prices against alert levels and sends notifications via:
- macOS native notifications (always)
- Telegram bot (if configured)

Runs via launchd on a schedule. Only notifies when thresholds are crossed.
"""

import json
import os
import sys
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

# Costa Rica timezone (GMT-6)
COSTA_RICA_TZ = timezone(timedelta(hours=-6))

# ─── TRY IMPORTING YFINANCE ─────────────────────────────────────────────────
try:
    import yfinance as yf
except ImportError:
    print("ERROR: yfinance not installed. Run: pip3 install yfinance")
    sys.exit(1)

# ─── CONFIGURATION ──────────────────────────────────────────────────────────

SCRIPT_DIR = Path(__file__).parent
STATE_FILE = SCRIPT_DIR / "alert_state.json"
CONFIG_FILE = SCRIPT_DIR / "config.json"
LOG_FILE = SCRIPT_DIR / "monitor.log"

# Price alert definitions
# direction: "below" means alert when price drops BELOW level
#            "above" means alert when price rises ABOVE level
ALERTS = [
    {
        "ticker": "^VIX",
        "name": "VIX",
        "watch": 35,
        "action": 40,
        "direction": "above",
        "priority": 0,
        "watch_msg": "Elevated fear — stay alert, prepare cash for VOO",
        "action_msg": "CONTRARIAN BUY SIGNAL — deploy all available cash to VOO NOW",
    },
    {
        "ticker": "^GSPC",
        "name": "S&P 500",
        "watch": 6400,
        "action": 6100,
        "direction": "below",
        "priority": 1,
        "watch_msg": "Correction zone — prepare to double VOO next month",
        "action_msg": "DEPLOY AGGRESSIVELY — double VOO to $3,000, suspend BRK.B & VRTX",
    },
    {
        "ticker": "BTC-USD",
        "name": "Bitcoin",
        "watch": 50000,
        "action": 40000,
        "direction": "below",
        "priority": 2,
        "watch_msg": "Deploy first BTC reserve tranche (half of accumulated cash)",
        "action_msg": "LTHRP ZONE — deploy ALL remaining BTC reserve immediately",
    },
    {
        "ticker": "NVDA",
        "name": "Nvidia",
        "watch": 165,
        "action": 148,
        "direction": "below",
        "priority": 3,
        "watch_msg": "~20x forward P/E — start building position faster ($250→$500)",
        "action_msg": "18x forward P/E — shift FULL $500 tech budget to NVDA, suspend ADBE",
    },
    {
        "ticker": "ADBE",
        "name": "Adobe",
        "watch": 250,
        "action": 220,
        "direction": "below",
        "priority": 4,
        "watch_msg": "Below 2022 lows — go aggressive ($250→$500)",
        "action_msg": "MAXIMUM FEAR — shift FULL $500 tech budget to ADBE, suspend NVDA",
    },
]

# Portfolio holdings for value tracking (optional summary in daily digest)
HOLDINGS = [
    {"ticker": "VOO", "name": "S&P 500 ETF", "shares": 54, "avg_cost": 486},
    {"ticker": "GOOGL", "name": "Alphabet", "shares": 68, "avg_cost": 113},
    {"ticker": "AMZN", "name": "Amazon", "shares": 33, "avg_cost": 194},
    {"ticker": "DOCN", "name": "DigitalOcean", "shares": 100, "avg_cost": 35.40},
    {"ticker": "VXUS", "name": "Intl ETF", "shares": 50, "avg_cost": 61.60},
    {"ticker": "RKLB", "name": "Rocket Lab", "shares": 110, "avg_cost": 13.30},
    {"ticker": "NVDA", "name": "Nvidia", "shares": 12, "avg_cost": 104.20},
    {"ticker": "NU", "name": "Nu Holdings", "shares": 100, "avg_cost": 10.94},
]

# BTC reserve tracking
DCA_START = datetime(2026, 4, 1)
BTC_MONTHLY_RESERVE = 500


# ─── LOGGING ─────────────────────────────────────────────────────────────────

def log(msg):
    """Append to log file and print."""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{timestamp}] {msg}"
    print(line)
    try:
        with open(LOG_FILE, "a") as f:
            f.write(line + "\n")
        # Keep log file under 1000 lines
        lines = LOG_FILE.read_text().splitlines()
        if len(lines) > 1000:
            LOG_FILE.write_text("\n".join(lines[-500:]) + "\n")
    except Exception:
        pass


# ─── STATE MANAGEMENT ────────────────────────────────────────────────────────
# Tracks which alerts have already been notified to avoid spamming.
# Resets when price moves back to OK, so you get notified again if it re-triggers.

def load_state():
    """Load previous alert states."""
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except Exception:
            return {}
    return {}


def save_state(state):
    """Persist alert states."""
    STATE_FILE.write_text(json.dumps(state, indent=2))


# ─── CONFIGURATION ──────────────────────────────────────────────────────────

def load_config():
    """Load Telegram config from env vars (GitHub Actions) or file (local)."""
    import os
    # GitHub Actions: read from environment
    if os.getenv("TELEGRAM_BOT_TOKEN"):
        return {
            "telegram_bot_token": os.getenv("TELEGRAM_BOT_TOKEN"),
            "telegram_chat_id": os.getenv("TELEGRAM_CHAT_ID"),
        }
    # Local: read from config file
    if CONFIG_FILE.exists():
        try:
            return json.loads(CONFIG_FILE.read_text())
        except Exception:
            return {}
    return {}


# ─── PRICE FETCHING ─────────────────────────────────────────────────────────

def fetch_prices():
    """Fetch current prices for all alert tickers + portfolio tickers."""
    alert_tickers = [a["ticker"] for a in ALERTS]
    portfolio_tickers = [h["ticker"] for h in HOLDINGS]
    all_tickers = list(dict.fromkeys(alert_tickers + portfolio_tickers))

    log(f"Fetching prices for {len(all_tickers)} tickers...")

    prices = {}
    for ticker in all_tickers:
        try:
            t = yf.Ticker(ticker)
            hist = t.history(period="1d")
            if not hist.empty:
                price = float(hist["Close"].iloc[-1])
                if price > 0:
                    prices[ticker] = round(price, 2)
                    log(f"  {ticker}: ${price:,.2f}")
        except Exception as e:
            log(f"  {ticker}: FAILED ({e})")

    log(f"Got prices for {len(prices)}/{len(all_tickers)} tickers")
    return prices


# ─── ALERT EVALUATION ───────────────────────────────────────────────────────

def evaluate_alerts(prices):
    """
    Check each alert against current prices.
    Returns list of triggered alerts with their status.
    """
    results = []

    for alert in sorted(ALERTS, key=lambda a: a["priority"]):
        ticker = alert["ticker"]
        price = prices.get(ticker)

        if price is None:
            results.append({**alert, "price": None, "status": "unknown"})
            continue

        direction = alert["direction"]
        status = "ok"

        if direction == "below":
            if price <= alert["action"]:
                status = "action"
            elif price <= alert["watch"]:
                status = "watch"
        elif direction == "above":
            if price >= alert["action"]:
                status = "action"
            elif price >= alert["watch"]:
                status = "watch"

        # Calculate how far price must move to hit watch level (positive = safe buffer)
        if direction == "below" and alert["watch"]:
            distance_pct = ((price - alert["watch"]) / alert["watch"]) * 100
        elif direction == "above" and alert["watch"]:
            distance_pct = ((alert["watch"] - price) / alert["watch"]) * 100
        else:
            distance_pct = None

        # Human-readable distance text
        if distance_pct is not None:
            if distance_pct > 0:
                dist_text = f"{distance_pct:.1f}% away from watch"
            else:
                dist_text = f"PAST watch by {abs(distance_pct):.1f}%"
        else:
            dist_text = None

        results.append({
            **alert,
            "price": price,
            "status": status,
            "distance_pct": distance_pct,
            "dist_text": dist_text,
        })

    return results


# ─── PORTFOLIO VALUATION ────────────────────────────────────────────────────

def calculate_portfolio(prices):
    """Calculate current portfolio value and gains."""
    total_value = 0
    total_cost = 0
    holdings_detail = []

    for h in HOLDINGS:
        price = prices.get(h["ticker"])
        if price:
            value = price * h["shares"]
            cost = h["avg_cost"] * h["shares"]
            gain_pct = ((value - cost) / cost) * 100
            total_value += value
            total_cost += cost
            holdings_detail.append({
                **h,
                "price": price,
                "value": value,
                "gain_pct": gain_pct,
            })

    total_gain_pct = ((total_value - total_cost) / total_cost * 100) if total_cost > 0 else 0

    return {
        "total_value": total_value,
        "total_cost": total_cost,
        "total_gain_pct": total_gain_pct,
        "holdings": holdings_detail,
    }


def calculate_btc_reserve():
    """Calculate accumulated BTC cash reserve."""
    now = datetime.now()
    if now < DCA_START:
        return 0, "DCA starts April 2026"
    months = (now.year - DCA_START.year) * 12 + (now.month - DCA_START.month) + 1
    accumulated = months * BTC_MONTHLY_RESERVE
    return accumulated, f"{months} months × ${BTC_MONTHLY_RESERVE}"


# ─── NOTIFICATIONS ──────────────────────────────────────────────────────────

def send_macos_notification(title, message, subtitle="", sound="default"):
    """Send a macOS native notification."""
    # Use osascript for rich notifications
    script = f'''
    display notification "{message}" with title "{title}" subtitle "{subtitle}" sound name "{sound}"
    '''
    try:
        subprocess.run(["osascript", "-e", script], check=True, capture_output=True)
        log(f"macOS notification sent: {title}")
    except Exception as e:
        log(f"macOS notification failed: {e}")


def send_macos_dialog(title, message):
    """Send a macOS dialog box for ACTION-level alerts (can't miss it)."""
    script = f'''
    display dialog "{message}" with title "{title}" buttons {{"Got it"}} default button 1 with icon stop
    '''
    try:
        subprocess.run(["osascript", "-e", script], check=True, capture_output=True)
    except Exception as e:
        log(f"macOS dialog failed: {e}")


def send_telegram(message, config):
    """Send a Telegram message."""
    bot_token = config.get("telegram_bot_token")
    chat_id = config.get("telegram_chat_id")

    if not bot_token or not chat_id:
        return

    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": message,
        "parse_mode": "HTML",
    }

    try:
        resp = requests.post(url, json=payload, timeout=10)
        if resp.status_code == 200:
            log("Telegram message sent")
        else:
            log(f"Telegram failed: {resp.status_code} {resp.text[:100]}")
    except Exception as e:
        log(f"Telegram error: {e}")


# ─── MAIN LOGIC ─────────────────────────────────────────────────────────────

def format_price(price, ticker):
    """Format price for display."""
    if price is None:
        return "N/A"
    if ticker == "BTC-USD":
        return f"${price:,.0f}"
    if price >= 1000:
        return f"${price:,.0f}"
    return f"${price:,.2f}"


def run_check(force_digest=False):
    """Main check routine."""
    log("=" * 50)
    log("Starting investment monitor check")

    config = load_config()
    state = load_state()
    prices = fetch_prices()

    if not prices:
        log("No prices fetched — aborting")
        send_macos_notification(
            "Investment Monitor",
            "Failed to fetch prices. Check internet connection.",
            sound="Basso"
        )
        return

    # Evaluate alerts
    results = evaluate_alerts(prices)
    new_triggers = []
    cleared_triggers = []

    for r in results:
        key = r["ticker"]
        prev_status = state.get(key, "ok")
        curr_status = r["status"]

        # Detect new triggers (ok→watch, ok→action, watch→action)
        if curr_status in ("watch", "action") and prev_status != curr_status:
            # Only notify if it's a NEW or ESCALATED trigger
            if curr_status == "action" or (curr_status == "watch" and prev_status == "ok"):
                new_triggers.append(r)

        # Detect cleared triggers (was triggered, now ok)
        if curr_status == "ok" and prev_status in ("watch", "action"):
            cleared_triggers.append(r)

        # Update state
        state[key] = curr_status

    save_state(state)

    # ── Send notifications for NEW triggers ──────────────────────────────

    for trigger in sorted(new_triggers, key=lambda t: t["priority"]):
        price_str = format_price(trigger["price"], trigger["ticker"])
        is_action = trigger["status"] == "action"
        level_type = "ACTION" if is_action else "WATCH"
        msg = trigger["action_msg"] if is_action else trigger["watch_msg"]

        # macOS notification
        macos_title = f"{'🔴' if is_action else '🟡'} {trigger['name']} — {level_type}"
        macos_body = f"Price: {price_str}\n{msg}"

        if is_action:
            # ACTION level = dialog box (can't miss it) + notification
            send_macos_dialog(macos_title, macos_body)
            send_macos_notification(macos_title, macos_body, sound="Sosumi")
        else:
            send_macos_notification(macos_title, macos_body, sound="Glass")

        # Telegram
        emoji = "🔴" if is_action else "🟡"
        level_name = "Action" if is_action else "Watch"
        level_price = trigger['action'] if is_action else trigger['watch']
        tg_msg = (
            f"{emoji} <b>{trigger['name']} — {level_type}</b>\n"
            f"Price: <code>{price_str}</code>\n"
            f"{level_name} level: <code>{format_price(level_price, trigger['ticker'])}</code>\n\n"
            f"→ {msg}"
        )
        send_telegram(tg_msg, config)

        log(f"ALERT: {trigger['name']} at {price_str} → {level_type}: {msg}")

    # ── Notify cleared triggers ──────────────────────────────────────────

    for cleared in cleared_triggers:
        price_str = format_price(cleared["price"], cleared["ticker"])
        msg = f"{cleared['name']} back to normal at {price_str}"
        send_macos_notification("✅ Alert Cleared", msg)
        send_telegram(f"✅ <b>Alert Cleared</b>\n{msg}", config)
        log(f"CLEARED: {msg}")

    # ── Daily digest (if force_digest or first check of the day) ─────────

    today = datetime.now().strftime("%Y-%m-%d")
    last_digest = state.get("_last_digest", "")

    if force_digest or last_digest != today:
        portfolio = calculate_portfolio(prices)
        btc_reserve, btc_note = calculate_btc_reserve()

        # Count active alerts
        active_watches = [r for r in results if r["status"] == "watch"]
        active_actions = [r for r in results if r["status"] == "action"]

        # Build digest with proper Telegram markdown
        now_cr = datetime.now(COSTA_RICA_TZ)
        timestamp = now_cr.strftime('%a, %b %d @ %I:%M %p') + " (GMT-6)"
        lines = [
            "📊 <b>INVESTMENT DIGEST</b>",
            f"<i>{timestamp}</i>",
            ""
        ]

        if active_actions:
            lines.append("🔴 <b>⚠️  ACTION REQUIRED</b>")
            for a in active_actions:
                lines.append(f"  • <b>{a['name']}</b>")
                lines.append(f"    Price: <code>{format_price(a['price'], a['ticker'])}</code>")
                lines.append(f"    → {a['action_msg']}")
            lines.append("")

        if active_watches:
            lines.append("🟡 <b>⏳ Watching</b>")
            for w in active_watches:
                lines.append(f"  • <b>{w['name']}</b>")
                lines.append(f"    Price: <code>{format_price(w['price'], w['ticker'])}</code>")
                lines.append(f"    {w['dist_text']}")
            lines.append("")

        if not active_actions and not active_watches:
            lines.append("✅ <b>All Clear</b>")
            lines.append("Standard DCA → no changes needed")
            lines.append("")

        # Alert distances with visual bars
        lines.append("")
        lines.append("<b>PRICE vs TRIGGERS</b>")
        lines.append("")
        for r in results:
            if r["price"] and r["distance_pct"] is not None:
                safe_pct = max(0, r["distance_pct"])
                bar_len = min(15, int(safe_pct / 5))
                bar = "█" * bar_len + "░" * (15 - bar_len)
                drop_or_rise = "↓" if r["direction"] == "below" else "↑"
                now_str = format_price(r["price"], r["ticker"])
                watch_str = format_price(r["watch"], r["ticker"])

                if r["distance_pct"] > 0:
                    label = f"{r['distance_pct']:.1f}% {drop_or_rise}"
                else:
                    label = f"<b>TRIGGERED</b> (past by {abs(r['distance_pct']):.1f}%)"

                lines.append(f"<b>{r['name']}</b>")
                lines.append(f"{bar} {label}")
                lines.append(f"Now: <code>{now_str}</code>  |  Alert: <code>{watch_str}</code>")
                lines.append("")

        # Portfolio summary
        if portfolio["total_value"] > 0:
            lines.append("<b>💰 PORTFOLIO</b>")
            lines.append(f"Value: <code>${portfolio['total_value']:,.0f}</code>")
            lines.append(f"Gain: <code>{portfolio['total_gain_pct']:+.1f}%</code>")
            lines.append("")

        if btc_reserve > 0:
            lines.append("<b>₿ BTC RESERVE</b>")
            lines.append(f"<code>${btc_reserve:,}</code> ({btc_note})")
            lines.append("")

        lines.append("<i>Next check: follow the schedule</i>")

        digest_text = "\n".join(lines)

        # Send digest
        send_telegram(digest_text, config)

        # macOS summary notification
        active_count = len(active_watches) + len(active_actions)
        if active_count > 0:
            macos_msg = f"{active_count} active alert(s). Portfolio: ${portfolio['total_value']:,.0f}"
        else:
            macos_msg = f"All clear. Portfolio: ${portfolio['total_value']:,.0f} ({portfolio['total_gain_pct']:+.1f}%)"
        send_macos_notification("📊 Daily Digest", macos_msg)

        state["_last_digest"] = today
        save_state(state)

        log("Daily digest sent")

    # Summary
    active = [r for r in results if r["status"] != "ok" and r["price"]]
    if not new_triggers and not active:
        log("All clear — no alerts triggered")
    elif not new_triggers:
        log(f"{len(active)} existing alert(s) still active (already notified)")
    else:
        log(f"{len(new_triggers)} NEW alert(s) triggered")

    log("Check complete")


# ─── CLI ─────────────────────────────────────────────────────────────────────

def setup_telegram():
    """Interactive Telegram setup."""
    print("\n🤖 Telegram Bot Setup")
    print("=" * 40)
    print("1. Open Telegram and search for @BotFather")
    print("2. Send /newbot and follow the prompts")
    print("3. Copy the bot token below\n")

    token = input("Bot token: ").strip()
    if not token:
        print("Aborted.")
        return

    print(f"\n4. Now send any message to your bot")
    print(f"5. Then visit: https://api.telegram.org/bot{token}/getUpdates")
    print(f"6. Find your chat_id in the response\n")

    chat_id = input("Chat ID: ").strip()
    if not chat_id:
        print("Aborted.")
        return

    config = load_config()
    config["telegram_bot_token"] = token
    config["telegram_chat_id"] = chat_id
    CONFIG_FILE.write_text(json.dumps(config, indent=2))

    # Test
    print("\nSending test message...")
    send_telegram("✅ *Investment Monitor connected!*\nYou'll receive alerts here.", config)
    print("Done! Check your Telegram.")


def print_status():
    """Print current alert status."""
    prices = fetch_prices()
    if not prices:
        print("Failed to fetch prices.")
        return

    results = evaluate_alerts(prices)
    portfolio = calculate_portfolio(prices)
    btc_reserve, btc_note = calculate_btc_reserve()

    print("\n" + "=" * 60)
    print("  INVESTMENT MONITOR — STATUS")
    print("=" * 60)

    for r in results:
        price_str = format_price(r["price"], r["ticker"])
        status_icon = {"ok": "🟢", "watch": "🟡", "action": "🔴", "unknown": "⚪"}.get(r["status"], "⚪")
        dist = r.get("dist_text") or ""
        watch_str = format_price(r["watch"], r["ticker"]) if r.get("watch") else ""
        action_str = format_price(r["action"], r["ticker"]) if r.get("action") else ""
        levels = f"W:{watch_str} A:{action_str}" if watch_str else ""
        print(f"  {status_icon} {r['name']:>12}  {price_str:>12}  {levels:>20}  {dist:>24}  {r['status'].upper()}")

    print(f"\n  💰 Portfolio: ${portfolio['total_value']:,.0f} ({portfolio['total_gain_pct']:+.1f}%)")
    print(f"  ₿  BTC Reserve: ${btc_reserve:,} ({btc_note})")
    print("=" * 60 + "\n")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Investment Price Monitor")
    parser.add_argument("command", nargs="?", default="check",
                        choices=["check", "status", "digest", "setup-telegram", "test"],
                        help="Command to run")
    args = parser.parse_args()

    if args.command == "check":
        run_check()
    elif args.command == "digest":
        run_check(force_digest=True)
    elif args.command == "status":
        print_status()
    elif args.command == "setup-telegram":
        setup_telegram()
    elif args.command == "test":
        send_macos_notification(
            "🧪 Test Alert",
            "Investment Monitor is working!",
            subtitle="This is a test notification",
            sound="Glass"
        )
        config = load_config()
        if config.get("telegram_bot_token"):
            send_telegram("🧪 *Test Alert*\nInvestment Monitor is working!", config)
            print("Test sent to macOS + Telegram")
        else:
            print("Test sent to macOS (Telegram not configured)")

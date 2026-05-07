#!/usr/bin/env python3
"""
Yogurt Stock Monitor
====================
Checks the PriceSmart Costa Rica page for the Member's Selection Greek Yogurt
once daily and pings Telegram:
  - 🚨 ALERT message when the product is back in stock
  - 📦 regular status message when it's still out of stock

PriceSmart's site is behind a TLS-fingerprinting bot blocker (Akamai), so
plain `requests` returns 403. We use `curl_cffi` which impersonates Chrome's
TLS handshake to fetch the page like a real browser.
"""

import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

try:
    from curl_cffi import requests as curl_requests
except ImportError:
    print("ERROR: curl_cffi not installed. Run: pip install curl_cffi")
    sys.exit(1)

# ─── CONFIGURATION ──────────────────────────────────────────────────────────

SCRIPT_DIR = Path(__file__).parent
CONFIG_FILE = SCRIPT_DIR / "config.json"
STATE_FILE = SCRIPT_DIR / "yogurt_state.json"
LOG_FILE = SCRIPT_DIR / "yogurt_monitor.log"

COSTA_RICA_TZ = timezone(timedelta(hours=-6))

PRODUCT = {
    "name": "Members Selection Greek Yogurt 907g",
    "url": "https://www.pricesmart.com/es-cr/producto/members-selection-yogurt-griego-natural-sin-grasa-907-g-2-lb-98930/98930",
    "id": "98930",
}

# Override the watched product via env vars (used by the one-shot test workflow).
if os.getenv("YOGURT_URL"):
    PRODUCT["url"] = os.environ["YOGURT_URL"]
if os.getenv("YOGURT_NAME"):
    PRODUCT["name"] = os.environ["YOGURT_NAME"]

# Spanish/English phrases that indicate the product is OUT of stock.
# If any of these appear we treat it as out-of-stock.
OUT_OF_STOCK_MARKERS = [
    "agotado",
    "sin existencias",
    "no disponible",
    "fuera de stock",
    "out of stock",
    "no hay stock",
    "producto no disponible",
    "notificarme cuando",  # "notificarme cuando esté disponible"
    "notify me when",
]

# Phrases that indicate the product IS in stock.
IN_STOCK_MARKERS = [
    "agregar al carrito",
    "añadir al carrito",
    "add to cart",
    "comprar ahora",
]


# ─── LOGGING ────────────────────────────────────────────────────────────────

def log(msg):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{timestamp}] {msg}"
    print(line)
    try:
        with open(LOG_FILE, "a") as f:
            f.write(line + "\n")
        lines = LOG_FILE.read_text().splitlines()
        if len(lines) > 1000:
            LOG_FILE.write_text("\n".join(lines[-500:]) + "\n")
    except Exception:
        pass


# ─── CONFIG / STATE ─────────────────────────────────────────────────────────

def load_config():
    if os.getenv("TELEGRAM_BOT_TOKEN"):
        return {
            "telegram_bot_token": os.getenv("TELEGRAM_BOT_TOKEN"),
            "telegram_chat_id": os.getenv("TELEGRAM_CHAT_ID"),
        }
    if CONFIG_FILE.exists():
        try:
            return json.loads(CONFIG_FILE.read_text())
        except Exception:
            return {}
    return {}


def load_state():
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except Exception:
            return {}
    return {}


def save_state(state):
    STATE_FILE.write_text(json.dumps(state, indent=2))


# ─── PAGE FETCH ─────────────────────────────────────────────────────────────

def fetch_page(url):
    """Fetch the product page using a Chrome TLS fingerprint to bypass bot block."""
    headers = {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "es-CR,es;q=0.9,en-US;q=0.8,en;q=0.7",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
        "Upgrade-Insecure-Requests": "1",
    }
    resp = curl_requests.get(url, headers=headers, impersonate="chrome124", timeout=30)
    log(f"Fetched {url} → HTTP {resp.status_code} ({len(resp.text)} bytes)")
    if resp.status_code != 200:
        raise RuntimeError(f"HTTP {resp.status_code} fetching product page")
    return resp.text


# ─── STOCK DETECTION ────────────────────────────────────────────────────────

def check_stock(html):
    """
    Inspect the rendered HTML for stock indicators.
    Returns: ("in_stock" | "out_of_stock" | "unknown", evidence_string)
    """
    text = html.lower()

    out_hits = [m for m in OUT_OF_STOCK_MARKERS if m in text]
    in_hits = [m for m in IN_STOCK_MARKERS if m in text]

    # Try to extract the price (best-effort, for the message body)
    price = None
    m = re.search(r'"price"\s*:\s*"?(\d+(?:[.,]\d+)?)', html)
    if m:
        price = m.group(1)

    if out_hits and not in_hits:
        return "out_of_stock", f"matched: {', '.join(out_hits)}", price
    if in_hits and not out_hits:
        return "in_stock", f"matched: {', '.join(in_hits)}", price
    if in_hits and out_hits:
        # Ambiguous — page might contain both add-to-cart template and an
        # out-of-stock notice. Lean on the out-of-stock signal as the safer
        # default (avoids false "back in stock" alerts).
        return "out_of_stock", f"ambiguous (in: {in_hits}, out: {out_hits})", price
    return "unknown", "no stock markers matched", price


# ─── TELEGRAM ───────────────────────────────────────────────────────────────

def send_telegram(message, config):
    bot_token = config.get("telegram_bot_token")
    chat_id = config.get("telegram_chat_id")
    if not bot_token or not chat_id:
        log("Telegram not configured — skipping")
        return False
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": False,
    }
    try:
        resp = requests.post(url, json=payload, timeout=10)
        if resp.status_code == 200:
            log("Telegram message sent")
            return True
        log(f"Telegram failed: {resp.status_code} {resp.text[:120]}")
    except Exception as e:
        log(f"Telegram error: {e}")
    return False


# ─── MAIN ───────────────────────────────────────────────────────────────────

def run_check():
    log("=" * 50)
    log("Starting yogurt stock check")

    config = load_config()
    has_token = bool(config.get("telegram_bot_token"))
    has_chat = bool(config.get("telegram_chat_id"))
    log(f"Telegram config — token: {'set' if has_token else 'MISSING'}, chat_id: {'set' if has_chat else 'MISSING'}")
    if not (has_token and has_chat):
        log("ABORT: Telegram secrets not configured")
        sys.exit(2)

    state = load_state()
    prev_status = state.get("status", "unknown")

    now_cr = datetime.now(COSTA_RICA_TZ)
    timestamp = now_cr.strftime("%a, %b %d @ %I:%M %p") + " (GMT-6)"

    try:
        html = fetch_page(PRODUCT["url"])
    except Exception as e:
        log(f"Fetch failed: {e}")
        send_telegram(
            f"⚠️ <b>Yogurt Monitor — fetch failed</b>\n"
            f"<i>{timestamp}</i>\n\n"
            f"Could not reach PriceSmart: <code>{e}</code>",
            config,
        )
        return

    status, evidence, price = check_stock(html)
    log(f"Status: {status} ({evidence})  price={price}")

    price_line = f"\nPrice: <code>₡{price}</code>" if price else ""
    link = f'<a href="{PRODUCT["url"]}">{PRODUCT["name"]}</a>'

    if status == "in_stock" and prev_status != "in_stock":
        # Transition into stock — high-priority alert.
        msg = (
            f"🚨🥛 <b>YOGURT IS BACK IN STOCK!</b>\n"
            f"<i>{timestamp}</i>\n\n"
            f"{link}{price_line}\n\n"
            f"→ Go buy it before it's gone."
        )
    elif status == "in_stock":
        # Still in stock — softer reminder so you don't tune it out.
        msg = (
            f"🥛 <b>Yogurt still in stock</b>\n"
            f"<i>{timestamp}</i>\n\n"
            f"{link}{price_line}"
        )
    elif status == "out_of_stock":
        msg = (
            f"📦 <b>Yogurt still out of stock</b>\n"
            f"<i>{timestamp}</i>\n\n"
            f"{link}{price_line}\n"
            f"<i>Will check again tomorrow.</i>"
        )
    else:
        msg = (
            f"❓ <b>Yogurt stock status unclear</b>\n"
            f"<i>{timestamp}</i>\n\n"
            f"{link}\n"
            f"<i>Page loaded but stock indicators didn't match — markup may have changed.</i>"
        )

    send_telegram(msg, config)

    state["status"] = status
    state["last_checked"] = now_cr.isoformat()
    state["last_evidence"] = evidence
    save_state(state)

    log("Check complete")


# ─── CLI ────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Yogurt Stock Monitor")
    parser.add_argument(
        "command",
        nargs="?",
        default="check",
        choices=["check", "test", "status"],
        help="Command to run",
    )
    args = parser.parse_args()

    if args.command == "check":
        try:
            run_check()
        except SystemExit:
            raise
        except Exception as e:
            log(f"FATAL: {type(e).__name__}: {e}")
            try:
                send_telegram(
                    f"💥 <b>Yogurt Monitor crashed</b>\n<code>{type(e).__name__}: {e}</code>",
                    load_config(),
                )
            except Exception:
                pass
            raise
    elif args.command == "test":
        config = load_config()
        ok = send_telegram(
            "🧪 <b>Yogurt Monitor test</b>\nTelegram wiring works.",
            config,
        )
        print("Test message sent." if ok else "Telegram not configured or failed.")
    elif args.command == "status":
        state = load_state()
        if not state:
            print("No prior state — run `check` first.")
        else:
            print(json.dumps(state, indent=2))

#!/usr/bin/env python3
"""
Yogurt Stock Monitor
====================
Checks the PriceSmart Costa Rica page for a yogurt SKU once daily and pings
Telegram:
  - 🚨 ALERT message when the product is back in stock
  - 📦 regular status message when it's still out of stock

PriceSmart's product page is a Nuxt SPA: the static HTML only contains i18n
label templates ("Out of Stock", "Add to Cart") that are present regardless
of the actual stock state, and the SKU-specific inventory is fetched by JS
after the page loads. We therefore use Playwright (headless Chromium) to
render the page, then read the rendered DOM's visible text to decide.
"""

import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from html import escape as html_escape
from pathlib import Path

import requests

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    print("ERROR: playwright not installed. Run: pip install playwright && playwright install chromium")
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

def fetch_page(url, debug=False):
    """
    Render the product page with headless Chromium and return
    (visible_body_text, full_html). Visible text is what the user actually
    sees, which is what we use for stock detection.
    """
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            ),
            locale="es-CR",
            viewport={"width": 1280, "height": 900},
        )
        page = context.new_page()
        log(f"Navigating to {url}")
        page.goto(url, wait_until="domcontentloaded", timeout=60000)
        # Let client-side fetches (inventory, price) complete
        try:
            page.wait_for_load_state("networkidle", timeout=30000)
        except Exception:
            log("networkidle timeout — continuing anyway")
        # Scroll to bottom and back to trigger any lazy-loaded sections, then
        # give hydration extra time to settle.
        try:
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            page.wait_for_timeout(1500)
            page.evaluate("window.scrollTo(0, 0)")
        except Exception:
            pass
        page.wait_for_timeout(5000)

        body_text = page.locator("body").inner_text()
        full_html = page.content()
        log(f"Rendered page: body={len(body_text)} chars, html={len(full_html)} chars")

        if debug:
            (SCRIPT_DIR / "page_debug.html").write_text(full_html)
            (SCRIPT_DIR / "page_debug.txt").write_text(body_text)
            try:
                page.screenshot(path=str(SCRIPT_DIR / "page_debug.png"), full_page=True)
                log("Saved debug HTML + body text + screenshot")
            except Exception as e:
                log(f"Screenshot failed: {e}")

        browser.close()
        return body_text, full_html


# ─── STOCK DETECTION ────────────────────────────────────────────────────────

def find_jsonld_availability(html):
    """
    Look for schema.org Product availability in <script type="application/ld+json">
    blocks. Returns ("in_stock" | "out_of_stock" | None, raw availability string or None).
    """
    blocks = re.findall(
        r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        html,
        flags=re.DOTALL | re.IGNORECASE,
    )
    for raw in blocks:
        try:
            data = json.loads(raw.strip())
        except Exception:
            continue
        # JSON-LD can be a single object, a list, or a graph
        candidates = data if isinstance(data, list) else [data]
        if isinstance(data, dict) and "@graph" in data:
            candidates = data["@graph"]
        for item in candidates:
            if not isinstance(item, dict):
                continue
            offers = item.get("offers")
            if not offers:
                continue
            offers_list = offers if isinstance(offers, list) else [offers]
            for offer in offers_list:
                if not isinstance(offer, dict):
                    continue
                avail = str(offer.get("availability", "")).lower()
                if not avail:
                    continue
                if "instock" in avail or "in_stock" in avail:
                    return "in_stock", avail
                if "outofstock" in avail or "out_of_stock" in avail or "soldout" in avail:
                    return "out_of_stock", avail
    return None, None


def find_inline_stock_state(html):
    """
    Look for an inline JSON state blob (Next.js __NEXT_DATA__, Nuxt __NUXT__,
    or any JSON with an "inStock"/"stockStatus" field).
    Returns ("in_stock" | "out_of_stock" | None, evidence_str).
    """
    # Common boolean / status keys e-commerce frameworks expose
    patterns = [
        (r'"inStock"\s*:\s*(true|false)',          lambda v: "in_stock" if v == "true" else "out_of_stock"),
        (r'"isInStock"\s*:\s*(true|false)',        lambda v: "in_stock" if v == "true" else "out_of_stock"),
        (r'"available"\s*:\s*(true|false)',        lambda v: "in_stock" if v == "true" else "out_of_stock"),
        (r'"outOfStock"\s*:\s*(true|false)',       lambda v: "out_of_stock" if v == "true" else "in_stock"),
        (r'"stockStatus"\s*:\s*"([^"]+)"',         lambda v: "in_stock" if "in" in v.lower() else "out_of_stock"),
        (r'"availability"\s*:\s*"([^"]+)"',        lambda v: "in_stock" if "instock" in v.lower().replace(" ", "") else "out_of_stock"),
    ]
    for pat, classify in patterns:
        m = re.search(pat, html)
        if m:
            v = m.group(1)
            return classify(v), f'{pat.split("(")[0]}={v}'
    return None, None


def check_stock(body_text, full_html):
    """
    Decide stock status from the rendered page.
    Priority order:
      1. JSON-LD schema.org availability (if PriceSmart ever adds it)
      2. Visible body text — what the user actually sees on screen
      3. Inline JSON state blobs (last-resort fallback)
    Returns: (status, evidence, price)
    """
    price = None
    m = re.search(r'"price"\s*:\s*"?(\d+(?:[.,]\d+)?)', full_html)
    if m:
        price = m.group(1)

    status, evidence = find_jsonld_availability(full_html)
    if status:
        return status, f"json-ld: {evidence}", price

    text = body_text.lower()
    out_hits = [m for m in OUT_OF_STOCK_MARKERS if m in text]
    in_hits = [m for m in IN_STOCK_MARKERS if m in text]

    if out_hits and not in_hits:
        return "out_of_stock", f"visible-text: {', '.join(out_hits)}", price
    if in_hits and not out_hits:
        return "in_stock", f"visible-text: {', '.join(in_hits)}", price
    if in_hits and out_hits:
        return "unknown", f"visible-text ambiguous (in: {in_hits}, out: {out_hits})", price

    status, evidence = find_inline_stock_state(full_html)
    if status:
        return status, f"inline-json: {evidence}", price

    return "unknown", "no stock markers matched in visible text", price


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

    debug = os.getenv("DEBUG") == "1"
    try:
        body_text, full_html = fetch_page(PRODUCT["url"], debug=debug)
    except Exception as e:
        log(f"Fetch failed: {e}")
        send_telegram(
            f"⚠️ <b>Yogurt Monitor — fetch failed</b>\n"
            f"<i>{timestamp}</i>\n\n"
            f"Could not render PriceSmart page: <code>{html_escape(str(e))}</code>",
            config,
        )
        return

    status, evidence, price = check_stock(body_text, full_html)
    log(f"Status: {status} ({evidence})  price={price}")

    if debug:
        # Dump the full visible body text so we can see exactly what
        # PriceSmart renders and pick the right marker.
        flat = " / ".join(line.strip() for line in body_text.splitlines() if line.strip())
        budget = 3500
        debug_msg = (
            f"🔍 <b>Debug — {html_escape(PRODUCT['name'])}</b>\n"
            f"<i>{timestamp}</i>\n"
            f"Status: <code>{html_escape(status)}</code>\n"
            f"Evidence: <code>{html_escape(evidence)}</code>\n"
            f"Visible text size: <code>{len(body_text)}</code>\n\n"
            f"<b>Full visible text:</b>\n<code>{html_escape(flat[:budget])}</code>"
        )
        if len(flat) > budget:
            debug_msg += f"\n<i>…truncated ({len(flat) - budget} more chars)</i>"
        send_telegram(debug_msg, config)

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

#!/usr/bin/env python3
"""
Daily digest sender — Yogurt Watch
==================================
Runs after `yogurt_monitor.py check` on the GitHub Actions cron. For each
active Stripe subscription, looks up the most recent Checkout Session to
read the customer's optional Telegram chat ID custom field, then sends:
  - a Telegram message (if a chat ID was supplied), or
  - an email via Resend (using the customer's Stripe-collected email)

Stripe is the only subscriber database — no separate KV/DB.

Environment variables required:
  STRIPE_SECRET_KEY      sk_live_… or sk_test_…
  RESEND_API_KEY         re_…
  FROM_EMAIL             "Yogurt Watch <updates@your-domain.app>"
  TELEGRAM_BOT_TOKEN     bot token (reused from yogurt_monitor.py)

Skips silently if STRIPE_SECRET_KEY is not set (lets the workflow no-op
when subscription billing isn't wired yet).
"""

import json
import os
import sys
from html import escape
from pathlib import Path

import requests

SCRIPT_DIR = Path(__file__).parent
STATUS_FILE = SCRIPT_DIR / "status.json"

# Custom-field key the user configures on the Stripe Payment Link to
# collect the optional Telegram chat ID.
TELEGRAM_FIELD_KEY = "telegram_chat_id"


def log(msg):
    print(f"[digest] {msg}", flush=True)


def load_status():
    if not STATUS_FILE.exists():
        log("status.json missing — aborting digest")
        sys.exit(0)
    return json.loads(STATUS_FILE.read_text())


def stripe_paginate(stripe, list_fn, **params):
    """Yield every item across paginated Stripe list endpoints."""
    starting_after = None
    while True:
        resp = list_fn(limit=100, starting_after=starting_after, **params)
        for item in resp.data:
            yield item
        if not resp.has_more:
            break
        starting_after = resp.data[-1].id


def get_telegram_chat_id(stripe, subscription_id):
    """Look up the custom field value from the original Checkout Session."""
    try:
        sessions = stripe.checkout.Session.list(subscription=subscription_id, limit=1)
    except Exception as e:
        log(f"  session.list failed for {subscription_id}: {e}")
        return None
    if not sessions.data:
        return None
    session = sessions.data[0]
    for field in (session.custom_fields or []):
        if field.get("key") == TELEGRAM_FIELD_KEY:
            value = (field.get("text") or {}).get("value")
            if value and value.strip():
                return value.strip()
    return None


# ─── Message building ──────────────────────────────────────────────────────

def build_email_html(status, product_name, product_url, checked_at):
    in_stock = status == "in_stock"
    badge = "🥛 IN STOCK" if in_stock else ("📦 Still out" if status == "out_of_stock" else "❓ Unclear")
    headline = (
        "It's back in stock!"
        if in_stock
        else ("Still out of stock today." if status == "out_of_stock" else "Stock status unclear today.")
    )
    cta = (
        f'<p style="margin:24px 0;"><a href="{product_url}" '
        f'style="background:#1a1a1a;color:#fff;padding:12px 24px;border-radius:10px;'
        f'text-decoration:none;font-weight:600;">Open on PriceSmart →</a></p>'
        if in_stock else ""
    )
    return (
        '<!DOCTYPE html><html><body style="font-family:-apple-system,BlinkMacSystemFont,'
        '\'Segoe UI\',Roboto,sans-serif;background:#faf8f3;margin:0;padding:32px 16px;color:#1a1a1a;">'
        '<div style="max-width:520px;margin:0 auto;background:#fff;border-radius:16px;'
        'padding:32px;border:1px solid #ececec;">'
        f'<p style="margin:0 0 8px;font-size:11px;letter-spacing:0.12em;text-transform:uppercase;'
        f'color:#5e5e5e;">{escape(badge)}</p>'
        f'<h1 style="margin:0 0 12px;font-size:24px;letter-spacing:-0.02em;">{escape(headline)}</h1>'
        f'<p style="margin:0 0 4px;color:#5e5e5e;font-size:14px;">{escape(product_name)}</p>'
        f'<p style="margin:0 0 24px;color:#9a9a9a;font-size:13px;">Checked {escape(checked_at)}</p>'
        f'{cta}'
        '<p style="margin:24px 0 0;font-size:11px;color:#9a9a9a;border-top:1px solid #ececec;'
        'padding-top:16px;">You\'re subscribed to Yogurt Watch. '
        '<a href="https://billing.stripe.com/p/login" style="color:#1f6feb;">Manage subscription</a>.</p>'
        '</div></body></html>'
    )


def build_telegram(status, product_name, product_url, checked_at):
    if status == "in_stock":
        headline = "🚨🥛 <b>YOGURT IS BACK IN STOCK!</b>"
        cta = "\n\n→ Go buy it before it's gone."
    elif status == "out_of_stock":
        headline = "📦 <b>Still out of stock</b>"
        cta = ""
    else:
        headline = "❓ <b>Stock status unclear</b>"
        cta = ""
    return (
        f"{headline}\n<i>{escape(checked_at)}</i>\n\n"
        f'<a href="{product_url}">{escape(product_name)}</a>{cta}'
    )


# ─── Senders ──────────────────────────────────────────────────────────────

def send_email(api_key, from_email, to_email, subject, html_body):
    resp = requests.post(
        "https://api.resend.com/emails",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={"from": from_email, "to": [to_email], "subject": subject, "html": html_body},
        timeout=15,
    )
    if resp.status_code >= 400:
        raise RuntimeError(f"Resend {resp.status_code}: {resp.text[:200]}")


def send_telegram(bot_token, chat_id, text):
    resp = requests.post(
        f"https://api.telegram.org/bot{bot_token}/sendMessage",
        json={"chat_id": chat_id, "text": text, "parse_mode": "HTML", "disable_web_page_preview": False},
        timeout=15,
    )
    if resp.status_code >= 400:
        raise RuntimeError(f"Telegram {resp.status_code}: {resp.text[:200]}")


# ─── Main ─────────────────────────────────────────────────────────────────

def main():
    stripe_key = os.getenv("STRIPE_SECRET_KEY")
    if not stripe_key:
        log("STRIPE_SECRET_KEY not set — skipping digest send")
        return

    try:
        import stripe
    except ImportError:
        log("ERROR: stripe SDK not installed (pip install stripe)")
        sys.exit(1)

    stripe.api_key = stripe_key

    status_data = load_status()
    status = status_data.get("status", "unknown")
    product = status_data.get("product") or {}
    product_name = product.get("name", "Greek Yogurt")
    product_url = product.get("url", "https://www.pricesmart.com/")
    checked_at = status_data.get("last_checked_human", "")

    subject = (
        f"🥛 {product_name} is back in stock"
        if status == "in_stock"
        else (f"📦 {product_name} — still out of stock" if status == "out_of_stock" else f"{product_name} — daily check")
    )

    resend_key = os.getenv("RESEND_API_KEY")
    from_email = os.getenv("FROM_EMAIL", "Yogurt Watch <onboarding@resend.dev>")
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN")

    log(f"Status={status}; subject={subject!r}")

    sent = 0
    failed = 0
    no_channel = 0

    for sub in stripe_paginate(stripe, stripe.Subscription.list, status="active"):
        try:
            customer = stripe.Customer.retrieve(sub.customer)
            email = (customer.email or "").strip() if hasattr(customer, "email") else ""
            tg_chat_id = get_telegram_chat_id(stripe, sub.id)

            if tg_chat_id and bot_token:
                send_telegram(
                    bot_token, tg_chat_id,
                    build_telegram(status, product_name, product_url, checked_at),
                )
                sent += 1
                log(f"  ✓ Telegram → {tg_chat_id} (sub {sub.id})")
            elif email and resend_key:
                send_email(
                    resend_key, from_email, email, subject,
                    build_email_html(status, product_name, product_url, checked_at),
                )
                sent += 1
                log(f"  ✓ Email → {email} (sub {sub.id})")
            else:
                no_channel += 1
                log(f"  ⚠ no usable channel for sub {sub.id} (email={email!r}, tg={tg_chat_id!r})")
        except Exception as e:
            failed += 1
            log(f"  ✗ sub {sub.id}: {e}")

    log(f"Done: sent={sent} failed={failed} no_channel={no_channel}")


if __name__ == "__main__":
    main()

// GET /api/digest
// Vercel cron handler — fires daily, fetches the latest stock status, and
// sends a digest to every active subscriber via their chosen channel.
//
// Requires Authorization: Bearer ${CRON_SECRET} (Vercel cron sets this
// automatically when CRON_SECRET is set).

import { kv } from "@vercel/kv";
import { Resend } from "resend";

const STATUS_URL = "https://raw.githubusercontent.com/crox777/investment-monitor/main/status.json";
const FROM_EMAIL = process.env.FROM_EMAIL || "Yogurt Watch <updates@yogurtwatch.app>";

function html(strings, ...values) {
  return strings.reduce((acc, s, i) => acc + s + (values[i] ?? ""), "");
}

function buildEmail(status, productName, productUrl, checkedAt) {
  const inStock = status === "in_stock";
  const badge = inStock ? "🥛 IN STOCK" : status === "out_of_stock" ? "📦 Still out" : "❓ Unclear";
  const headline = inStock
    ? "It's back in stock!"
    : status === "out_of_stock"
    ? "Still out of stock today."
    : "Stock status unclear today.";
  const cta = inStock
    ? `<p style="margin:24px 0;"><a href="${productUrl}" style="background:#1a1a1a;color:#fff;padding:12px 24px;border-radius:10px;text-decoration:none;font-weight:600;">Open on PriceSmart →</a></p>`
    : "";
  return html`<!DOCTYPE html>
<html><body style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:#faf8f3;margin:0;padding:32px 16px;color:#1a1a1a;">
  <div style="max-width:520px;margin:0 auto;background:#fff;border-radius:16px;padding:32px;border:1px solid #ececec;">
    <p style="margin:0 0 8px;font-size:11px;letter-spacing:0.12em;text-transform:uppercase;color:#5e5e5e;">${badge}</p>
    <h1 style="margin:0 0 12px;font-size:24px;letter-spacing:-0.02em;">${headline}</h1>
    <p style="margin:0 0 4px;color:#5e5e5e;font-size:14px;">${productName}</p>
    <p style="margin:0 0 24px;color:#9a9a9a;font-size:13px;">Checked ${checkedAt}</p>
    ${cta}
    <p style="margin:24px 0 0;font-size:11px;color:#9a9a9a;border-top:1px solid #ececec;padding-top:16px;">
      You're subscribed to Yogurt Watch. <a href="https://billing.stripe.com/p/login" style="color:#1f6feb;">Manage subscription</a>.
    </p>
  </div>
</body></html>`;
}

function buildTelegram(status, productName, productUrl, checkedAt) {
  const inStock = status === "in_stock";
  const headline = inStock
    ? "🚨🥛 <b>YOGURT IS BACK IN STOCK!</b>"
    : status === "out_of_stock"
    ? "📦 <b>Still out of stock</b>"
    : "❓ <b>Stock status unclear</b>";
  const link = `<a href="${productUrl}">${productName}</a>`;
  const cta = inStock ? "\n\n→ Go buy it before it's gone." : "";
  return `${headline}\n<i>${checkedAt}</i>\n\n${link}${cta}`;
}

async function sendEmail(resend, to, subject, htmlBody) {
  await resend.emails.send({ from: FROM_EMAIL, to, subject, html: htmlBody });
}

async function sendTelegram(token, chatId, text) {
  const resp = await fetch(`https://api.telegram.org/bot${token}/sendMessage`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ chat_id: chatId, text, parse_mode: "HTML", disable_web_page_preview: false }),
  });
  if (!resp.ok) {
    const t = await resp.text();
    throw new Error(`Telegram ${resp.status}: ${t.slice(0, 120)}`);
  }
}

export default async function handler(req, res) {
  // Vercel cron sets Authorization: Bearer <CRON_SECRET>
  const auth = req.headers.authorization || "";
  if (process.env.CRON_SECRET && auth !== `Bearer ${process.env.CRON_SECRET}`) {
    return res.status(401).json({ error: "Unauthorized" });
  }

  const statusResp = await fetch(STATUS_URL, { cache: "no-store" });
  if (!statusResp.ok) {
    return res.status(502).json({ error: `status.json fetch failed: ${statusResp.status}` });
  }
  const status = await statusResp.json();
  const productName = status.product?.name || "Greek Yogurt";
  const productUrl = status.product?.url || "https://www.pricesmart.com/";
  const checkedAt = status.last_checked_human || "";
  const subject = status.status === "in_stock"
    ? `🥛 ${productName} is back in stock`
    : status.status === "out_of_stock"
    ? `📦 ${productName} — still out of stock`
    : `${productName} — daily check`;

  const ids = await kv.smembers("subs:active");
  const resend = process.env.RESEND_API_KEY ? new Resend(process.env.RESEND_API_KEY) : null;

  let sent = 0, failed = 0;
  const errors = [];

  for (const id of ids) {
    const sub = await kv.get(`sub:${id}`);
    if (!sub || !sub.active) continue;

    try {
      if (sub.channel === "telegram" && sub.telegram_chat_id && process.env.TELEGRAM_BOT_TOKEN) {
        await sendTelegram(
          process.env.TELEGRAM_BOT_TOKEN,
          sub.telegram_chat_id,
          buildTelegram(status.status, productName, productUrl, checkedAt),
        );
      } else if (sub.email && resend) {
        await sendEmail(
          resend,
          sub.email,
          subject,
          buildEmail(status.status, productName, productUrl, checkedAt),
        );
      } else {
        failed++;
        errors.push(`sub ${id}: no usable channel`);
        continue;
      }
      sent++;
    } catch (err) {
      failed++;
      errors.push(`sub ${id}: ${err.message || err}`);
    }
  }

  return res.status(200).json({
    status: status.status,
    subscribers: ids.length,
    sent,
    failed,
    errors: errors.slice(0, 10),
  });
}

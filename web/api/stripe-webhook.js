// POST /api/stripe-webhook
// Receives Stripe events and persists subscriber state in Vercel KV.
// Listens for:
//   - checkout.session.completed → save subscriber
//   - customer.subscription.deleted → mark inactive
//   - customer.subscription.updated → keep status in sync

import Stripe from "stripe";
import { kv } from "@vercel/kv";

const stripe = new Stripe(process.env.STRIPE_SECRET_KEY);
const WEBHOOK_SECRET = process.env.STRIPE_WEBHOOK_SECRET;

// Vercel needs the raw body for Stripe signature verification.
export const config = {
  api: { bodyParser: false },
};

async function readRaw(req) {
  const chunks = [];
  for await (const chunk of req) chunks.push(typeof chunk === "string" ? Buffer.from(chunk) : chunk);
  return Buffer.concat(chunks);
}

async function saveSubscriber(sub) {
  const key = `sub:${sub.id}`;
  await kv.set(key, sub);
  await kv.sadd("subs:active", sub.id);
}

async function deactivateSubscriber(subscriptionId) {
  const key = `sub:${subscriptionId}`;
  const existing = await kv.get(key);
  if (existing) {
    await kv.set(key, { ...existing, active: false, deactivated_at: new Date().toISOString() });
  }
  await kv.srem("subs:active", subscriptionId);
}

export default async function handler(req, res) {
  if (req.method !== "POST") {
    return res.status(405).json({ error: "Method not allowed" });
  }

  const sig = req.headers["stripe-signature"];
  let event;
  try {
    const raw = await readRaw(req);
    event = stripe.webhooks.constructEvent(raw, sig, WEBHOOK_SECRET);
  } catch (err) {
    console.error("Stripe webhook signature failed:", err.message);
    return res.status(400).send(`Webhook Error: ${err.message}`);
  }

  try {
    switch (event.type) {
      case "checkout.session.completed": {
        const session = event.data.object;
        const md = session.metadata || {};
        const sub = {
          id: session.subscription, // Stripe subscription ID is our key
          stripe_customer: session.customer,
          email: md.email || session.customer_email,
          channel: md.channel || "email",
          telegram_chat_id: md.telegram_chat_id || null,
          active: true,
          created_at: new Date().toISOString(),
        };
        await saveSubscriber(sub);
        console.log("Subscriber saved:", sub.id);
        break;
      }
      case "customer.subscription.deleted": {
        const sub = event.data.object;
        await deactivateSubscriber(sub.id);
        console.log("Subscriber deactivated:", sub.id);
        break;
      }
      case "customer.subscription.updated": {
        const sub = event.data.object;
        if (["canceled", "unpaid", "incomplete_expired"].includes(sub.status)) {
          await deactivateSubscriber(sub.id);
        }
        break;
      }
      default:
        // No-op for other event types.
        break;
    }
    return res.status(200).json({ received: true });
  } catch (err) {
    console.error("Stripe webhook handler error:", err);
    return res.status(500).json({ error: err.message });
  }
}

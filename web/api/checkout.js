// POST /api/checkout
// Body: { channel: "email" | "telegram", email: string, telegram_chat_id?: string }
// Creates a Stripe Checkout session and returns { url }.

import Stripe from "stripe";

const stripe = new Stripe(process.env.STRIPE_SECRET_KEY);

export default async function handler(req, res) {
  if (req.method !== "POST") {
    return res.status(405).json({ error: "Method not allowed" });
  }

  const { channel, email, telegram_chat_id } = req.body || {};

  if (!email || !/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email)) {
    return res.status(400).json({ error: "Valid email required" });
  }
  if (channel !== "email" && channel !== "telegram") {
    return res.status(400).json({ error: "Invalid channel" });
  }
  if (channel === "telegram" && (!telegram_chat_id || !/^-?\d+$/.test(String(telegram_chat_id)))) {
    return res.status(400).json({ error: "Telegram chat ID must be a number" });
  }

  const origin = `https://${req.headers.host}`;

  try {
    const session = await stripe.checkout.sessions.create({
      mode: "subscription",
      payment_method_types: ["card"],
      line_items: [{ price: process.env.STRIPE_PRICE_ID, quantity: 1 }],
      customer_email: email,
      success_url: `${origin}/success.html?session_id={CHECKOUT_SESSION_ID}`,
      cancel_url: `${origin}/cancel.html`,
      // Subscriber preferences travel with the session and are read by the
      // webhook handler when the subscription becomes active.
      metadata: {
        channel,
        email,
        telegram_chat_id: telegram_chat_id ? String(telegram_chat_id) : "",
      },
      subscription_data: {
        metadata: {
          channel,
          email,
          telegram_chat_id: telegram_chat_id ? String(telegram_chat_id) : "",
        },
      },
    });
    return res.status(200).json({ url: session.url });
  } catch (err) {
    console.error("Stripe checkout error:", err);
    return res.status(500).json({ error: err.message || "Checkout failed" });
  }
}

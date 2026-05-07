// Greek Yogurt Watch — frontend
// Reads status.json from the GitHub repo (committed by the daily Actions
// workflow) and posts the subscribe form to /api/checkout.

const STATUS_URL = "https://raw.githubusercontent.com/crox777/investment-monitor/main/status.json";

const HEADLINES = {
  in_stock: "It's back in stock!",
  out_of_stock: "Still out of stock.",
  unknown: "Couldn't tell — check the page.",
  error: "Couldn't load the latest check.",
  loading: "Loading current stock…",
};

const LABELS = {
  in_stock: "In stock",
  out_of_stock: "Out of stock",
  unknown: "Status unclear",
  error: "Check failed",
  loading: "Checking…",
};

async function loadStatus() {
  const card = document.getElementById("status");
  const label = document.getElementById("status-label");
  const headline = document.getElementById("status-headline");
  const meta = document.getElementById("status-meta");
  const link = document.getElementById("product-link");

  try {
    const resp = await fetch(STATUS_URL, { cache: "no-store" });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const data = await resp.json();
    const state = data.status || "unknown";

    card.dataset.state = state;
    label.textContent = LABELS[state] || LABELS.unknown;
    headline.textContent = HEADLINES[state] || HEADLINES.unknown;

    const productName = data.product?.name || "Greek Yogurt";
    const checkedAt = data.last_checked_human || "";
    meta.textContent = `${productName} · last checked ${checkedAt}`;

    if (data.product?.url) {
      link.href = data.product.url;
      link.hidden = false;
    } else {
      link.hidden = true;
    }
  } catch (e) {
    card.dataset.state = "error";
    label.textContent = LABELS.error;
    headline.textContent = HEADLINES.error;
    meta.textContent = String(e);
  }
}

function wireChannelToggle() {
  const radios = document.querySelectorAll('input[name="channel"]');
  const emailField = document.getElementById("email-field");
  const telegramField = document.getElementById("telegram-field");
  const emailInput = document.getElementById("email");

  function update() {
    const channel = document.querySelector('input[name="channel"]:checked').value;
    if (channel === "email") {
      emailField.classList.remove("hidden");
      telegramField.classList.add("hidden");
      emailInput.required = true;
    } else {
      emailField.classList.remove("hidden"); // we still ask for email as receipt
      telegramField.classList.remove("hidden");
      emailInput.required = true;
    }
  }
  radios.forEach((r) => r.addEventListener("change", update));
  update();
}

async function handleSubmit(e) {
  e.preventDefault();
  const btn = document.getElementById("subscribe-btn");
  const errorEl = document.getElementById("subscribe-error");
  errorEl.hidden = true;

  const channel = document.querySelector('input[name="channel"]:checked').value;
  const email = document.getElementById("email").value.trim();
  const telegramChatId = document.getElementById("telegram-chat-id").value.trim();

  if (!email) {
    errorEl.textContent = "Email is required (we send the receipt there).";
    errorEl.hidden = false;
    return;
  }
  if (channel === "telegram" && !telegramChatId) {
    errorEl.textContent = "Please enter your Telegram chat ID. Open @userinfobot in Telegram to find it.";
    errorEl.hidden = false;
    return;
  }

  btn.disabled = true;
  btn.textContent = "Redirecting to Stripe…";

  try {
    const resp = await fetch("/api/checkout", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ channel, email, telegram_chat_id: telegramChatId || null }),
    });
    if (!resp.ok) {
      const text = await resp.text();
      throw new Error(text || `HTTP ${resp.status}`);
    }
    const { url } = await resp.json();
    if (!url) throw new Error("No checkout URL returned.");
    window.location.assign(url);
  } catch (err) {
    btn.disabled = false;
    btn.textContent = "Subscribe — $0.99/mo";
    errorEl.textContent = `Couldn't start checkout: ${err.message || err}`;
    errorEl.hidden = false;
  }
}

document.addEventListener("DOMContentLoaded", () => {
  loadStatus();
  wireChannelToggle();
  document.getElementById("subscribe-form").addEventListener("submit", handleSubmit);

  // Allow operators to inject a Telegram bot deep-link via window var (set in HTML if desired)
  const botUsername = window.TELEGRAM_BOT_USERNAME;
  if (botUsername) {
    document.getElementById("bot-link").href = `https://t.me/${botUsername}`;
  }
});

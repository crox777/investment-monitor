// Greek Yogurt Watch — frontend
// Fetches status.json (committed daily by GitHub Actions) and points the
// subscribe button at a Stripe Payment Link.

const CFG = window.YOGURT_CONFIG || {};

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
    const resp = await fetch(CFG.statusUrl, { cache: "no-store" });
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

function wireSubscribe() {
  const btn = document.getElementById("subscribe-btn");
  if (CFG.stripePaymentLink && !CFG.stripePaymentLink.includes("REPLACE_WITH_YOUR_LINK")) {
    btn.href = CFG.stripePaymentLink;
  } else {
    btn.addEventListener("click", (e) => {
      e.preventDefault();
      alert("Subscriptions aren't configured yet — set window.YOGURT_CONFIG.stripePaymentLink in index.html.");
    });
  }
}

document.addEventListener("DOMContentLoaded", () => {
  loadStatus();
  wireSubscribe();
});

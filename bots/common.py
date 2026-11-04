"""Shared helpers for the Kalijy bots: config, Shopify, Grok (xAI), Telegram."""
import datetime as dt
import html
import json
import logging
import os
import re
import time
from zoneinfo import ZoneInfo

import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("kalijy")


def env(name, default=None, required=False):
    val = os.getenv(name)
    if val is None or val.strip() == "":
        if required:
            raise SystemExit(f"Missing required setting: {name}")
        return default
    return val.strip()


def env_float(name, default=0.0):
    try:
        return float(env(name, default))
    except (TypeError, ValueError):
        return float(default)


def env_bool(name, default=False):
    return str(env(name, str(default))).lower() in ("1", "true", "yes", "on")


DRY_RUN = env_bool("DRY_RUN", False)
TZ = ZoneInfo(env("BOT_TIMEZONE", "Africa/Casablanca"))
REPORT_LANGUAGE = env("REPORT_LANGUAGE", "English")

# Kalijy product IDs (from the live store)
CHARGER_PRODUCT_ID = env("CHARGER_PRODUCT_ID", "gid://shopify/Product/15441476288793")
GIFT_PRODUCT_ID = env("GIFT_PRODUCT_ID", "gid://shopify/Product/15442328256793")


def utcnow():
    return dt.datetime.now(dt.timezone.utc)


def parse_ts(s):
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00"))


def esc(s):
    return html.escape(str(s if s is not None else ""), quote=False)


def money(amount, currency):
    try:
        return f"{float(amount):,.2f} {currency}"
    except (TypeError, ValueError):
        return f"{amount} {currency}"


# ---------------------------------------------------------------- Shopify ---
SHOP = env("SHOPIFY_SHOP", "hsd9ie-ij.myshopify.com")
SHOPIFY_API_VERSION = env("SHOPIFY_API_VERSION", "2026-10")
ADMIN_URL = f"https://admin.shopify.com/store/{SHOP.split('.')[0]}"
_token_cache = {"token": None, "exp": 0}


def shopify_token():
    """Static token (SHOPIFY_ADMIN_TOKEN) or client-credentials token from a Dev Dashboard app."""
    static = env("SHOPIFY_ADMIN_TOKEN")
    if static:
        return static
    if _token_cache["token"] and time.time() < _token_cache["exp"] - 300:
        return _token_cache["token"]
    r = requests.post(
        f"https://{SHOP}/admin/oauth/access_token",
        json={
            "client_id": env("SHOPIFY_CLIENT_ID", required=True),
            "client_secret": env("SHOPIFY_CLIENT_SECRET", required=True),
            "grant_type": "client_credentials",
        },
        timeout=30,
    )
    r.raise_for_status()
    data = r.json()
    _token_cache["token"] = data["access_token"]
    _token_cache["exp"] = time.time() + int(data.get("expires_in", 3600))
    return _token_cache["token"]


def gql(query, variables=None, retries=4):
    url = f"https://{SHOP}/admin/api/{SHOPIFY_API_VERSION}/graphql.json"
    for attempt in range(retries):
        r = requests.post(
            url,
            json={"query": query, "variables": variables or {}},
            headers={"X-Shopify-Access-Token": shopify_token(), "Content-Type": "application/json"},
            timeout=60,
        )
        if r.status_code in (429, 502, 503):
            time.sleep(2 * (attempt + 1))
            continue
        r.raise_for_status()
        data = r.json()
        errs = data.get("errors")
        if errs:
            if any("THROTTLED" in json.dumps(e) for e in errs):
                time.sleep(2 * (attempt + 1))
                continue
            raise RuntimeError(f"Shopify GraphQL error: {errs}")
        return data["data"]
    raise RuntimeError("Shopify GraphQL: too many retries")


ORDERS_QUERY = """
query($q:String!, $after:String){
  orders(first:100, after:$after, query:$q, sortKey:CREATED_AT, reverse:true){
    pageInfo{ hasNextPage endCursor }
    nodes{
      id name createdAt cancelledAt email customerLocale test
      displayFinancialStatus displayFulfillmentStatus
      totalPriceSet{ shopMoney{ amount currencyCode } presentmentMoney{ amount currencyCode } }
      totalRefundedSet{ shopMoney{ amount currencyCode } }
      shippingAddress{ countryCodeV2 city firstName }
      risk{ recommendation }
      lineItems(first:20){ nodes{ id title variantTitle sku quantity currentQuantity product{ id }
        originalUnitPriceSet{ shopMoney{ amount } }
        discountAllocations{ allocatedAmountSet{ shopMoney{ amount } } } } }
      fulfillments(first:10){ id createdAt displayStatus status inTransitAt deliveredAt
        trackingInfo(first:3){ number url company }
        fulfillmentLineItems(first:20){ nodes{ quantity lineItem{ id } } } }
    }
  }
}"""


def fetch_orders(search, max_pages=10):
    out, after = [], None
    for _ in range(max_pages):
        data = gql(ORDERS_QUERY, {"q": search, "after": after})["orders"]
        out.extend(data["nodes"])
        if not data["pageInfo"]["hasNextPage"]:
            break
        after = data["pageInfo"]["endCursor"]
    return out


def order_admin_link(order):
    oid = order["id"].rsplit("/", 1)[-1]
    return f'<a href="{ADMIN_URL}/orders/{oid}">{esc(order["name"])}</a>'


def qty(li):
    """Quantity still in the order (after edits/removals)."""
    q = li.get("currentQuantity")
    return li["quantity"] if q is None else q


def _is(li, pid):
    return bool(li.get("product")) and li["product"]["id"] == pid


def charger_lines(order):
    return [li for li in order["lineItems"]["nodes"] if _is(li, CHARGER_PRODUCT_ID) and qty(li) > 0]


def kit_lines(order):
    return [li for li in order["lineItems"]["nodes"] if _is(li, GIFT_PRODUCT_ID) and qty(li) > 0]


def charger_qty(order):
    return sum(qty(li) for li in charger_lines(order))


def free_units(li):
    """How many units of this line were made free by discounts (the gift kit offer)."""
    try:
        unit = float(li["originalUnitPriceSet"]["shopMoney"]["amount"])
        off = sum(float(d["allocatedAmountSet"]["shopMoney"]["amount"]) for d in li.get("discountAllocations") or [])
    except (KeyError, TypeError, ValueError):
        return 0
    return min(qty(li), int(round(off / unit))) if unit > 0 else qty(li)


def has_gift(order):
    return bool(kit_lines(order))


MAX_CHARGERS = int(env("MAX_CHARGERS_PER_ORDER", "3"))


def md_checklist(order):
    """Exactly what must be entered in MutualDropshipping for this order."""
    rows = []
    for li in charger_lines(order):
        rows.append(f'{qty(li)}× Charger {li.get("variantTitle") or ""} (SKU {li.get("sku") or "?"})'.replace("  ", " "))
    for li in kit_lines(order):
        free = free_units(li)
        tag = "FREE gift" if free == qty(li) else f"{free} free + {qty(li) - free} paid"
        rows.append(f'{qty(li)}× Gift kit, band size {li.get("variantTitle") or "?"} ({tag})')
    return rows


def order_check(order):
    """Problems with what was ordered: chargers 1-3, exactly ONE free gift kit, kit size known."""
    out = []
    n = charger_qty(order)
    kits = kit_lines(order)
    free = sum(free_units(li) for li in kits)
    if n == 0:
        if kits:
            out.append("⚠️ Gift kit ordered without a charger")
        return out
    if n > MAX_CHARGERS:
        out.append(f"⚠️ {n} chargers in one order (more than {MAX_CHARGERS}): check price and supplier stock")
    if not kits:
        out.append("🎁 No gift kit in the order: band size unknown, email the customer before submitting")
    elif free == 0:
        out.append("🎁 Gift kit was paid, not free: the free-gift discount did not apply, check the order")
    elif free > 1:
        out.append(f"⚠️ {free} free gift kits: only 1 per order is allowed, send 1")
    paid = sum(qty(li) for li in kits) - free
    if paid > 0 and free >= 1:
        out.append(f"ℹ️ Customer PAID for {paid} extra kit(s): send them too")
    for li in kits:
        if not li.get("variantTitle"):
            out.append("⚠️ Gift kit has no band size")
    return out


def shipped_vs_ordered(order):
    """Compare what the supplier reported as shipped (Shopify fulfillments) with what was ordered."""
    shipped = {}
    for f in order.get("fulfillments") or []:
        if f.get("status") in ("CANCELLED", "ERROR", "FAILURE"):
            continue
        for fl in (f.get("fulfillmentLineItems") or {}).get("nodes", []):
            lid = (fl.get("lineItem") or {}).get("id")
            shipped[lid] = shipped.get(lid, 0) + fl["quantity"]
    diffs = []
    for li in order["lineItems"]["nodes"]:
        want, got = qty(li), shipped.get(li.get("id"), 0)
        if want != got and (want or got):
            name = li["title"] + (f' ({li["variantTitle"]})' if li.get("variantTitle") else "")
            diffs.append(f"{name}: ordered {want}, shipped {got}")
    return diffs


def tracking_numbers(order):
    return [t for f in order.get("fulfillments") or [] for t in f.get("trackingInfo") or [] if t.get("number")]


# --------------------------------------------------------------------- AI ---
# AI_PROVIDER = openai (default, gpt-6-luna) or xai (Grok). Both use the same "Responses" request format.
PROVIDERS = {
    "openai": ("https://api.openai.com/v1/responses", "OPENAI_API_KEY", "gpt-6-luna"),
    "xai": ("https://api.x.ai/v1/responses", "XAI_API_KEY", "grok-4.3"),
}


def ai(system, user):
    """One AI call. Returns text, or None if not configured / failed (the bots then retry or fall back)."""
    provider = env("AI_PROVIDER", "openai").lower()
    url, key_name, default_model = PROVIDERS.get(provider, PROVIDERS["openai"])
    key = env(key_name)
    if not key:
        log.warning("%s not set: skipping AI step", key_name)
        return None
    body = {
        "model": env("AI_MODEL") or (env("GROK_MODEL") if provider == "xai" else None) or default_model,
        "input": [{"role": "system", "content": system}, {"role": "user", "content": user}],
    }
    try:
        r = requests.post(url, json=body, headers={"Authorization": f"Bearer {key}"}, timeout=120)
        r.raise_for_status()
        data = r.json()
    except Exception as e:  # never let the AI step break the bot
        log.error("AI call failed (%s): %s", provider, e)
        return None
    if isinstance(data.get("output_text"), str):
        return data["output_text"].strip()
    for item in data.get("output", []):
        if item.get("type") == "message":
            for c in item.get("content", []):
                if c.get("type") == "output_text":
                    return c.get("text", "").strip()
    log.error("AI: no text in response")
    return None


grok = ai  # old name, kept so older code keeps working


def ai_json(system, user, required=(), tries=2):
    """AI call that must return a JSON object containing the `required` keys. Retries once, then None."""
    for attempt in range(tries):
        txt = ai(system if attempt == 0 else system + " Your previous answer was not valid JSON. "
                 "Reply with ONE complete JSON object and nothing else.", user)
        m = re.search(r"\{.*\}", txt or "", re.S)
        if m:
            try:
                obj = json.loads(m.group(0))
                if isinstance(obj, dict) and all(k in obj for k in required):
                    return obj
            except json.JSONDecodeError:
                pass
        log.warning("AI JSON invalid (attempt %d)", attempt + 1)
    return None


grok_json = ai_json


# --------------------------------------------------------------- Telegram ---
def _email_alert(text):
    """Alerts by email (to ALERT_EMAIL, sent from the support mailbox) when Telegram is not set up."""
    import mailer  # local import: mailer imports common
    plain = re.sub(r'<a href="([^"]+)">([^<]*)</a>', r"\2 (\1)", text)
    plain = html.unescape(re.sub(r"<[^>]+>", "", plain))
    first = plain.strip().split("\n", 1)[0][:90]
    try:
        mailer.send(mailer.build(env("ALERT_EMAIL"), f"[Kalijy bot] {first}", plain))
    except Exception as e:
        log.error("alert email failed: %s", e)


def notify(text):
    """Send an HTML message to your Telegram chat (split if too long)."""
    if DRY_RUN or (not env("TELEGRAM_BOT_TOKEN") and not env("ALERT_EMAIL")):
        print("---- ALERT (not sent) ----\n" + text + "\n-----------------------------")
        return
    if not env("TELEGRAM_BOT_TOKEN"):
        return _email_alert(text)
    token, chat = env("TELEGRAM_BOT_TOKEN"), env("TELEGRAM_CHAT_ID", required=True)
    chunks, cur = [], ""
    for line in text.split("\n"):
        if len(cur) + len(line) + 1 > 3800:
            chunks.append(cur)
            cur = ""
        cur += line + "\n"
    if cur.strip():
        chunks.append(cur)
    for c in chunks:
        r = requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                          json={"chat_id": chat, "text": c, "parse_mode": "HTML",
                                "disable_web_page_preview": True}, timeout=30)
        if r.status_code != 200:
            log.error("Telegram error %s: %s", r.status_code, r.text[:300])

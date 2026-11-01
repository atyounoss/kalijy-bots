"""Order watcher: new orders to submit in MutualDropshipping + problems that need you.

It never pays, submits or edits anything. MutualDropshipping orders are submitted and paid by you.
Each alert is sent once (remembered in state.py).
"""
import datetime as dt

import state
from common import (env_float, esc, fetch_orders, log, md_checklist, money, notify, order_admin_link,
                    order_check, parse_ts, shipped_vs_ordered, utcnow)

UNFULFILLED_H = env_float("UNFULFILLED_ALERT_HOURS", 24)
NO_TRACKING_H = env_float("NO_TRACKING_ALERT_HOURS", 6)
NOT_DELIVERED_D = env_float("NOT_DELIVERED_ALERT_DAYS", 21)
OPEN_STATUSES = {"UNFULFILLED", "PARTIALLY_FULFILLED", "ON_HOLD", "SCHEDULED", "IN_PROGRESS", "OPEN",
                 "PENDING_FULFILLMENT"}
PAID_STATUSES = {"PAID", "PARTIALLY_REFUNDED"}
DONE_SHIPMENT = {"DELIVERED", "CANCELED", "FAILURE"}


def recent_orders(days=45):
    since = (utcnow() - dt.timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return [o for o in fetch_orders(f"created_at:>='{since}'") if not o.get("cancelledAt")]


def describe(o):
    items = ", ".join(f'{li["quantity"]}× {li["title"]}' + (f' ({li["variantTitle"]})' if li.get("variantTitle") else "")
                      for li in o["lineItems"]["nodes"])
    p = o["totalPriceSet"]["presentmentMoney"]
    country = (o.get("shippingAddress") or {}).get("countryCodeV2", "?")
    return f'{order_admin_link(o)} · {country} · {money(p["amount"], p["currencyCode"])}\n   {esc(items)}'


def problems(o, now):
    """(key, text) for every problem on this order right now."""
    out = []
    age = now - parse_ts(o["createdAt"])
    if o["displayFulfillmentStatus"] in OPEN_STATUSES and o["displayFinancialStatus"] in PAID_STATUSES:
        crossed = [h for h in (UNFULFILLED_H, UNFULFILLED_H * 3) if age >= dt.timedelta(hours=h)]
        if crossed:
            h = max(crossed)
            out.append((f"unf{int(h)}", f"⏰ Not fulfilled after {int(h)}h"))
    for i, f in enumerate(o.get("fulfillments") or []):
        f_age = now - parse_ts(f["createdAt"])
        if not (f.get("trackingInfo") or []) and f_age >= dt.timedelta(hours=NO_TRACKING_H):
            out.append((f"notrack{i}", "📭 Fulfilled but no tracking number"))
        if f.get("displayStatus") not in DONE_SHIPMENT and f_age >= dt.timedelta(days=NOT_DELIVERED_D):
            out.append((f"late{i}", f"🚚 Shipped {int(NOT_DELIVERED_D)}+ days ago, not delivered"))
    for i, text in enumerate(order_check(o)):
        if not text.startswith("ℹ️"):
            out.append((f"chk{i}", text))
    fs = [f for f in o.get("fulfillments") or [] if f.get("status") not in ("CANCELLED", "ERROR", "FAILURE")]
    if fs and now - max(parse_ts(f["createdAt"]) for f in fs) >= dt.timedelta(hours=NO_TRACKING_H):
        diffs = shipped_vs_ordered(o)
        if diffs:
            out.append(("mismatch", "📦 Supplier shipment does not match the order: " + "; ".join(diffs)))
    return out


def open_issues(orders, now=None):
    """Everything currently wrong (used by the daily report)."""
    now = now or utcnow()
    return [(text, o) for o in orders for _key, text in problems(o, now)]


def run():
    now = utcnow()
    orders = recent_orders()
    log.info("order_watch: %d recent orders", len(orders))
    new, alerts = [], []
    for o in orders:
        if now - parse_ts(o["createdAt"]) < dt.timedelta(days=3) and not state.seen(f"new:{o['id']}"):
            flags = ["<b>Enter in MD exactly:</b> " + esc(" + ".join(md_checklist(o)) or "nothing")]
            flags += [esc(t) for t in order_check(o)]
            rec = (o.get("risk") or {}).get("recommendation")
            if rec in ("CANCEL", "INVESTIGATE"):
                flags.append(f"⚠️ Shopify fraud check: {rec}, review before paying the supplier")
            new.append(describe(o) + "".join("\n   " + f for f in flags))
            state.mark(f"new:{o['id']}")
        for key, text in problems(o, now):
            k = f"{key}:{o['id']}"
            if not key.startswith("chk") and not state.seen(k):
                alerts.append(f"{text}: " + describe(o))
                state.mark(k)
    if not new and not alerts:
        log.info("order_watch: nothing to report")
        return
    msg = []
    if new:
        msg.append(f"<b>🛒 {len(new)} new order(s)</b>\nSubmit + pay them in MutualDropshipping "
                   "(Store Order → Submit Orders → Pay). Before paying, check the quantities and the kit "
                   "size in MD match the line below: 1 free gift kit per order, never 1 per charger.\n")
        msg.extend("• " + n for n in new)
    if alerts:
        msg.append("\n<b>Needs attention</b>")
        msg.extend("• " + a for a in alerts)
    notify("\n".join(msg))


if __name__ == "__main__":
    run()

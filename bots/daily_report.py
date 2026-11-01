"""Morning report: yesterday's sales, bundle mix, countries, open problems, Meta results + a short Grok analysis."""
import collections
import datetime as dt
import json

import ads_monitor
import order_watch
from common import (REPORT_LANGUAGE, TZ, charger_qty, esc, fetch_orders, grok, log, money, notify,
                    order_admin_link, utcnow)


def local_day_bounds(day):
    start = dt.datetime.combine(day, dt.time.min, TZ)
    end = start + dt.timedelta(days=1)
    f = "%Y-%m-%dT%H:%M:%SZ"
    return start.astimezone(dt.timezone.utc).strftime(f), end.astimezone(dt.timezone.utc).strftime(f)


def summarize(orders):
    live = [o for o in orders if not o.get("cancelledAt")]
    by_cur, by_country, mix = collections.Counter(), collections.Counter(), collections.Counter()
    shop_total, refunded, units = 0.0, 0.0, 0
    shop_cur = ""
    for o in live:
        p = o["totalPriceSet"]["presentmentMoney"]
        by_cur[p["currencyCode"]] += float(p["amount"])
        s = o["totalPriceSet"]["shopMoney"]
        shop_total += float(s["amount"])
        shop_cur = s["currencyCode"]
        refunded += float(o["totalRefundedSet"]["shopMoney"]["amount"])
        by_country[(o.get("shippingAddress") or {}).get("countryCodeV2", "?")] += 1
        q = charger_qty(o)
        units += q
        mix[f"{q}x" if q < 3 else "3x+"] += 1
    return {
        "orders": len(live),
        "cancelled": len(orders) - len(live),
        "chargers_sold": units,
        "revenue_shop_currency": round(shop_total, 2),
        "shop_currency": shop_cur,
        "revenue_by_currency": {k: round(v, 2) for k, v in by_cur.items()},
        "refunded_shop_currency": round(refunded, 2),
        "orders_by_country": dict(by_country),
        "bundle_mix": dict(mix),
        "aov_shop_currency": round(shop_total / len(live), 2) if live else 0,
    }


def run():
    today = dt.datetime.now(TZ).date()
    yday = today - dt.timedelta(days=1)
    s, e = local_day_bounds(yday)
    s7, _ = local_day_bounds(yday - dt.timedelta(days=6))
    y = summarize(fetch_orders(f"created_at:>='{s}' created_at:<'{e}'"))
    week = summarize(fetch_orders(f"created_at:>='{s7}' created_at:<'{e}'"))
    issues = order_watch.open_issues(order_watch.recent_orders(), utcnow())

    meta = None
    if ads_monitor.configured():
        try:
            meta = ads_monitor.day_summary(yday.isoformat())
        except Exception as ex:
            log.error("Meta summary failed: %s", ex)

    lines = [f"<b>☀️ Kalijy report · {yday.strftime('%a %d %b %Y')}</b>",
             f"Orders: <b>{y['orders']}</b> · chargers: {y['chargers_sold']} · cancelled: {y['cancelled']}",
             f"Revenue: <b>{money(y['revenue_shop_currency'], y['shop_currency'])}</b> "
             f"(" + ", ".join(money(v, k) for k, v in y["revenue_by_currency"].items()) + ")"
             if y["orders"] else "Revenue: 0",
             f"AOV: {money(y['aov_shop_currency'], y['shop_currency'])} · bundles: {y['bundle_mix'] or '-'}",
             f"Countries: {y['orders_by_country'] or '-'}",
             f"Refunded: {money(y['refunded_shop_currency'], y['shop_currency'])}",
             f"Last 7 days: {week['orders']} orders · {money(week['revenue_shop_currency'], week['shop_currency'])}"]
    if meta:
        t = meta["totals"]
        cur = t.get("account_currency", "")
        cpa = f"{t['cpa']:.2f} {cur}" if t.get("cpa") else "-"
        lines.append(f"\n<b>Meta</b>: spend {t['spend']:.2f} {cur} · purchases {t['purchases']:.0f} · "
                     f"ROAS {t['roas']:.2f} · CPA {cpa} · CTR {t['ctr']:.2f}%")
        for a in meta["top_ads"]:
            lines.append(f"  · {esc(a['ad_name'])}: {a['spend']:.2f} {cur}, {a['purchases']:.0f} purch., "
                         f"ROAS {a['roas']:.2f}")
    if issues:
        lines.append(f"\n<b>⚠️ Open problems ({len(issues)})</b>")
        lines.extend(f"• {esc(why)}: {order_admin_link(o)}" for why, o in issues[:15])
    else:
        lines.append("\n✅ No open order problems")

    facts = {"yesterday": y, "last_7_days": week,
             "open_problems": [why for why, _ in issues],
             "meta": meta and {"totals": {k: meta["totals"].get(k) for k in
                                          ("spend", "purchases", "value", "roas", "cpa", "ctr", "impressions")},
                               "top_ads": [{k: a.get(k) for k in ("ad_name", "spend", "purchases", "roas", "ctr")}
                                           for a in meta["top_ads"]],
                               "target_roas": meta["target_roas"]}}
    analysis = grok(
        "You are the data analyst of Kalijy, a small Shopify store selling one foldable 3-in-1 wireless "
        "charger in the Netherlands, Sweden and Denmark (fulfilled by a dropshipping supplier, ads on Meta "
        "and Google). Write in " + REPORT_LANGUAGE + ". Use ONLY the numbers in the JSON. Never invent data, "
        "benchmarks or causes; if data is missing or too small to judge, say so. Max 6 short bullets: what "
        "changed, what is wrong, and at most 3 concrete actions for today. Plain text, no markdown headers.",
        json.dumps(facts, default=str),
    )
    if analysis:
        lines.append("\n<b>🤖 Analysis</b>\n" + esc(analysis))
    notify("\n".join(lines))


if __name__ == "__main__":
    run()

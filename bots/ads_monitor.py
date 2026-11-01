"""Meta ads watcher: alerts on ads burning money, weak CTR and daily overspend.

Rules are switched off (0) until you set your own numbers in the GitHub secrets/variables.
It only pauses ads by itself if META_AUTO_PAUSE=true and the token has ads_management.
It never raises budgets: scaling is your decision.
"""
import datetime as dt

import requests

import state
from common import DRY_RUN, TZ, env, env_bool, env_float, esc, log, notify

API = f"https://graph.facebook.com/{env('META_API_VERSION', 'v26.0')}"
PURCHASE_TYPES = ("omni_purchase", "purchase", "offsite_conversion.fb_pixel_purchase")

KILL_SPEND = env_float("META_KILL_SPEND", 0)          # spend over 3 days with 0 purchases → pause/alert
MIN_CTR = env_float("META_MIN_CTR", 0)                # % link CTR under which an ad is flagged
MIN_IMPR = env_float("META_MIN_IMPRESSIONS", 2000)    # impressions before CTR is judged
DAILY_CAP = env_float("META_DAILY_SPEND_CAP", 0)      # alert when today's spend passes this
TARGET_ROAS = env_float("META_TARGET_ROAS", 0)        # used in the daily report to name winners
AUTO_PAUSE = env_bool("META_AUTO_PAUSE", False)


def configured():
    return bool(env("META_ACCESS_TOKEN") and env("META_AD_ACCOUNT_ID"))


def _account():
    a = env("META_AD_ACCOUNT_ID", required=True)
    return a if a.startswith("act_") else f"act_{a}"


def _get(path, params):
    params = dict(params, access_token=env("META_ACCESS_TOKEN", required=True))
    out, url = [], f"{API}/{path}"
    while url:
        r = requests.get(url, params=params, timeout=60)
        r.raise_for_status()
        data = r.json()
        out.extend(data.get("data", []))
        url, params = data.get("paging", {}).get("next"), {}
    return out


def _first(actions, types):
    by = {a["action_type"]: float(a["value"]) for a in actions or []}
    for t in types:
        if t in by:
            return by[t]
    return 0.0


def insights(level, **window):
    fields = ("ad_id,ad_name,adset_name,campaign_name,spend,impressions,clicks,inline_link_click_ctr,"
              "cpc,actions,action_values,account_currency")
    params = {"level": level, "fields": fields, "limit": 200}
    params.update(window)
    rows = []
    for r in _get(f"{_account()}/insights", params):
        spend = float(r.get("spend", 0))
        purchases = _first(r.get("actions"), PURCHASE_TYPES)
        value = _first(r.get("action_values"), PURCHASE_TYPES)
        rows.append({
            **r,
            "spend": spend,
            "impressions": int(r.get("impressions", 0)),
            "ctr": float(r.get("inline_link_click_ctr") or 0),
            "purchases": purchases,
            "value": value,
            "roas": value / spend if spend else 0,
            "cpa": spend / purchases if purchases else None,
        })
    return rows


def active_ad_ids():
    ads = _get(f"{_account()}/ads", {"fields": "id,effective_status", "limit": 500,
                                      "effective_status": '["ACTIVE"]'})
    return {a["id"] for a in ads}


def pause(ad_id):
    if DRY_RUN:
        log.info("DRY_RUN: would pause ad %s", ad_id)
        return False
    r = requests.post(f"{API}/{ad_id}", data={"status": "PAUSED",
                                              "access_token": env("META_ACCESS_TOKEN")}, timeout=30)
    return r.status_code == 200


def day_summary(day):
    """Totals + top ads for one local day (used by the daily report)."""
    tr = {"time_range": f'{{"since":"{day}","until":"{day}"}}'}
    acc = insights("account", **tr)
    ads = sorted(insights("ad", **tr), key=lambda r: (-r["purchases"], -r["spend"]))[:5]
    tot = acc[0] if acc else {"spend": 0, "purchases": 0, "value": 0, "roas": 0, "cpa": None,
                              "impressions": 0, "ctr": 0, "account_currency": ""}
    return {"totals": tot, "top_ads": ads, "target_roas": TARGET_ROAS}


def run():
    if not configured():
        log.info("ads_monitor: Meta not configured, skipping")
        return
    active = active_ad_ids()
    rows = [r for r in insights("ad", date_preset="last_3d") if r["ad_id"] in active]
    cur = rows[0]["account_currency"] if rows else ""
    today = dt.datetime.now(TZ).date().isoformat()
    lines = []
    for r in rows:
        name = esc(f'{r["campaign_name"]} › {r["ad_name"]}')
        key = f"ad:{r['ad_id']}:{today}"
        if state.seen(key):
            continue  # one alert per ad per day
        if KILL_SPEND and r["purchases"] == 0 and r["spend"] >= KILL_SPEND:
            state.mark(key)
            done = pause(r["ad_id"]) if AUTO_PAUSE else False
            lines.append(f"🔴 {name}: {r['spend']:.2f} {cur} spent in 3 days, 0 purchases → "
                         + ("<b>PAUSED automatically</b>" if done else "pause it?"))
        elif MIN_CTR and r["impressions"] >= MIN_IMPR and r["ctr"] < MIN_CTR:
            state.mark(key)
            lines.append(f"🟠 {name}: link CTR {r['ctr']:.2f}% after {r['impressions']} impressions "
                         f"(your floor {MIN_CTR}%) → test a new hook/creative")
    if DAILY_CAP and not state.seen(f"cap:{today}"):
        acc = insights("account", time_range=f'{{"since":"{today}","until":"{today}"}}')
        spent = acc[0]["spend"] if acc else 0
        if spent > DAILY_CAP:
            state.mark(f"cap:{today}")
            lines.append(f"💸 Today's spend {spent:.2f} {cur} is above your cap of {DAILY_CAP:.2f}")
    if lines:
        notify("<b>📣 Meta ads check</b>\n" + "\n".join(lines))
    else:
        log.info("ads_monitor: no rule triggered (%d active ads)", len(rows))


if __name__ == "__main__":
    run()

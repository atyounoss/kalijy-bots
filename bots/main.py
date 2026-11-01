"""Single entry point, started every 30 minutes by GitHub Actions.

  every run .................. support inbox + order watcher + customer follow-up emails
  every 6 hours .............. Meta ads check
  once a day after REPORT_HOUR  morning report (local time, BOT_TIMEZONE)

Run one bot by hand:  python bots/main.py daily_report   (or order_watch / customer_followup / ads_monitor / support_inbox / all)
"""
import datetime as dt
import sys
import time
import traceback

import state
from common import TZ, env, esc, log, notify

BOTS = ["support_inbox", "order_watch", "customer_followup", "ads_monitor", "daily_report"]


def due(now_local):
    jobs = ["support_inbox", "order_watch", "customer_followup"]
    last_ads = state.last_run("ads_monitor") or 0
    if time.time() - last_ads >= 6 * 3600 - 600:
        jobs.append("ads_monitor")
    today = now_local.date().isoformat()
    if now_local.hour >= int(env("REPORT_HOUR", "8")) and state.last_run("daily_report") != today:
        jobs.append("daily_report")
    return jobs


def main():
    arg = sys.argv[1] if len(sys.argv) > 1 else ""
    now_local = dt.datetime.now(TZ)
    jobs = BOTS if arg == "all" else [arg] if arg in BOTS else due(now_local)
    failed = []
    for name in jobs:
        log.info("running %s", name)
        try:
            __import__(name).run()
            if name == "ads_monitor":
                state.set_last_run(name, time.time())
            elif name == "daily_report":
                state.set_last_run(name, now_local.date().isoformat())
        except Exception as e:
            traceback.print_exc()
            failed.append(f"{name}: {e}")
        finally:
            state.save()
    if failed:
        notify("<b>⚠️ Kalijy bot error</b>\n" + esc("\n".join(failed)[:1500]))
        sys.exit(1)


if __name__ == "__main__":
    main()

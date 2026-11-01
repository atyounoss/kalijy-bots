"""Customer-service bot for the support mailbox (IMAP/SMTP, e.g. Gmail with an app password).

For each new customer email: Grok detects language + topic, Shopify order data is looked up,
and Grok writes a reply in the customer's language using only kb.md + order facts.
- "Where is my order" emails with a matching order are answered automatically (configurable).
- Everything else (returns, damage, cancellations, complaints, unknown questions) is saved as a
  DRAFT in your mailbox and sent to you on Telegram to check and send yourself.
"""
import datetime as dt
import email
import email.utils
import imaplib
import json
import os
import re
import smtplib
import time
from email.header import decode_header, make_header
from email.message import EmailMessage

import state
from common import DRY_RUN, env, env_bool, esc, fetch_orders, grok, grok_json, log, notify, tracking_numbers  # grok = the configured AI

IMAP_HOST = env("IMAP_HOST", "mail.spacemail.com")
SMTP_HOST = env("SMTP_HOST", "mail.spacemail.com")
SMTP_PORT = int(env("SMTP_PORT", "465"))
LABEL = env("SUPPORT_BOT_LABEL", "kalijy-bot")
AUTO_SEND = {c.strip() for c in env("AUTO_SEND_CATEGORIES", "order_status").split(",") if c.strip()}
LOOKBACK_DAYS = int(env("SUPPORT_LOOKBACK_DAYS", "3"))
MAX_PER_RUN = int(env("SUPPORT_MAX_PER_RUN", "15"))
IGNORE = [s.strip().lower() for s in env(
    "SUPPORT_IGNORE_SENDERS",
    "shopify.com,mutualdropshipping,facebookmail.com,google.com,noreply,no-reply,mailer-daemon,"
    "notifications,paypal,stripe,x.ai,github.com,telegram").split(",") if s.strip()]
# Words that mean the AI promised an action or outcome: such replies are never auto-sent (draft instead).
PROMISE_RE = re.compile(
    r"investigat|onderzoek|undersök|undersøg|refund|terugbetal|återbetal|refusion|tilbagebetal|"
    r"replace|vervang|ersätt|erstat|reship|opnieuw verzend|skicka.{0,10}igen|sende.{0,10}igen|"
    r"we have contacted|we('ve| have) (been in touch|reached out)|contacted the carrier|"
    r"contact (opgenomen|gehad)|hebben .{0,25}gecontacteerd|vi har kontaktat|vi har kontaktet|"
    r"guarantee|garandeer|garanter|"
    r"will (arrive|be delivered) (on|by|tomorrow)|komt morgen|levereras i morgon|leveres i morgen",
    re.I)
URGENT = {"cancel_or_change", "damaged_wrong_item", "complaint_legal", "return_refund"}
KB = open(os.path.join(os.path.dirname(__file__), "kb.md"), encoding="utf-8").read()
FOOTER = {
    "nl": "Dit is een automatisch antwoord van Kalijy. Antwoord op deze e-mail om een medewerker te spreken.",
    "sv": "Detta är ett automatiskt svar från Kalijy. Svara på det här mejlet för att nå en person.",
    "da": "Dette er et automatisk svar fra Kalijy. Svar på denne e-mail for at få fat i en medarbejder.",
    "fr": "Ceci est une réponse automatique de Kalijy. Répondez à cet e-mail pour joindre une personne.",
    "de": "Dies ist eine automatische Antwort von Kalijy. Antworten Sie auf diese E-Mail, um eine Person zu erreichen.",
}
FOOTER_EN = "This is an automated reply from Kalijy. Reply to this email to reach a person."


def dec(s):
    try:
        return str(make_header(decode_header(s or "")))
    except Exception:
        return s or ""


def body_text(msg):
    part = None
    if msg.is_multipart():
        for p in msg.walk():
            if p.get_content_type() == "text/plain" and "attachment" not in str(p.get("Content-Disposition")):
                part = p
                break
        if part is None:
            for p in msg.walk():
                if p.get_content_type() == "text/html":
                    part = p
                    break
    else:
        part = msg
    if part is None:
        return ""
    raw = part.get_payload(decode=True) or b""
    txt = raw.decode(part.get_content_charset() or "utf-8", errors="replace")
    if part.get_content_type() == "text/html":
        txt = re.sub(r"<(script|style).*?</\1>", " ", txt, flags=re.S | re.I)
        txt = re.sub(r"<br\s*/?>|</p>", "\n", txt, flags=re.I)
        txt = re.sub(r"<[^>]+>", " ", txt)
    # cut quoted history
    cut = re.split(r"\n\s*(On .+wrote:|Op .+schreef.+:|Den .+skrev.+:|.+skrev .+:|Le .+a écrit|-----Original|From: )",
                   txt, maxsplit=1)[0]
    lines = [l for l in cut.splitlines() if not l.strip().startswith(">")]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()[:4000]


def order_facts(sender, order_number):
    orders = fetch_orders(f"email:{sender}", max_pages=1)[:5]
    if order_number:
        num = "#" + re.sub(r"\D", "", order_number)
        match = [o for o in orders if o["name"] == num]
        if not match and fetch_orders(f"name:{num}", max_pages=1):
            return None, "order_number_belongs_to_other_email"
        orders = match or orders
    facts = []
    for o in orders[:3]:
        facts.append({
            "order": o["name"], "date": o["createdAt"][:10], "payment": o["displayFinancialStatus"],
            "fulfillment": o["displayFulfillmentStatus"], "cancelled": bool(o.get("cancelledAt")),
            "items": [f'{li["quantity"]}x {li["title"]} {li.get("variantTitle") or ""}'.strip()
                      for li in o["lineItems"]["nodes"]],
            "tracking": [{"number": t["number"], "url": t.get("url"), "carrier": t.get("company")}
                         for t in tracking_numbers(o)],
            "shipment_status": [f.get("displayStatus") for f in o.get("fulfillments") or []],
        })
    return facts, None


def classify(subject, text):
    return grok_json(
        "Classify a customer email for an online store. Reply with JSON only: "
        '{"language":"nl|sv|da|en|fr|de|other","category":"order_status|product_question|return_refund|'
        'damaged_wrong_item|cancel_or_change|complaint_legal|spam_or_not_customer|other",'
        '"order_number":"digits like 1001 or null","summary":"one short English sentence"}',
        f"Subject: {subject}\n\n{text}", required=("language", "category", "summary"))


def write_reply(lang, category, subject, text, facts, note):
    return grok(
        "You are Kalijy's customer support. Write the reply email body only (no subject), in the "
        f"customer's language ({lang}), short, warm and clear, signed 'Team Kalijy'. "
        "Use ONLY facts from the knowledge base and the order data below. Never promise refunds, "
        "replacements, discounts, cancellations or delivery dates; for those say a colleague will "
        "handle it personally. Never invent tracking numbers or product specs. If the order data is "
        "empty, ask for the order number and the email used at checkout. If a device is not in the "
        "compatibility list, say a colleague will confirm. You cannot take any action yourself: never "
        "say that you or the team have contacted the carrier, opened an investigation, checked the "
        "warehouse or arranged anything. Never offer something the customer has already refused. "
        "Always end with one useful next step for the customer (for example: the tracking link to "
        "check, or what to reply with) so the email is never a dead end.\n\nKNOWLEDGE BASE:\n" + KB,
        json.dumps({"category": category, "note": note, "order_data": facts,
                    "customer_subject": subject, "customer_message": text}, ensure_ascii=False))


def build_reply(orig, to_addr, body, lang):
    m = EmailMessage()
    sender = env("SUPPORT_REPLY_FROM") or env("SUPPORT_ONLY_TO") or env("IMAP_USER")
    m["From"] = email.utils.formataddr((env("SUPPORT_FROM_NAME", "Kalijy"), sender))
    m["To"] = to_addr
    subj = dec(orig.get("Subject"))
    m["Subject"] = subj if subj.lower().startswith(("re:", "sv:", "aw:")) else f"Re: {subj}"
    if orig.get("Message-ID"):
        m["In-Reply-To"] = orig["Message-ID"]
        m["References"] = (orig.get("References", "") + " " + orig["Message-ID"]).strip()
    m["Date"] = email.utils.formatdate(localtime=True)
    m.set_content(body.strip() + "\n\n—\n" + FOOTER.get(lang, FOOTER_EN))
    return m


LIST_RE = re.compile(r'\((?P<flags>[^)]*)\)\s+(?P<delim>"[^"]*"|NIL)\s+(?P<name>.+)$')


def special_folder(M, flag, fallbacks):
    """Find the Drafts/Sent folder by its IMAP special-use flag, else by usual names."""
    typ, boxes = M.list()
    names = []
    for b in boxes or []:
        m = LIST_RE.match(b.decode(errors="replace"))
        if not m:
            continue
        name = m.group("name").strip()
        quoted = name if name.startswith('"') else f'"{name}"'
        if flag in m.group("flags"):
            return quoted
        names.append((name.strip('"'), quoted))
    for want in fallbacks:
        for plain, quoted in names:
            if plain.lower() == want.lower():
                return quoted
    return f'"{fallbacks[0]}"'


def run():
    if not (env("IMAP_USER") and env("IMAP_PASSWORD")):
        log.info("support_inbox: mailbox not configured, skipping")
        return
    user = env("IMAP_USER")
    if user.lower().endswith(("@gmail.com", "@googlemail.com")) and not env("SUPPORT_ONLY_TO"):
        log.warning("support_inbox: personal Gmail without SUPPORT_ONLY_TO: refusing to touch your private mail")
        return
    M = imaplib.IMAP4_SSL(IMAP_HOST)
    M.login(user, env("IMAP_PASSWORD"))
    gmail = "X-GM-EXT-1" in M.capabilities
    M.select("INBOX")
    only_to = env("SUPPORT_ONLY_TO")  # e.g. support@kalijy.com: ignore everything not sent to it
    if gmail:
        q = f"in:inbox -label:{LABEL} newer_than:{LOOKBACK_DAYS}d" + (f" to:{only_to}" if only_to else "")
        typ, data = M.uid("SEARCH", "X-GM-RAW", f'"{q}"')
    else:
        since = (dt.date.today() - dt.timedelta(days=LOOKBACK_DAYS)).strftime("%d-%b-%Y")
        crit = f"(SINCE {since}" + (f' TO "{only_to}"' if only_to else "") + ")"
        typ, data = M.uid("SEARCH", None, crit)
    uids = (data[0] or b"").split()
    log.info("support_inbox: %d emails in window", len(uids))
    report, handled = [], 0
    for uid in uids:
        if handled >= MAX_PER_RUN:
            break
        typ, raw = M.uid("FETCH", uid, "(BODY.PEEK[])")
        msg = email.message_from_bytes(raw[0][1])
        mid = (msg.get("Message-ID") or f"uid-{uid.decode()}").strip()
        if state.seen(f"mail:{mid}"):
            continue  # already handled in an earlier run
        handled += 1
        name, addr = email.utils.parseaddr(msg.get("Reply-To") or msg.get("From"))
        addr = addr.lower()
        subject = dec(msg.get("Subject"))
        done = True
        if not addr or addr == user.lower() or any(s in addr for s in IGNORE):
            pass
        else:
            text = body_text(msg)
            c = classify(subject, text) or {}
            cat = c.get("category", "other")
            lang = c.get("language") or "the same language as the customer's message"
            if not c:
                report.append(f"⚠️ AI could not classify the email from {esc(addr)}: handled as 'other' (draft only)")
            if cat == "spam_or_not_customer":
                report.append(f"🗑 ignored ({esc(addr)}): {esc(c.get('summary', subject))}")
            else:
                facts, note = order_facts(addr, c.get("order_number"))
                reply = write_reply(lang, cat, subject, text, facts, note)
                if not reply:
                    fails = sum(1 for i in range(3) if state.seen(f"mailfail{i}:{mid}"))
                    if fails >= 2:  # 3rd failure: stop retrying, hand it to you
                        report.append(f"🚨 AI failed 3 times on the email from {esc(addr)} "
                                      f"(\"{esc(subject)}\"): answer it yourself")
                    else:
                        state.mark(f"mailfail{fails}:{mid}")
                        done = False  # AI unavailable: retry next run
                else:
                    out = build_reply(msg, addr, reply, lang)
                    risky = PROMISE_RE.search(reply)
                    auto = (cat in AUTO_SEND and facts and not note and not DRY_RUN and not risky
                            and env_bool("SUPPORT_AUTO_SEND_ENABLED", True))
                    if risky and cat in AUTO_SEND:
                        report.append(f"⚠️ reply held as draft, it promised something (\"{esc(risky.group(0))}\")")
                    if auto:
                        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT) as s:
                            s.login(user, env("IMAP_PASSWORD"))
                            s.send_message(out)
                        if not gmail:  # Gmail files sent mail itself; other providers need a copy in Sent
                            M.append(special_folder(M, "\\Sent", ["Sent", "Sent Items", "Sent Messages", "INBOX.Sent"]),
                                     "\\Seen", imaplib.Time2Internaldate(time.time()), out.as_bytes())
                        report.append(f"✅ auto-answered {esc(addr)} [{cat}/{lang}]: {esc(c.get('summary', ''))}")
                    else:
                        if not DRY_RUN:
                            M.append(special_folder(M, "\\Drafts", ["Drafts", "Draft", "INBOX.Drafts", "[Gmail]/Drafts"]),
                                     "\\Draft", imaplib.Time2Internaldate(time.time()), out.as_bytes())
                        flag = "🚨" if cat in URGENT else "📝"
                        report.append(f"{flag} draft ready for {esc(addr)} [{cat}/{lang}]: "
                                      f"{esc(c.get('summary', subject))}")
        if done and not DRY_RUN:
            state.mark(f"mail:{mid}")
            if gmail:
                M.uid("STORE", uid, "+X-GM-LABELS", LABEL)
    M.logout()
    if report:
        notify("<b>✉️ Support inbox</b>\n" + "\n".join(report) +
               "\n\nDrafts are in your mailbox's Drafts folder: check, edit, send.")


if __name__ == "__main__":
    run()

"""Customer emails, in the customer's language (nl / sv / da / en), from the support mailbox.

  thanks     right after payment (next run, max ~30 min): thank you + what was ordered + the free gift kit
             (with its band size) + "your tracking number follows within 24 hours"
  tracking   as soon as MutualDropshipping uploads the tracking number to Shopify: number, carrier, link and
             "your order is being processed" (no shipping status, no delivery days)
  update     every FOLLOWUP_UPDATE_DAYS (3) days once the parcel is on its way (carrier "in transit", or 2 days
             after the tracking upload if Shopify gets no carrier status) until delivered (max 30 days):
             current status + link. Past the promised time it apologises, and you get a Telegram alert.
  delivered  2 days after Shopify marks the parcel delivered: "did it arrive well?" + first-use tips

Texts are fixed templates (no AI), so they never invent facts. Each email is sent once (remembered in
state.py) and only inside a time window, so a lost memory file can't cause a flood of old emails.
Updates and "delivered" go out between FOLLOWUP_SEND_FROM and FOLLOWUP_SEND_TO (customer time);
"thanks" and "tracking" go out at any hour.
"""
import datetime as dt
from zoneinfo import ZoneInfo

import mailer
import state
from common import (charger_lines, env, env_bool, esc, fetch_orders, free_units, kit_lines, log, notify,
                    order_admin_link, parse_ts, qty, utcnow)

UPDATE_DAYS = int(env("FOLLOWUP_UPDATE_DAYS", "3"))
UPDATE_MAX_DAYS = int(env("FOLLOWUP_UPDATE_MAX_DAYS", "30"))
DELIVERED_DAYS = int(env("FOLLOWUP_DELIVERED_DAYS", "2"))
MAX_PER_RUN = int(env("FOLLOWUP_MAX_PER_RUN", "40"))
PROCESSING_DAYS, SHIP_LO, SHIP_HI = 2, 5, 7     # shipping policy: processing 0-2 days, shipping 5-7 business days
LATE_AFTER_BDAYS = SHIP_HI + int(env("FOLLOWUP_LATE_EXTRA_BDAYS", "0"))
CUSTOMER_TZ = ZoneInfo("Europe/Amsterdam")
DONE = {"DELIVERED", "CANCELED", "FAILURE", "PICKED_UP"}

COLOR = {"nl": {"black": "zwart", "white": "wit"}, "sv": {"black": "svart", "white": "vit"},
         "da": {"black": "sort", "white": "hvid"}, "en": {"black": "black", "white": "white"}}

T = {
 "nl": {
  "charger": "Kalijy 3-in-1 draadloze lader", "kit": "Gratis 3-delige accessoireset (horlogebandje {size})",
  "thanks": ("Bedankt voor je bestelling {order}!",
   "Hoi{name},\n\nBedankt voor je bestelling bij Kalijy! We hebben je betaling ontvangen en gaan meteen voor je aan de slag.\n\n"
   "Je bestelling {order}:\n{items}\n\nJe track-and-tracecode ontvang je binnen 24 uur per e-mail.\n\n"
   "Vragen? Antwoord gewoon op deze e-mail.\n\nGroetjes,\nKalijy"),
  "tracking": ("Je track-and-tracecode voor bestelling {order}",
   "Hoi{name},\n\nHier is de track-and-tracecode van je bestelling {order}:\n\nCode: {number}\nVervoerder: {carrier}\n"
   "Volg je pakket: {link}\n\nJe bestelling wordt nu verwerkt. Zodra je pakket ons magazijn verlaat, zie je de eerste updates in de tracking.\n\n"
   "Zodra je pakket onderweg is, sturen we je om de paar dagen een update.\n\nGroetjes,\nKalijy"),
  "update": ("Update over je bestelling {order}",
   "Hoi{name},\n\nEen korte update over je bestelling {order}: {status}\n\nVolg je pakket: {link}\n\n"
   "Vragen? Antwoord gewoon op deze e-mail.\n\nGroetjes,\nKalijy"),
  "update_late": ("Update over je bestelling {order}",
   "Hoi{name},\n\nJe bestelling {order} doet er iets langer over dan verwacht. Excuses daarvoor. "
   "We hebben contact met de vervoerder om ervoor te zorgen dat je bestelling zo snel mogelijk wordt bezorgd.\n\nVolg je pakket: {link}\n\n"
   "Is je pakket inmiddels aangekomen, of zie je iets vreemds in de tracking? Antwoord op deze e-mail, dan helpen we je meteen.\n\n"
   "Groetjes,\nKalijy"),
  "status": {"OUT_FOR_DELIVERY": "je pakket wordt vandaag bezorgd.",
             "ATTEMPTED_DELIVERY": "de vervoerder heeft geprobeerd je pakket te bezorgen. Bekijk de tracking voor de volgende stap.",
             "_": "je pakket is onderweg naar je toe."},
  "delivered": ("Is je Kalijy-lader goed aangekomen?",
   "Hoi{name},\n\nVolgens de track & trace is je bestelling {order} bezorgd. We hopen dat je blij bent met je Kalijy-lader!\n\n"
   "Tips voor de eerste keer:\n• Sluit de lader aan op een USB-C-stroombron (bijvoorbeeld een stekkeradapter).\n"
   "• Dikke, metalen of niet-magnetische hoesjes kunnen het opladen tegenhouden. Haal ook pasjes of metalen plaatjes weg.\n"
   "• Je oordopjescase moet draadloos opladen ondersteunen.\n\nIs er iets mis of ontbreekt er iets (ook in de gratis "
   "accessoireset)? Antwoord op deze e-mail, dan lossen we het op.\n\nGroetjes,\nKalijy"),
  "footer": "Dit bericht is automatisch verstuurd. Je kunt er gewoon op antwoorden.",
 },
 "sv": {
  "charger": "Kalijy 3-i-1 trådlös laddare", "kit": "Gratis tillbehörspaket i 3 delar (klockarmband {size})",
  "thanks": ("Tack för din beställning {order}!",
   "Hej{name},\n\nTack för din beställning hos Kalijy! Vi har tagit emot din betalning och börjar direkt med din order.\n\n"
   "Din beställning {order}:\n{items}\n\nDu får ditt spårningsnummer via mejl inom 24 timmar.\n\n"
   "Frågor? Svara bara på det här mejlet.\n\nVänliga hälsningar,\nKalijy"),
  "tracking": ("Ditt spårningsnummer för beställning {order}",
   "Hej{name},\n\nHär är spårningsnumret för din beställning {order}:\n\nNummer: {number}\nTransportör: {carrier}\n"
   "Följ ditt paket: {link}\n\nDin beställning håller på att behandlas. När paketet lämnar vårt lager syns de första uppdateringarna i spårningen.\n\n"
   "När paketet är på väg skickar vi en uppdatering med några dagars mellanrum.\n\n"
   "Vänliga hälsningar,\nKalijy"),
  "update": ("Uppdatering om din beställning {order}",
   "Hej{name},\n\nEn kort uppdatering om din beställning {order}: {status}\n\nFölj ditt paket: {link}\n\n"
   "Frågor? Svara bara på det här mejlet.\n\nVänliga hälsningar,\nKalijy"),
  "update_late": ("Uppdatering om din beställning {order}",
   "Hej{name},\n\nDin beställning {order} tar lite längre tid än väntat, och det beklagar vi. "
   "Vi har kontakt med transportören för att se till att din beställning levereras så snart som möjligt.\n\nFölj ditt paket: {link}\n\n"
   "Har paketet redan kommit fram, eller ser du något konstigt i spårningen? Svara på det här mejlet så hjälper vi dig direkt.\n\n"
   "Vänliga hälsningar,\nKalijy"),
  "status": {"OUT_FOR_DELIVERY": "ditt paket levereras i dag.",
             "ATTEMPTED_DELIVERY": "transportören har försökt leverera ditt paket. Se spårningen för nästa steg.",
             "_": "ditt paket är på väg till dig."},
  "delivered": ("Har din Kalijy-laddare kommit fram?",
   "Hej{name},\n\nEnligt spårningen har din beställning {order} levererats. Vi hoppas att du gillar din Kalijy-laddare!\n\n"
   "Tips inför första användningen:\n• Anslut laddaren till en USB-C-strömkälla (till exempel en väggadapter).\n"
   "• Tjocka skal, metallskal eller skal som inte är magnetiska kan hindra laddningen. Ta också bort kort eller metallplattor.\n"
   "• Ditt hörlursfodral måste ha stöd för trådlös laddning.\n\nÄr något fel eller saknas något (även i det gratis "
   "tillbehörspaketet)? Svara på det här mejlet så löser vi det.\n\nVänliga hälsningar,\nKalijy"),
  "footer": "Det här mejlet skickades automatiskt. Du kan svara på det som vanligt.",
 },
 "da": {
  "charger": "Kalijy 3-i-1 trådløs oplader", "kit": "Gratis tilbehørssæt i 3 dele (urrem {size})",
  "thanks": ("Tak for din ordre {order}!",
   "Hej{name},\n\nTak for din ordre hos Kalijy! Vi har modtaget din betaling og går i gang med det samme.\n\n"
   "Din ordre {order}:\n{items}\n\nDu modtager dit sporingsnummer på e-mail inden for 24 timer.\n\n"
   "Spørgsmål? Svar bare på denne e-mail.\n\nVenlig hilsen\nKalijy"),
  "tracking": ("Dit sporingsnummer til ordre {order}",
   "Hej{name},\n\nHer er sporingsnummeret til din ordre {order}:\n\nNummer: {number}\nTransportør: {carrier}\n"
   "Følg din pakke: {link}\n\nDin ordre er ved at blive behandlet. Når pakken forlader vores lager, kan du se de første opdateringer i sporingen.\n\n"
   "Når pakken er på vej, sender vi dig en opdatering med få dages mellemrum.\n\n"
   "Venlig hilsen\nKalijy"),
  "update": ("Opdatering om din ordre {order}",
   "Hej{name},\n\nEn kort opdatering om din ordre {order}: {status}\n\nFølg din pakke: {link}\n\n"
   "Spørgsmål? Svar bare på denne e-mail.\n\nVenlig hilsen\nKalijy"),
  "update_late": ("Opdatering om din ordre {order}",
   "Hej{name},\n\nDin ordre {order} tager lidt længere tid end forventet, og det beklager vi. "
   "Vi er i kontakt med transportøren for at sikre, at din ordre bliver leveret så hurtigt som muligt.\n\nFølg din pakke: {link}\n\n"
   "Er pakken allerede kommet, eller ser du noget mærkeligt i sporingen? Svar på denne e-mail, så hjælper vi dig med det samme.\n\n"
   "Venlig hilsen\nKalijy"),
  "status": {"OUT_FOR_DELIVERY": "din pakke bliver leveret i dag.",
             "ATTEMPTED_DELIVERY": "transportøren har forsøgt at levere din pakke. Se sporingen for næste skridt.",
             "_": "din pakke er på vej til dig."},
  "delivered": ("Er din Kalijy-oplader kommet godt frem?",
   "Hej{name},\n\nIfølge sporingen er din ordre {order} blevet leveret. Vi håber, du bliver glad for din Kalijy-oplader!\n\n"
   "Tips til første gang:\n• Tilslut opladeren til en USB-C-strømkilde (for eksempel en vægadapter).\n"
   "• Tykke covers, metalcovers eller covers, der ikke er magnetiske, kan forhindre opladning. Fjern også kort eller metalplader.\n"
   "• Dit etui til earbuds skal understøtte trådløs opladning.\n\nEr der noget galt, eller mangler der noget (også i det "
   "gratis tilbehørssæt)? Svar på denne e-mail, så løser vi det.\n\nVenlig hilsen\nKalijy"),
  "footer": "Denne e-mail er sendt automatisk. Du kan bare svare på den.",
 },
 "en": {
  "charger": "Kalijy 3-in-1 wireless charger", "kit": "Free 3-piece accessory kit (watch band {size})",
  "thanks": ("Thank you for your order {order}!",
   "Hi{name},\n\nThank you for your order with Kalijy! We've received your payment and are getting it ready right away.\n\n"
   "Your order {order}:\n{items}\n\nYou'll receive your tracking number by email within 24 hours.\n\n"
   "Questions? Just reply to this email.\n\nBest wishes,\nKalijy"),
  "tracking": ("Your tracking number for order {order}",
   "Hi{name},\n\nHere is the tracking number for your order {order}:\n\nNumber: {number}\nCarrier: {carrier}\n"
   "Track your parcel: {link}\n\nYour order is now being processed. Once the parcel leaves our warehouse, the first updates will show in the tracking.\n\n"
   "Once your parcel is on its way, we'll send you an update every few days.\n\nBest wishes,\nKalijy"),
  "update": ("An update on your order {order}",
   "Hi{name},\n\nA quick update on your order {order}: {status}\n\nTrack your parcel: {link}\n\n"
   "Questions? Just reply to this email.\n\nBest wishes,\nKalijy"),
  "update_late": ("An update on your order {order}",
   "Hi{name},\n\nYour order {order} is taking a little longer than expected, and we're sorry about that. "
   "We are in contact with the carrier to make sure your order gets delivered as soon as possible.\n\nTrack your parcel: {link}\n\n"
   "Has your parcel arrived in the meantime, or do you see something odd in the tracking? Reply to this email and we'll help you right away.\n\n"
   "Best wishes,\nKalijy"),
  "status": {"OUT_FOR_DELIVERY": "your parcel is out for delivery today.",
             "ATTEMPTED_DELIVERY": "the carrier tried to deliver your parcel. Check the tracking for the next step.",
             "_": "your parcel is on its way to you."},
  "delivered": ("Did your Kalijy charger arrive safely?",
   "Hi{name},\n\nAccording to the tracking, your order {order} has been delivered. We hope you enjoy your Kalijy charger!\n\n"
   "Tips for the first use:\n• Connect the charger to a USB-C power source (for example a wall adapter).\n"
   "• Thick, metal or non-magnetic cases can stop it from charging. Also remove cards or metal plates.\n"
   "• Your earbuds case must support wireless charging.\n\nIs anything wrong or missing (including in the free accessory "
   "kit)? Reply to this email and we'll sort it out.\n\nBest wishes,\nKalijy"),
  "footer": "This email was sent automatically. You can simply reply to it.",
 },
}


def lang_of(order):
    loc = (order.get("customerLocale") or "").lower()
    for code in ("nl", "sv", "da"):
        if loc.startswith(code):
            return code
    if loc:
        return "en"
    return {"NL": "nl", "SE": "sv", "DK": "da"}.get((order.get("shippingAddress") or {}).get("countryCodeV2"), "en")


def business_days(start, end):
    d, n = start.date(), 0
    while d < end.date():
        d += dt.timedelta(days=1)
        if d.weekday() < 5:
            n += 1
    return n


def first_tracking(f):
    for t in f.get("trackingInfo") or []:
        if t.get("number"):
            return t
    return None


def items_text(order, lang):
    rows = []
    for li in charger_lines(order):
        color = COLOR[lang].get((li.get("variantTitle") or "").strip().lower(), li.get("variantTitle") or "")
        rows.append(f"• {qty(li)}× {T[lang]['charger']}" + (f" ({color})" if color else ""))
    for li in kit_lines(order):
        if free_units(li):
            rows.append("• 1× " + T[lang]["kit"].format(size=li.get("variantTitle") or "?"))
    return "\n".join(rows)


def plan(order, now):
    """Emails due for this order right now: list of (state_key, stage, extra)."""
    out = []
    if order.get("cancelledAt") or order.get("displayFinancialStatus") in ("REFUNDED", "VOIDED") or not order.get("email"):
        return out
    oid = order["id"]
    age = now - parse_ts(order["createdAt"])
    if order.get("displayFinancialStatus") in ("PAID", "PARTIALLY_REFUNDED") and age <= dt.timedelta(hours=48):
        out.append((f"fu:thanks:{oid}", "thanks", None))
    fs = [f for f in order.get("fulfillments") or [] if f.get("status") not in ("CANCELLED", "ERROR", "FAILURE")
          and first_tracking(f)]
    if not fs:
        return out
    f = min(fs, key=lambda x: x["createdAt"])
    t = first_tracking(f)
    link = t.get("url") or f"https://t.17track.net/en#nums={t['number']}"
    info = {"number": t["number"], "carrier": t.get("company") or "-", "link": link, "status": f.get("displayStatus")}
    uploaded = parse_ts(f["createdAt"])
    if (now - uploaded).days <= 10:
        out.append((f"fu:tracking:{oid}", "tracking", info))
    # Days are counted from when the parcel actually leaves (carrier "in transit"), not from the tracking upload.
    # If Shopify never gets a carrier status, counting starts after the 2-day processing time.
    if f.get("inTransitAt"):
        shipped = parse_ts(f["inTransitAt"])
    elif f.get("displayStatus") in ("IN_TRANSIT", "OUT_FOR_DELIVERY", "ATTEMPTED_DELIVERY", "DELIVERED"):
        shipped = uploaded
    else:
        shipped = uploaded + dt.timedelta(days=PROCESSING_DAYS)
    if shipped > now:
        return out
    days = (now - shipped).days
    status = f.get("displayStatus")
    if status == "DELIVERED" or f.get("deliveredAt"):
        if f.get("deliveredAt") and DELIVERED_DAYS <= (now - parse_ts(f["deliveredAt"])).days <= DELIVERED_DAYS + 5:
            out.append((f"fu:delivered:{oid}", "delivered", info))
        return out
    if status in DONE:
        return out
    k = days // UPDATE_DAYS
    if k >= 1 and days <= UPDATE_MAX_DAYS:
        late = business_days(shipped, now) > LATE_AFTER_BDAYS
        out.append((f"fu:update{k}:{oid}", "update_late" if late else "update", dict(info, late=late, bdays=business_days(shipped, now))))
    return out


def run():
    if not env_bool("FOLLOWUP_ENABLED", True):
        log.info("customer_followup: disabled")
        return
    now = utcnow()
    hour = now.astimezone(CUSTOMER_TZ).hour
    daytime = int(env("FOLLOWUP_SEND_FROM", "9")) <= hour < int(env("FOLLOWUP_SEND_TO", "20"))
    since = (now - dt.timedelta(days=45)).strftime("%Y-%m-%dT%H:%M:%SZ")
    sent, owner = 0, []
    for o in fetch_orders(f"created_at:>='{since}'"):
        lang = lang_of(o)
        sa = o.get("shippingAddress") or {}
        first = (sa.get("firstName") or "").strip()
        tracking_now = False
        for key, stage, info in plan(o, now):
            if state.seen(key) or sent >= MAX_PER_RUN:
                continue
            if stage.startswith("update") and tracking_now:
                continue  # the tracking email just went out: the first update comes later
            if stage in ("update", "update_late", "delivered") and not daytime:
                continue
            vals = {"name": f" {first}" if first else "", "order": o["name"], "lo": SHIP_LO, "hi": SHIP_HI}
            if stage == "thanks":
                vals["items"] = items_text(o, lang)
            if info:
                vals.update(info)
                vals["status"] = T[lang]["status"].get(info.get("status"), T[lang]["status"]["_"])
            subj, body = T[lang][stage]
            msg = mailer.build(o["email"], ("[TEST] " if o.get("test") else "") + subj.format(**vals), body.format(**vals) + "\n\n—\n" + T[lang]["footer"])
            try:
                ok = mailer.send(msg)
            except Exception as e:
                log.error("customer_followup: %s for %s failed: %s", stage, o["name"], e)
                continue
            if ok:
                state.mark(key)
                sent += 1
            if stage == "tracking":
                tracking_now = True
            line = f"{stage} email {'sent' if ok else '(dry run)'}: {order_admin_link(o)} · {esc(sa.get('countryCodeV2'))} · {lang}"
            if stage == "update_late":
                line = "🐢 " + line + f"\n   {info['bdays']} business days since shipping: ask MutualDropshipping where it is. {esc(info['link'])}"
            owner.append(line)
    log.info("customer_followup: %d email(s) sent", sent)
    if owner:
        notify("<b>📬 Customer emails</b>\n" + "\n".join(owner))


if __name__ == "__main__":
    run()

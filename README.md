# Kalijy bots

Four bots that run on their own every 30 minutes on GitHub Actions, use **OpenAI (gpt-6-luna)** by default, or Grok (xAI) if you choose for the AI
parts, and message you on **Telegram**. They do not use Claude.

| Bot | When | What it does | What it never does |
|---|---|---|---|
| `order_watch` | every run | New orders → Telegram with items, country, total and **exactly what to enter in MutualDropshipping** (charger colour × quantity + 1 gift kit with its band size). Flags: no gift kit, gift kit not free, more than 1 free kit, more than 3 chargers, extra kits the customer paid for. **After MutualDropshipping ships:** compares what it reported as shipped with what was ordered (e.g. "Kit: ordered 1, shipped 0"). Alerts: not fulfilled after 24h / 72h, fulfilled without tracking, shipped 21+ days and not delivered, gift kit missing, Shopify fraud flag. | Pay or submit supplier orders, edit orders. |
| `support_inbox` | every run | Reads new customer emails, Grok detects language + topic, looks up the order in Shopify, writes the reply in the customer's language from `bots/kb.md` + order data. "Where is my order" with a matching order → answered automatically. Everything else → **draft in your mailbox** + Telegram alert. | Promise refunds, replacements, discounts, cancellations or dates. Share order data with an email that is not the order's email. |
| `customer_followup` | every run | Emails the customer in their language (NL/SV/DA/EN, fixed texts, no AI): **right after payment** thank you + items + the free gift kit and its band size + "tracking number within 24 hours"; **as soon as MutualDropshipping uploads the tracking number** the number, carrier and link; **every 3 days** a status update until delivered (apologises if late, and alerts you); **2 days after delivery** "did it arrive well?" + tips. | Email cancelled or refunded orders. Send the same email twice. |
| `ads_monitor` | every 6 h | Meta ads: ads that spent your limit with 0 purchases, ads under your CTR floor, daily spend above your cap. | Raise budgets. Pause ads unless you switch `META_AUTO_PAUSE` on. |
| `daily_report` | once a day after `REPORT_HOUR` | Yesterday: orders, revenue per currency, AOV, bundle mix, countries, refunds, 7-day total, Meta spend/ROAS/CPA/top ads, open order problems + a short Grok analysis. | Invent numbers: Grok is told to use only the data it receives. |

Every alert is sent once (the bots keep a small memory file between runs).

---

## 1. Accounts you need (you create these, nobody else should hold the passwords)

1. **GitHub**: free account. Create a **private** repository, e.g. `kalijy-bots`, and upload all files of this folder (keep the `.github` folder).
2. **Telegram**: in Telegram open **@BotFather** → `/newbot` → copy the **bot token**. Send any message to your new bot, then open `https://api.telegram.org/bot<TOKEN>/getUpdates` in a browser and copy `"chat":{"id": ...}`: that is your **chat id**.
3. **OpenAI API** (not the ChatGPT subscription): on platform.openai.com create an account, add credit under Billing (e.g. $5), then API keys → Create new secret key. Default model `gpt-6-luna`. (Grok stays possible: set `AI_PROVIDER` = `xai` and add `XAI_API_KEY`.)
4. **Shopify access** (Shopify no longer lets you create "custom apps" inside the admin): go to the **Shopify Dev Dashboard** (dev.shopify.com) → create an app "Kalijy Bots" → access scopes **read_orders, read_products, read_fulfillments**, and turn on **protected customer data** access for **name and email** (needed for the follow-up emails and the support bot) → release the version → install it on your store → in the app settings copy **Client ID** and **Client secret**. (Button names may differ slightly.)
5. **Support mailbox (Spacemail)**:
   1. In Spacemail create the mailbox `support@kalijy.com` (any name you like) and its password.
   2. Connect the domain: kalijy.com's DNS is at Spaceship, so in Spacemail use the option to connect/verify the domain and let it add the **MX, SPF (TXT) and DKIM** records automatically. Until the MX records exist, emails to @kalijy.com bounce (on 1 Nov 2026 kalijy.com had no MX record at all).
   3. Add a DMARC record in Spaceship DNS: host `_dmarc`, type TXT, value `v=DMARC1; p=none; rua=mailto:support@kalijy.com`.
   4. Send a test email from your phone to support@kalijy.com and reply to it from Spacemail webmail: both must work before you switch the bot on.
   5. Settings for the bot: IMAP and SMTP are both `mail.spacemail.com` (ports 993 and 465, SSL), which are already the defaults. Username = the full address.
   6. In Shopify: Settings → **Notifications → Sender email** → `support@kalijy.com` and click **Authenticate** (Shopify gives you DNS records to add in Spaceship), and Settings → Store details → **customer email** → `support@kalijy.com`. Then customers' replies to order emails land in the bot's mailbox.
   - A free trial ends: when it does, the mailbox stops and customer emails bounce. Note the end date.
6. **Meta (optional, when ads run)**: Business Settings → Users → **System users** → add one → assign your ad account → generate a token with `ads_read` (add `ads_management` only if you want auto-pause).

## 2. Put the keys in GitHub

Repository → **Settings → Secrets and variables → Actions**.

**Secrets** (hidden):

| Name | Value |
|---|---|
| `SHOPIFY_CLIENT_ID` / `SHOPIFY_CLIENT_SECRET` | from step 4 |
| `OPENAI_API_KEY` | from platform.openai.com → API keys (default AI) |
| `XAI_API_KEY` | only if you set `AI_PROVIDER` = `xai` (Grok) |
| `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` | from step 2 |
| `IMAP_USER` / `IMAP_PASSWORD` | `support@kalijy.com` / its Spacemail password |
| `META_ACCESS_TOKEN` | optional, from step 6 |

**Variables** (settings; leave empty to use the default):

| Name | Default | Meaning |
|---|---|---|
| `SHOPIFY_SHOP` | `hsd9ie-ij.myshopify.com` | your store |
| `ALERT_EMAIL` | – | your own email: alerts and the daily report go there **instead of Telegram** (leave the two Telegram secrets empty) |
| `DRY_RUN` | `false` | `true` = print only, send nothing (use for the first test) |
| `REPORT_HOUR` | `8` | hour of the morning report |
| `BOT_TIMEZONE` | `Africa/Casablanca` | your time zone |
| `REPORT_LANGUAGE` | `English` | language of the Grok analysis (e.g. `French`) |
| `AI_PROVIDER` | `openai` | `openai` (gpt-6-luna) or `xai` (Grok) |
| `AI_MODEL` | `gpt-6-luna` / `grok-4.3` | model id, if you want another one |
| `SUPPORT_ONLY_TO` | – | leave empty with a dedicated Spacemail box; set it only if the box also receives other mail |
| `IMAP_HOST` / `SMTP_HOST` | `mail.spacemail.com` | change only if you switch email provider |
| `SUPPORT_REPLY_FROM` | `SUPPORT_ONLY_TO` | address the replies are sent from |
| `AUTO_SEND_CATEGORIES` | `order_status` | which topics may be answered without you |
| `SUPPORT_AUTO_SEND_ENABLED` | `true` | `false` = only drafts, never auto-send |
| `META_AD_ACCOUNT_ID` | – | e.g. `act_1234567890` |
| `META_KILL_SPEND` | `0` (off) | 3-day spend with 0 purchases that triggers a pause alert |
| `META_MIN_CTR` | `0` (off) | link-CTR floor in % (judged after 2,000 impressions) |
| `META_DAILY_SPEND_CAP` | `0` (off) | alert when today's spend passes this |
| `META_TARGET_ROAS` | `0` | shown in the daily report |
| `META_AUTO_PAUSE` | `false` | `true` = the bot pauses ads that hit `META_KILL_SPEND` |
| `FOLLOWUP_ENABLED` | `true` | `false` = no follow-up emails to customers |
| `FOLLOWUP_UPDATE_DAYS` | `3` | days between tracking update emails |
| `FOLLOWUP_LATE_EXTRA_BDAYS` | `0` | extra business days after the 7 promised (counted from "in transit") before updates say "we are in contact with the carrier" and you get an alert |
| `MAX_CHARGERS_PER_ORDER` | `3` | more chargers than this in one order gets flagged |

The Meta rules are off until **you** choose the numbers: they depend on your margin per order.

## 3. Test, then switch on

1. Set variable `DRY_RUN` = `true`.
2. Repository → **Actions → kalijy-bots → Run workflow**, input `all`. Open the run log: you will see what each bot *would* send.
3. Set `DRY_RUN` = `false`, run `daily_report` once: you should get the Telegram message.
4. From then on it runs by itself every 30 minutes. To stop everything: Actions → kalijy-bots → **Disable workflow**.

### What the MutualDropshipping check can and cannot see
- I found no public API for MutualDropshipping, so no bot can read what you typed inside MD before it ships. The bot gives you the exact line to enter for each order. You check it against MD before you press Pay.
- Once MD ships and sends the tracking back to Shopify, the bot compares the items MD marked as shipped with the order. If MD marks the whole order shipped while a box was packed wrong, nobody can see that from Shopify. Only the customer can, which is why the "delivered" email asks whether anything is missing.
- In Shopify → Settings → Notifications, keep **Shipping confirmation** and **Shipping update** switched on: those are the emails with the tracking number when MD ships.

## 4. Costs and limits

- **GitHub**: one short job every 30 minutes, about 1,500 runner-minutes a month. GitHub Free includes $12/month of Actions compute for private repos (≈2,000 Linux minutes at $0.006/min). Scheduled runs can start a few minutes late; nothing is lost thanks to the memory file.
- **AI**: gpt-6-luna costs $0.10 / 1M input and $0.50 / 1M output tokens (OpenAI pricing page, Nov 2026). My estimate, not a measured figure: about $0.001 per customer email (two calls), so roughly 5,000 emails for $5.
- **Safeguards**: a reply that promises an action (investigation, refund, replacement, "we contacted the carrier", a delivery date) is never auto-sent: it becomes a draft and you get an alert. Broken AI answers are retried; after 3 failures you get an alert to answer yourself.
- **Telegram**: free.
- **Not included yet**: Google Ads reporting (needs a Google Ads developer token, which Google approves separately) and any direct link to MutualDropshipping (I found no public API for it; the order bot reminds you to submit and pay there).
- **AI transparency**: auto-answered emails end with a line saying it is an automated reply and how to reach a person.

## 5. Updating facts

`bots/kb.md` is the only product/policy knowledge the support bot has. When prices, delivery times, policies or the gift kit change, edit that file in GitHub. Wrong facts there = wrong answers to customers.

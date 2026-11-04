"""Send one email from the support mailbox (SMTP) and keep a copy in its Sent folder (IMAP)."""
import email.utils
import imaplib
import smtplib
import time
from email.message import EmailMessage

from common import DRY_RUN, env, log

IMAP_HOST = env("IMAP_HOST", "mail.spacemail.com")
SMTP_HOST = env("SMTP_HOST", "mail.spacemail.com")
SMTP_PORT = int(env("SMTP_PORT", "465"))


NET_TIMEOUT = 30  # seconds; a hung mail server must never stall the whole run
NET_ERRORS = (OSError, imaplib.IMAP4.abort, smtplib.SMTPServerDisconnected, smtplib.SMTPConnectError)


def imap_connect(tries=3):
    """Log in to the mailbox, retrying short network glitches (SSL EOF, timeouts, resets)."""
    user, pwd = env("IMAP_USER", required=True), env("IMAP_PASSWORD", required=True)
    for i in range(tries):
        try:
            M = imaplib.IMAP4_SSL(IMAP_HOST, timeout=NET_TIMEOUT)
            M.login(user, pwd)
            return M
        except NET_ERRORS as e:
            if i == tries - 1:
                raise
            log.warning("mailbox connection failed (%s), retrying", e)
            time.sleep(5 * (i + 1))


def sender_address():
    return env("SUPPORT_REPLY_FROM") or env("SUPPORT_ONLY_TO") or env("IMAP_USER") or "support@kalijy.com"


def build(to_addr, subject, body):
    m = EmailMessage()
    m["From"] = email.utils.formataddr((env("SUPPORT_FROM_NAME", "Kalijy"), sender_address()))
    m["To"] = to_addr
    m["Subject"] = subject
    m["Date"] = email.utils.formatdate(localtime=True)
    m["Message-ID"] = email.utils.make_msgid(domain=(sender_address() or "kalijy.com").split("@")[-1])
    m.set_content(body)
    return m


def _sent_folder(M):
    from support_inbox import special_folder  # same folder detection as the support bot
    return special_folder(M, "\\Sent", ["Sent", "Sent Items", "Sent Messages", "INBOX.Sent"])


def send(msg):
    """Returns True when sent. In DRY_RUN it only prints."""
    if DRY_RUN:
        print(f"---- EMAIL (not sent) to {msg['To']} ----\nSubject: {msg['Subject']}\n{msg.get_content()}\n----")
        return False
    user, pwd = env("IMAP_USER", required=True), env("IMAP_PASSWORD", required=True)
    for i in range(3):
        try:
            with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=NET_TIMEOUT) as s:
                s.login(user, pwd)
                s.send_message(msg)
            break
        except NET_ERRORS as e:  # connection never got to sending: safe to retry
            if i == 2 or isinstance(e, smtplib.SMTPResponseException):
                raise
            log.warning("SMTP connection failed (%s), retrying", e)
            time.sleep(5 * (i + 1))
    if "gmail" not in IMAP_HOST:  # Gmail files sent mail itself
        try:
            M = imap_connect(tries=2)
            M.append(_sent_folder(M), "\\Seen", imaplib.Time2Internaldate(time.time()), msg.as_bytes())
            M.logout()
        except Exception as e:  # the email is already sent; a missing Sent copy is not worth failing for
            log.warning("could not save a copy in Sent: %s", e)
    return True

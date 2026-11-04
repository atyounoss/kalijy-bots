"""Diagnostic: python bots/main.py maildiag — tests TLS to the mail server (no login) and prints the result."""
import socket
import ssl

from mailer import IMAP_HOST, SMTP_HOST, SMTP_PORT


def run():
    print("resolve", IMAP_HOST, socket.getaddrinfo(IMAP_HOST, 993, proto=socket.IPPROTO_TCP)[:2])
    for host, port in [(IMAP_HOST, 993), (SMTP_HOST, SMTP_PORT), (IMAP_HOST, 143)]:
        for attempt in range(2):
            try:
                s = socket.create_connection((host, port), timeout=15)
                if port == 143:
                    print(host, port, "plain banner:", s.recv(200))
                    s.close()
                    break
                t = ssl.create_default_context().wrap_socket(s, server_hostname=host)
                print(host, port, "TLS OK", t.version(), t.getpeercert().get("subject"))
                t.close()
                break
            except Exception as e:
                print(host, port, "FAIL", type(e).__name__, e)

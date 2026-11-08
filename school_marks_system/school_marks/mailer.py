"""Sending notification emails to staff.

Email only works after the admin has filled in an email account to send FROM under
Settings -> Email (for example the school's Gmail address with an "app password"), and while the
computer running the system has internet. Until then nothing is sent and nothing breaks.

Emails never contain a password.
"""
import smtplib
import ssl
import threading
from email.message import EmailMessage
from email.utils import formataddr

KEYS = ("mail_enabled", "mail_host", "mail_port", "mail_security", "mail_user", "mail_password", "mail_from", "site_url")
_threads = []


def config(db):
    cfg = {k: "" for k in KEYS}
    cfg.update({r["key"]: r["value"] for r in db.execute(
        "SELECT key, value FROM settings WHERE key IN (%s)" % ",".join("?" * len(KEYS)), KEYS)})
    cfg["on"] = cfg["mail_enabled"] == "1" and bool(cfg["mail_host"])
    return cfg


def send(cfg, to, subject, body, school=""):
    """Send one plain-text email now. Raises an exception with the reason if it cannot be sent."""
    sender = cfg["mail_from"] or cfg["mail_user"]
    if not sender:
        raise ValueError("no sender address is set")
    msg = EmailMessage()
    msg["From"] = formataddr((school or "School Marks System", sender))
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)
    port = int(cfg["mail_port"] or (465 if cfg["mail_security"] == "ssl" else 587))
    if cfg["mail_security"] == "ssl":
        server = smtplib.SMTP_SSL(cfg["mail_host"], port, timeout=20, context=ssl.create_default_context())
    else:
        server = smtplib.SMTP(cfg["mail_host"], port, timeout=20)
    try:
        if cfg["mail_security"] == "starttls":
            server.starttls(context=ssl.create_default_context())
        if cfg["mail_user"]:
            server.login(cfg["mail_user"], cfg["mail_password"])
        server.send_message(msg)
    finally:
        try:
            server.quit()
        except Exception:
            pass


def send_later(cfg, to, subject, body, school, on_done):
    """Send in the background so the person who clicked never waits for the mail server.
    `on_done(error)` is called with None when it was sent, or with the reason when it was not."""
    def work():
        try:
            send(cfg, to, subject, body, school)
            on_done(None)
        except Exception as problem:          # any failure: no internet, wrong password, bad address ...
            on_done(str(problem) or problem.__class__.__name__)
    thread = threading.Thread(target=work, daemon=True)
    _threads.append(thread)
    thread.start()


def wait():
    """Wait until every email started so far has been sent or has failed (used by the tests)."""
    while _threads:
        _threads.pop().join()

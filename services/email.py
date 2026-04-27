import imaplib
import smtplib
import email
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.header import decode_header
import config
import brain


def _decode(value: str) -> str:
    parts = decode_header(value or "")
    result = []
    for part, enc in parts:
        if isinstance(part, bytes):
            result.append(part.decode(enc or "utf-8", errors="replace"))
        else:
            result.append(str(part))
    return "".join(result)


def _extract_preview(msg, max_chars: int = 200) -> str:
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain":
                payload = part.get_payload(decode=True)
                if payload:
                    return payload.decode(errors="replace")[:max_chars].strip()
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            return payload.decode(errors="replace")[:max_chars].strip()
    return ""


def is_configured() -> bool:
    return bool(config.EMAIL_ADDRESS and config.EMAIL_PASSWORD and config.EMAIL_IMAP_HOST)


def _is_vip(sender: str, vip_list: list) -> bool:
    sender_lower = sender.lower()
    return any(v.lower() in sender_lower for v in vip_list if v)


def _is_blacklisted(sender: str, subject: str, blacklist: list) -> bool:
    sender_lower = sender.lower()
    subject_lower = subject.lower()
    return any(b.lower() in sender_lower or b.lower() in subject_lower for b in blacklist if b)


def query(folder: str = "INBOX", filter: str = "UNSEEN", limit: int = 5) -> list[dict]:
    if not is_configured():
        return []
    settings = brain.read(section="settings")
    contacts = settings.get("contacts", {})
    vip_list = contacts.get("email_vip", [])
    blacklist = contacts.get("email_blacklist", [])
    fetch_limit = limit * 4
    results = []
    try:
        with imaplib.IMAP4_SSL(config.EMAIL_IMAP_HOST, 993) as mail:
            mail.login(config.EMAIL_ADDRESS, config.EMAIL_PASSWORD)
            mail.select(folder)
            _, data = mail.search(None, filter)
            ids = data[0].split()
            if not ids or ids == [b""]:
                return []
            for uid in reversed(ids[-fetch_limit:]):
                _, msg_data = mail.fetch(uid, "(RFC822)")
                raw = msg_data[0][1]
                msg = email.message_from_bytes(raw)
                sender = _decode(msg.get("From", ""))
                subject = _decode(msg.get("Subject", ""))
                entry = {
                    "subject": subject,
                    "from": sender,
                    "date": msg.get("Date", ""),
                    "preview": _extract_preview(msg),
                    "vip": _is_vip(sender, vip_list),
                }
                if not _is_blacklisted(sender, subject, blacklist):
                    results.append(entry)
    except Exception as e:
        return [{"error": str(e)}]
    vips = [r for r in results if r["vip"]]
    rest = [r for r in results if not r["vip"]]
    return vips + rest[:limit]


def send(to: str, subject: str, body: str) -> str:
    if not config.EMAIL_SEND_ENABLED:
        return "E-Mail-Versand ist deaktiviert (EMAIL_SEND_ENABLED=false). Simon muss das explizit bestätigen."
    if not is_configured():
        return "E-Mail nicht konfiguriert (EMAIL_ADDRESS, EMAIL_PASSWORD, EMAIL_SMTP_HOST fehlen)."
    msg = MIMEMultipart()
    msg["Subject"] = subject
    msg["From"] = config.EMAIL_ADDRESS
    msg["To"] = to
    msg.attach(MIMEText(body, "plain", "utf-8"))
    try:
        with smtplib.SMTP(config.EMAIL_SMTP_HOST, 587) as smtp:
            smtp.ehlo()
            smtp.starttls()
            smtp.login(config.EMAIL_ADDRESS, config.EMAIL_PASSWORD)
            smtp.send_message(msg)
        return f"E-Mail an {to} gesendet."
    except Exception as e:
        return f"Fehler beim Senden: {e}"

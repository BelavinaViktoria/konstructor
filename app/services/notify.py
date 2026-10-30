"""Уведомления в приложении, письма по SMTP (если настроен) и единый журнал действий."""
import logging
import smtplib
from datetime import datetime
from email.message import EmailMessage

from flask import current_app

from ..extensions import db
from ..models import AuditLog, Notification, Setting

log = logging.getLogger(__name__)


def audit(text: str, actor: str = "Система", plan=None, ipr=None, kind: str = "act") -> None:
    db.session.add(AuditLog(text=text, actor=actor, kind=kind, plan_id=plan.id if plan else None, ipr_id=ipr.id if ipr else None))


def get_setting(key: str, default: str = "") -> str:
    row = db.session.get(Setting, key)
    return row.value if row else default


def set_setting(key: str, value: str) -> None:
    row = db.session.get(Setting, key)
    if row:
        row.value = value
    else:
        db.session.add(Setting(key=key, value=value))


def _send_email(to: str, subject: str, body: str) -> bool:
    cfg = current_app.config
    if not cfg.get("SMTP_HOST") or not to:
        return False
    msg = EmailMessage()
    msg["Subject"], msg["From"], msg["To"] = subject, cfg.get("SMTP_FROM") or cfg.get("SMTP_USER"), to
    msg.set_content(body)
    try:
        with smtplib.SMTP(cfg["SMTP_HOST"], cfg["SMTP_PORT"], timeout=15) as smtp:
            smtp.starttls()
            if cfg.get("SMTP_USER"):
                smtp.login(cfg["SMTP_USER"], cfg["SMTP_PASSWORD"])
            smtp.send_message(msg)
        return True
    except (OSError, smtplib.SMTPException):
        log.exception("Письмо не отправлено: %s", subject)
        return False


def notify(users, subject: str, body: str = "", link: str = "", dedupe_key: str | None = None) -> int:
    """Создаёт уведомления. С dedupe_key одно и то же событие не повторяется для одного получателя."""
    count, seen = 0, set()
    for user in users:
        if user is None or user.id in seen or not user.active:
            continue
        seen.add(user.id)
        if dedupe_key and Notification.query.filter_by(user_id=user.id, dedupe_key=dedupe_key).first():
            continue
        note = Notification(user_id=user.id, subject=subject, body=body, link=link, dedupe_key=dedupe_key)
        full_link = current_app.config["BASE_URL"].rstrip("/") + link if link else ""
        if _send_email(user.email, subject, f"{body}\n\n{full_link}".strip()):
            note.emailed_at = datetime.now()
        db.session.add(note)
        count += 1
    return count

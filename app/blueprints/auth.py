"""Вход, выход, уведомления."""
from datetime import datetime, timedelta
from urllib.parse import urlparse

from flask import Blueprint, abort, flash, g, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash

from ..extensions import db
from ..models import Notification, User
from ..security import HOME, login_required

bp = Blueprint("auth", __name__)
MAX_FAILED = 5
LOCK_MINUTES = 15


def _safe_next(target: str | None) -> str | None:
    if not target:
        return None
    parts = urlparse(target)
    if parts.scheme or parts.netloc or not target.startswith("/") or target.startswith("//"):
        return None
    return target


@bp.route("/")
def index():
    if not g.user:
        return redirect(url_for("auth.login"))
    return redirect(url_for(HOME[g.user.role]))


@bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        user = User.query.filter_by(email=email).first()
        now = datetime.now()
        if user and user.locked_until and user.locked_until > now:
            flash(f"Слишком много попыток. Попробуйте через {LOCK_MINUTES} минут.", "error")
        elif user and user.active and check_password_hash(user.password_hash, request.form.get("password", "")):
            user.failed_logins, user.locked_until = 0, None
            db.session.commit()
            session.clear()
            session["uid"] = user.id
            return redirect(_safe_next(request.args.get("next")) or url_for(HOME[user.role]))
        else:
            if user:
                user.failed_logins += 1
                if user.failed_logins >= MAX_FAILED:
                    user.locked_until, user.failed_logins = now + timedelta(minutes=LOCK_MINUTES), 0
                db.session.commit()
            flash("Неверная почта или пароль.", "error")
    return render_template("login.html")


@bp.post("/logout")
def logout():
    session.clear()
    return redirect(url_for("auth.login"))


@bp.get("/notifications")
@login_required
def notifications():
    rows = Notification.query.filter_by(user_id=g.user.id).order_by(Notification.created_at.desc()).limit(200).all()
    return render_template("notifications.html", rows=rows)


@bp.post("/notifications/read-all")
@login_required
def notifications_read_all():
    Notification.query.filter_by(user_id=g.user.id, read_at=None).update({"read_at": datetime.now()})
    db.session.commit()
    return redirect(url_for("auth.notifications"))


@bp.post("/notifications/<int:note_id>/open")
@login_required
def notification_open(note_id):
    note = db.session.get(Notification, note_id)
    if not note or note.user_id != g.user.id:
        abort(404)
    note.read_at = note.read_at or datetime.now()
    db.session.commit()
    return redirect(_safe_next(note.link) or url_for("auth.notifications"))

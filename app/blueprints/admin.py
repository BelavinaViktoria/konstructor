"""Администрирование: пользователи, библиотека задач, письмо о пребординге, реестры и журнал."""
from flask import Blueprint, abort, current_app, flash, g, redirect, render_template, request, url_for
from werkzeug.security import generate_password_hash

from .. import catalog
from ..extensions import db
from ..models import LEARNING, ROLE_LABELS, AuditLog, LibraryTask, Position, User
from ..security import roles
from ..services.notify import audit, get_setting, set_setting

bp = Blueprint("admin", __name__, url_prefix="/admin")


def short_name(full_name: str) -> str:
    parts = full_name.split()
    return parts[0] + " " + " ".join(p[0] + "." for p in parts[1:]) if len(parts) > 1 else full_name


@bp.get("")
@roles(LEARNING)
def index():
    cfg = current_app.config
    integrations = [("Поток (приём «Принял оффер»)", bool(cfg["POTOK_API_KEY"])), ("Яндекс Форма (анкеты кандидатов)", bool(cfg["FORMS_API_KEY"])),
                    ("Почта (SMTP)", bool(cfg["SMTP_HOST"])), ("ИИ на базе Claude", bool(cfg["ANTHROPIC_API_KEY"]))]
    return render_template("admin/index.html", users=User.query.order_by(User.role, User.full_name).all(), roles=ROLE_LABELS,
                           library=LibraryTask.query.order_by(LibraryTask.family, LibraryTask.title).all(), positions=Position.query.order_by(Position.name).all(),
                           log=AuditLog.query.order_by(AuditLog.ts.desc()).limit(100).all(), integrations=integrations,
                           pre_subject=get_setting("preboarding_subject", catalog.PREBOARDING_SUBJECT), pre_body=get_setting("preboarding_body", catalog.PREBOARDING_BODY))


@bp.post("/users")
@roles(LEARNING)
def user_create():
    full_name, email, role, password = (request.form.get(k, "").strip() for k in ("full_name", "email", "role", "password"))
    email = email.lower()
    if not full_name or "@" not in email or role not in ROLE_LABELS or len(password) < 10:
        flash("Заполните ФИО, почту, роль и пароль не короче 10 символов.", "error")
    elif User.query.filter_by(email=email).first():
        flash("Пользователь с такой почтой уже есть.", "error")
    else:
        db.session.add(User(full_name=full_name, short_name=short_name(full_name), email=email, role=role,
                            password_hash=generate_password_hash(password, method=current_app.config["PASSWORD_HASH_METHOD"])))
        audit(f"Создан пользователь {full_name}", g.user.short_name)
        db.session.commit()
        flash("Пользователь создан.", "ok")
    return redirect(url_for("admin.index") + "#users")


@bp.post("/users/<int:user_id>/toggle")
@roles(LEARNING)
def user_toggle(user_id):
    user = db.session.get(User, user_id)
    if not user:
        abort(404)
    if user.id == g.user.id:
        flash("Нельзя отключить собственную учётную запись.", "error")
    else:
        user.active = not user.active
        audit(f"Учётная запись {user.full_name}: {'включена' if user.active else 'отключена'}", g.user.short_name)
        db.session.commit()
    return redirect(url_for("admin.index") + "#users")


def _library_fields(item: LibraryTask, form) -> bool:
    title = form.get("title", "").strip()
    algorithm = [x.strip() for x in form.get("algorithm", "").splitlines() if x.strip()]
    family = form.get("family", "")
    if not title or not algorithm or family not in catalog.FAMILIES:
        flash("Укажите семейство, название и алгоритм: без алгоритма задачу сохранить нельзя.", "error")
        return False
    item.title, item.family, item.algorithm = title, family, algorithm
    item.kind = form.get("kind", "prof")
    item.description, item.fmt = form.get("description", "").strip(), form.get("fmt", "Документ").strip()
    item.term, item.responsible = form.get("term", "Первая неделя"), form.get("responsible", "Наставник").strip()
    return True


@bp.route("/library/new", methods=["GET", "POST"])
@roles(LEARNING)
def library_new():
    item = LibraryTask(family="sales", kind="prof", fmt="Документ", term="Первая неделя", responsible="Наставник", algorithm=[], title="")
    if request.method == "POST" and _library_fields(item, request.form):
        db.session.add(item)
        audit(f"В библиотеку добавлена задача «{item.title}»", g.user.short_name)
        db.session.commit()
        return redirect(url_for("admin.index") + "#library")
    return render_template("admin/library_form.html", item=item, is_new=True)


@bp.route("/library/<int:item_id>", methods=["GET", "POST"])
@roles(LEARNING)
def library_edit(item_id):
    item = db.session.get(LibraryTask, item_id)
    if not item:
        abort(404)
    if request.method == "POST" and _library_fields(item, request.form):
        audit(f"Изменена задача библиотеки «{item.title}»", g.user.short_name)
        db.session.commit()
        return redirect(url_for("admin.index") + "#library")
    return render_template("admin/library_form.html", item=item, is_new=False)


@bp.post("/library/<int:item_id>/delete")
@roles(LEARNING)
def library_delete(item_id):
    item = db.session.get(LibraryTask, item_id)
    if not item:
        abort(404)
    audit(f"Из библиотеки удалена задача «{item.title}»", g.user.short_name)
    db.session.delete(item)
    db.session.commit()
    return redirect(url_for("admin.index") + "#library")


@bp.post("/preboarding")
@roles(LEARNING)
def preboarding():
    set_setting("preboarding_subject", request.form.get("subject", "").strip() or catalog.PREBOARDING_SUBJECT)
    set_setting("preboarding_body", request.form.get("body", "").strip() or catalog.PREBOARDING_BODY)
    audit("Изменён текст письма о пребординге", g.user.short_name)
    db.session.commit()
    flash("Текст письма сохранён.", "ok")
    return redirect(url_for("admin.index") + "#preboarding")

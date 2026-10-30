"""Модуль ИПР: кандидаты в резерв, конструктор, карточка, план резервиста."""
from datetime import date

from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for

from .. import catalog
from ..extensions import db
from ..models import (
    IPR_APPROVAL, IPR_APPROVED, IPR_DONE, IPR_RETURNED, IPR_STATUSES, IPR_WORK, LEARNING, MANAGER, MENTOR, RESERVIST, TOP, AuditLog, Ipr, IprBook,
    IprCandidate, IprCourse, IprMeeting, IprPractice, IprSkill, User,
)
from ..security import can_view_ipr, login_required, roles
from ..services import ai
from ..services import ipr as svc
from ..services.dates import fmt_date, parse_date
from ..services.notify import audit, notify
from ..services.plans import RuleError, safe_link

bp = Blueprint("ipr", __name__, url_prefix="/ipr")


def _ipr(ipr_id: int) -> Ipr:
    ipr = db.session.get(Ipr, ipr_id)
    if not ipr:
        abort(404)
    if not can_view_ipr(g.user, ipr):
        abort(403)
    return ipr


def _child(model, child_id: int):
    item = db.session.get(model, child_id)
    if not item:
        abort(404)
    if not can_view_ipr(g.user, item.ipr):
        abort(403)
    return item


def _users(role: str) -> list[User]:
    return User.query.filter_by(role=role, active=True).order_by(User.full_name).all()


def _card(ipr: Ipr):
    return redirect(url_for("ipr.card", ipr_id=ipr.id))


def _build(ipr: Ipr):
    return redirect(url_for("ipr.build", ipr_id=ipr.id))


# ---------- Кандидаты ----------

@bp.get("/candidates")
@roles(LEARNING)
def candidates():
    status = request.args.get("status", "")
    query = IprCandidate.query
    if status in catalog.CANDIDATE_STATUS:
        query = query.filter_by(status=status)
    return render_template("ipr/candidates.html", rows=query.order_by(IprCandidate.submitted_at.desc()).all(), status=status)


@bp.post("/candidates/<int:cand_id>")
@roles(LEARNING)
def candidate_update(cand_id):
    candidate = db.session.get(IprCandidate, cand_id)
    if not candidate:
        abort(404)
    status = request.form.get("status", "")
    if status not in catalog.CANDIDATE_STATUS:
        abort(400)
    if status == "mgr_ok" and candidate.status == "mgr_ok":
        return redirect(url_for("ipr.candidates"))
    candidate.status, candidate.comment = status, request.form.get("comment", "").strip()
    if status == "mgr_ok":
        ipr = svc.from_candidate(candidate, g.user.short_name)
        db.session.commit()
        flash("Создан черновик ИПР с данными из анкеты.", "ok")
        return _build(ipr)
    db.session.commit()
    return redirect(url_for("ipr.candidates"))


# ---------- Рабочий стол и создание ----------

@bp.get("/desk")
@roles(LEARNING, MANAGER, MENTOR)
def desk():
    query = Ipr.query
    if g.user.role == MANAGER:
        query = query.filter_by(manager_id=g.user.id)
    elif g.user.role == MENTOR:
        query = query.filter_by(mentor_id=g.user.id)
    everything = query.order_by(Ipr.id.desc()).all()
    args, rows = request.args, []
    fields = ("department", "upr", "otd", "target", "manager_name", "site", "initiator", "position_holder", "experience")
    applied_from, applied_to = parse_date(args.get("applied_from")), parse_date(args.get("applied_to"))
    meet_ranges = {kind: (parse_date(args.get(f"{kind}_from")), parse_date(args.get(f"{kind}_to"))) for kind in ("first", "m2")}
    for ipr in everything:
        if args.get("q") and args["q"].lower() not in ipr.fio.lower():
            continue
        if args.get("status") and ipr.status != args["status"]:
            continue
        if any(args.get(f) and getattr(ipr, f) != args[f] for f in fields):
            continue
        if applied_from and (not ipr.applied_date or ipr.applied_date < applied_from):
            continue
        if applied_to and (not ipr.applied_date or ipr.applied_date > applied_to):
            continue
        skip = False
        for kind, (start, end) in meet_ranges.items():
            value = svc.meeting_filter_value(ipr, kind)
            if (start and (not value or value < start)) or (end and (not value or value > end)):
                skip = True
        if skip:
            continue
        rows.append({"ipr": ipr, "avg": svc.averages(ipr), "m2": svc.meeting_filter_value(ipr, "m2")})

    def options(field):
        return sorted({getattr(i, field) for i in everything if getattr(i, field)})

    filter_options = {f: options(f) for f in fields}
    return render_template("ipr/desk.html", rows=rows, statuses=IPR_STATUSES, opts=filter_options, total=len(everything),
                           stats={s: sum(i.status == s for i in everything) for s in IPR_STATUSES})


@bp.route("/new", methods=["GET", "POST"])
@roles(LEARNING)
def new():
    if request.method == "POST":
        fio = request.form.get("fio", "").strip()
        if not fio:
            flash("Укажите ФИО резервиста.", "error")
        else:
            ipr = Ipr(fio=fio)
            db.session.add(ipr)
            _apply_data(ipr, request.form)
            db.session.flush()
            audit("Создан черновик ИПР", g.user.short_name, ipr=ipr)
            db.session.commit()
            return _build(ipr)
    return render_template("ipr/new.html", **_people())


def _people() -> dict:
    return {"managers": _users(MANAGER), "mentors": _users(MENTOR), "reservists": _users(RESERVIST)}


def _apply_data(ipr: Ipr, form) -> None:
    for field in ("fio", "position", "department", "upr", "otd", "site", "email", "phone", "experience", "target", "initiator", "position_holder"):
        if field in form:
            setattr(ipr, field, form.get(field, "").strip())
    if form.get("category") in catalog.IPR_CATEGORIES:
        ipr.category = form["category"]
    ipr.applied_date = parse_date(form.get("applied_date")) or ipr.applied_date
    manager = db.session.get(User, form.get("manager_id", type=int) or 0)
    if manager and manager.role == MANAGER:
        ipr.manager, ipr.manager_name, ipr.manager_email = manager, manager.short_name, manager.email
    mentor = db.session.get(User, form.get("mentor_id", type=int) or 0)
    if mentor and mentor.role == MENTOR:
        ipr.mentor, ipr.mentor_name, ipr.mentor_email = mentor, mentor.short_name, mentor.email
    reservist = db.session.get(User, form.get("reservist_id", type=int) or 0)
    if reservist and reservist.role == RESERVIST:
        ipr.reservist = reservist
    start = parse_date(form.get("start_date"))
    if start and start != ipr.start_date:
        db.session.flush()
        svc.set_start_date(ipr, start)


# ---------- Конструктор ИПР ----------

@bp.get("/<int:ipr_id>/build")
@roles(LEARNING)
def build(ipr_id):
    ipr = _ipr(ipr_id)
    return render_template("ipr/build.html", ipr=ipr, errors=svc.approval_errors(ipr), needs_tq=svc.needs_talent_q(ipr), jd=None,
                           selected_hard=len(svc.selected_skills(ipr, "hard")), selected_soft=len(svc.selected_skills(ipr, "soft")), **_people())


@bp.post("/<int:ipr_id>/data")
@roles(LEARNING)
def data(ipr_id):
    ipr = _ipr(ipr_id)
    _apply_data(ipr, request.form)
    audit("Изменены данные о резервисте", g.user.short_name, ipr=ipr)
    db.session.commit()
    flash("Данные сохранены.", "ok")
    return _build(ipr)


@bp.post("/<int:ipr_id>/talentq")
@roles(LEARNING)
def talentq(ipr_id):
    """Черновик можно сохранить в любой момент: без пройденного теста ИПР просто остаётся черновиком."""
    ipr = _ipr(ipr_id)
    ipr.tq_sent = parse_date(request.form.get("tq_sent"))
    ipr.tq_done = parse_date(request.form.get("tq_done"))
    rows = []
    for line in request.form.get("tq_rows", "").splitlines():
        parts = [p.strip() for p in line.split(";")]
        if len(parts) == 3:
            try:
                rows.append([parts[0], float(parts[1].replace(",", ".")), float(parts[2].replace(",", "."))])
            except ValueError:
                flash(f"Строка не распознана: {line}", "error")
    ipr.tq_rows = rows
    audit("Сохранены данные Talent Q", g.user.short_name, ipr=ipr)
    db.session.commit()
    flash("Сохранено." + ("" if ipr.tq_done or not svc.needs_talent_q(ipr) else " Пока теста нет, ИПР остаётся черновиком."), "ok")
    return _build(ipr)


@bp.post("/<int:ipr_id>/skills/parse")
@roles(LEARNING)
def skills_parse(ipr_id):
    ipr = _ipr(ipr_id)
    text = request.form.get("text", "")
    file = request.files.get("file")
    if file and file.filename:
        try:
            text = ai.extract_text(file.filename, file.stream)
        except ValueError as exc:
            flash(str(exc), "error")
            return _build(ipr)
    result = ai.suggest_from_jd(text, ipr.target)
    existing = {s.title for s in ipr.skills}
    added = 0
    for title in result.get("hard_skills", []):
        if title not in existing:
            db.session.add(IprSkill(ipr=ipr, kind="hard", title=title[:300], rationale="Из должностной инструкции"))
            added += 1
    for title in ai.default_soft_skills():
        if title not in existing:
            db.session.add(IprSkill(ipr=ipr, kind="soft", title=title))
            added += 1
    db.session.commit()
    flash(f"В профиль добавлено навыков: {added}. Отметьте те, что берёте в развитие.", "ok")
    return _build(ipr)


@bp.post("/<int:ipr_id>/skills/add")
@roles(LEARNING)
def skill_add(ipr_id):
    ipr = _ipr(ipr_id)
    title = request.form.get("title", "").strip()
    if title:
        db.session.add(IprSkill(ipr=ipr, kind="soft" if request.form.get("kind") == "soft" else "hard", title=title[:300], selected=True))
        db.session.commit()
    return _build(ipr)


@bp.post("/<int:ipr_id>/skills/select")
@roles(LEARNING, MANAGER)
def skills_select(ipr_id):
    ipr = _ipr(ipr_id)
    if g.user.role == MANAGER and ipr.manager_id != g.user.id:
        abort(403)
    chosen = set(request.form.getlist("skill", type=int))
    for skill in ipr.skills:
        skill.selected = skill.id in chosen
    for skill in svc.selected_skills(ipr):
        for author, title in catalog.IPR_BOOKS.get(skill.title, []):
            if not any(b.title == title for b in ipr.books):
                db.session.add(IprBook(ipr=ipr, author=author, title=title, skill=skill.title))
    audit(f"Выбрано навыков для развития: {len(chosen)}", g.user.short_name, ipr=ipr)
    db.session.commit()
    flash("Выбор навыков сохранён, литература подобрана.", "ok")
    return _card(ipr) if g.user.role == MANAGER else _build(ipr)


@bp.post("/<int:ipr_id>/courses/add")
@roles(LEARNING)
def course_add(ipr_id):
    ipr = _ipr(ipr_id)
    title = request.form.get("title", "").strip()
    try:
        url = safe_link(request.form.get("url"))
    except RuleError as exc:
        flash(str(exc), "error")
        return _build(ipr)
    if title and not any(c.title == title for c in ipr.courses):
        db.session.add(IprCourse(ipr=ipr, title=title[:300], url=url))
        db.session.commit()
    return _build(ipr)


@bp.post("/courses/<int:course_id>/delete")
@roles(LEARNING)
def course_delete(course_id):
    course = _child(IprCourse, course_id)
    ipr = course.ipr
    db.session.delete(course)
    db.session.commit()
    return _build(ipr)


@bp.post("/<int:ipr_id>/practices/defaults")
@roles(LEARNING)
def practices_defaults(ipr_id):
    ipr = _ipr(ipr_id)
    svc.add_default_practices(ipr)
    db.session.commit()
    return _build(ipr)


@bp.post("/<int:ipr_id>/books/add")
@roles(LEARNING)
def book_add(ipr_id):
    ipr = _ipr(ipr_id)
    title = request.form.get("title", "").strip()
    if title:
        db.session.add(IprBook(ipr=ipr, title=title[:300], author=request.form.get("author", "").strip()[:200]))
        db.session.commit()
    return _build(ipr)


@bp.post("/<int:ipr_id>/send")
@roles(LEARNING)
def send(ipr_id):
    ipr = _ipr(ipr_id)
    try:
        svc.send_to_approval(ipr, g.user.short_name)
        db.session.commit()
        flash("ИПР отправлен руководителю на согласование.", "ok")
    except RuleError as exc:
        flash(str(exc), "error")
    return _build(ipr)


@bp.post("/<int:ipr_id>/decision")
@roles(MANAGER)
def decision(ipr_id):
    ipr = _ipr(ipr_id)
    if ipr.manager_id != g.user.id or ipr.status != IPR_APPROVAL:
        abort(403)
    choice, comment = request.form.get("decision"), request.form.get("comment", "").strip()
    learning = _users(LEARNING)
    if choice == "ok":
        ipr.status = IPR_WORK
        audit("Руководитель утвердил ИПР", g.user.short_name, ipr=ipr)
        notify([*learning, ipr.reservist, ipr.mentor], "ИПР утверждён", f"{ipr.fio} → {ipr.target}", f"/ipr/{ipr.id}/card")
    elif choice == "return" and comment:
        ipr.status, ipr.return_comment = IPR_RETURNED, comment
        audit(f"Руководитель вернул ИПР на доработку: {comment}", g.user.short_name, ipr=ipr)
        notify(learning, "ИПР возвращён на доработку", f"{ipr.fio}: {comment}", f"/ipr/{ipr.id}/build")
    else:
        flash("Укажите, что нужно поправить.", "error")
        return _card(ipr)
    db.session.commit()
    return _card(ipr)


# ---------- Карточка ИПР ----------

@bp.get("/<int:ipr_id>/card")
@login_required
def card(ipr_id):
    ipr = _ipr(ipr_id)
    return render_template("ipr/card.html", ipr=ipr, avg=svc.averages(ipr), skills=svc.selected_skills(ipr), all_skills=ipr.skills, today=date.today(),
                           can_edit=g.user.role == LEARNING, is_manager=ipr.manager_id == g.user.id, is_mentor=ipr.mentor_id == g.user.id,
                           log=AuditLog.query.filter_by(ipr_id=ipr.id).order_by(AuditLog.ts.desc()).limit(30).all())


def _can_edit_practice(practice: IprPractice) -> bool:
    return g.user.role == LEARNING or g.user.id in (practice.ipr.manager_id, practice.ipr.mentor_id)


@bp.post("/practices/<int:practice_id>/due")
@login_required
def practice_due(practice_id):
    practice = _child(IprPractice, practice_id)
    if not _can_edit_practice(practice):
        abort(403)
    try:
        svc.change_practice_due(practice, parse_date(request.form.get("due_date")), g.user.short_name)
        db.session.commit()
        flash("Срок изменён, участники уведомлены.", "ok")
    except RuleError as exc:
        flash(str(exc), "error")
    return _card(practice.ipr)


@bp.post("/practices/<int:practice_id>/strike")
@login_required
def practice_strike(practice_id):
    practice = _child(IprPractice, practice_id)
    if not _can_edit_practice(practice):
        abort(403)
    try:
        svc.strike_practice(practice, request.form.get("reason"), g.user.short_name)
        db.session.commit()
    except RuleError as exc:
        flash(str(exc), "error")
    return _card(practice.ipr)


@bp.post("/practices/<int:practice_id>/comment")
@login_required
def practice_comment(practice_id):
    practice = _child(IprPractice, practice_id)
    text = request.form.get("text", "").strip()
    if text:
        practice.comments = [*(practice.comments or []), {"who": g.user.short_name, "at": fmt_date(date.today()), "text": text}]
        db.session.commit()
    return redirect(request.referrer or _card(practice.ipr).location)


@bp.post("/practices/<int:practice_id>/progress")
@login_required
def practice_progress(practice_id):
    """Процент и статус ставит резервист; подтверждает руководитель."""
    practice = _child(IprPractice, practice_id)
    ipr = practice.ipr
    if g.user.id != ipr.reservist_id and not _can_edit_practice(practice):
        abort(403)
    if practice.status == "na":
        abort(400)
    pct = request.form.get("pct", type=int)
    if pct is not None:
        practice.pct = max(0, min(100, pct))
    if request.form.get("action") == "done":
        practice.status = "done"
        notify([ipr.manager], "Практическое задание ждёт подтверждения", f"{ipr.fio}: {practice.title[:120]}", f"/ipr/{ipr.id}/card")
    elif practice.status == "plan" and practice.pct > 0:
        practice.status = "work"
    db.session.commit()
    return redirect(request.referrer or _card(ipr).location)


@bp.post("/practices/<int:practice_id>/confirm")
@roles(MANAGER)
def practice_confirm(practice_id):
    practice = _child(IprPractice, practice_id)
    if practice.ipr.manager_id != g.user.id:
        abort(403)
    practice.status, practice.pct = "conf", 100
    audit(f"Руководитель подтвердил задание «{practice.title[:80]}»", g.user.short_name, ipr=practice.ipr)
    db.session.commit()
    return _card(practice.ipr)


@bp.post("/<int:ipr_id>/practices/add")
@login_required
def practice_add(ipr_id):
    ipr = _ipr(ipr_id)
    if g.user.role != LEARNING and g.user.id not in (ipr.manager_id, ipr.mentor_id):
        abort(403)
    title, result, due = request.form.get("title", "").strip(), request.form.get("result", "").strip(), parse_date(request.form.get("due_date"))
    if not title or not due:
        flash("Укажите задание и срок.", "error")
    else:
        replaces = request.form.get("replaces_id", type=int)
        db.session.add(IprPractice(ipr=ipr, title=title, result=result, due_date=due, replaces_id=replaces))
        audit(f"Добавлено практическое задание «{title[:80]}»", g.user.short_name, ipr=ipr)
        notify([ipr.reservist, ipr.mentor], "Новое практическое задание", f"{ipr.fio}: {title[:120]}", f"/ipr/{ipr.id}/card")
        db.session.commit()
    return redirect(request.referrer or _card(ipr).location)


@bp.post("/courses/<int:course_id>/update")
@roles(LEARNING)
def course_update(course_id):
    course = _child(IprCourse, course_id)
    pct = request.form.get("pct", type=int)
    if pct is not None:
        course.pct = max(0, min(100, pct))
    course.assigned = bool(request.form.get("assigned"))
    db.session.commit()
    return _card(course.ipr)


@bp.post("/skills/<int:skill_id>/mastery")
@roles(MANAGER, LEARNING)
def skill_mastery(skill_id):
    skill = _child(IprSkill, skill_id)
    if g.user.role == MANAGER and skill.ipr.manager_id != g.user.id:
        abort(403)
    value = request.form.get("mastery", "")
    if value and value not in catalog.MASTERY:
        abort(400)
    skill.mastery, skill.mastery_note = value, request.form.get("note", "").strip()
    db.session.commit()
    return _card(skill.ipr)


@bp.post("/meetings/<int:meeting_id>")
@roles(LEARNING)
def meeting_update(meeting_id):
    meeting = _child(IprMeeting, meeting_id)
    ipr, moved = meeting.ipr, parse_date(request.form.get("moved_date"))
    status = request.form.get("status", meeting.status)
    if status not in catalog.MEET_STATUS:
        abort(400)
    changed = moved and moved != meeting.moved_date
    meeting.moved_date = moved or meeting.moved_date
    meeting.status, meeting.note = status, request.form.get("note", "").strip()
    title = catalog.IPR_MEET_TITLES[meeting.kind]
    audit(f"{title}: статус «{catalog.MEET_STATUS[status][1]}», дата {fmt_date(meeting.effective_date)}", g.user.short_name, ipr=ipr)
    if changed or status == "set":
        notify([ipr.reservist, ipr.manager], f"{title} по ИПР", f"{ipr.fio}: {fmt_date(meeting.effective_date)}", f"/ipr/{ipr.id}/card")
    if meeting.kind == "fin" and status == "done":
        ipr.status = IPR_DONE
    db.session.commit()
    return _card(ipr)


# ---------- Резервист и состав резерва ----------

@bp.get("/my")
@roles(RESERVIST)
def my():
    ipr = Ipr.query.filter_by(reservist_id=g.user.id).filter(Ipr.status.in_((IPR_WORK, IPR_APPROVED, IPR_DONE))).order_by(Ipr.id.desc()).first()
    if not ipr:
        return render_template("error.html", code="", text="Ваш план развития ещё не утверждён руководителем."), 200
    return render_template("ipr/my.html", ipr=ipr, skills=svc.selected_skills(ipr), avg=svc.averages(ipr), show_tq=bool(ipr.tq_rows))


@bp.get("/top")
@roles(TOP, LEARNING)
def top():
    rows = [{"ipr": i, "avg": svc.averages(i)} for i in Ipr.query.filter(Ipr.status.in_((IPR_WORK, IPR_APPROVED, IPR_DONE))).order_by(Ipr.target).all()]
    target = request.args.get("target", "")
    if target:
        rows = [r for r in rows if r["ipr"].target == target]
    targets = sorted({i.target for i in Ipr.query.all() if i.target})
    return render_template("ipr/top.html", rows=rows, targets=targets, target=target)

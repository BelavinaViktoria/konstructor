"""Модуль ПВД: рабочий стол, конструктор, цели, план новичка, встречи и итоги."""
import json
from datetime import date, datetime

from flask import Blueprint, abort, current_app, flash, g, redirect, render_template, request, url_for

from .. import catalog
from ..extensions import db
from ..models import (
    LEARNING, MANAGER, MENTOR, NEWBIE, PLAN_STATUSES, RECRUITER, ST_APPROVAL, ST_DRAFT, ST_WAIT_GOALS, ST_WORK, AuditLog, Goal, LibraryTask, Plan,
    PlanTask, Position, Signal, SurveyResponse, User,
)
from ..security import can_view_plan, login_required, roles
from ..services import ai, kpi
from ..services import plans as svc
from ..services.dates import fmt_date, parse_date, term_to_date
from ..services.notify import audit, notify
from ..services.risk import plan_risk

bp = Blueprint("pvd", __name__)


def _plan(plan_id: int) -> Plan:
    plan = db.session.get(Plan, plan_id)
    if not plan:
        abort(404)
    if not can_view_plan(g.user, plan):
        abort(403)
    return plan


def _task(task_id: int) -> PlanTask:
    task = db.session.get(PlanTask, task_id)
    if not task:
        abort(404)
    if not can_view_plan(g.user, task.plan):
        abort(403)
    return task


def _goal(goal_id: int) -> Goal:
    goal = db.session.get(Goal, goal_id)
    if not goal:
        abort(404)
    if not can_view_plan(g.user, goal.plan):
        abort(403)
    return goal


def _is_staff() -> bool:
    return g.user.role in (RECRUITER, LEARNING)


def _require_manager(plan: Plan) -> None:
    if plan.manager_id != g.user.id:
        abort(403)


def _require_employee(plan: Plan) -> None:
    if plan.employee_id != g.user.id:
        abort(403)


def _users(role: str) -> list[User]:
    return User.query.filter_by(role=role, active=True).order_by(User.full_name).all()


def _risk_rows(plans: list[Plan]) -> list[dict]:
    today = date.today()
    return [{"plan": p, "risk": plan_risk(p, today), "progress": svc.progress(p), "open_signals": sum(s.status in ("new", "returned") for s in p.signals)} for p in plans]


# ---------- Обзор и рабочий стол ----------

@bp.get("/overview")
@roles(RECRUITER, LEARNING)
def overview():
    counts = {s: Plan.query.filter_by(status=s).count() for s in PLAN_STATUSES}
    open_signals = Signal.query.filter(Signal.status.in_(("new", "returned"))).count()
    return render_template("pvd/overview.html", counts=counts, open_signals=open_signals)


@bp.get("/desk")
@roles(RECRUITER, LEARNING)
def desk():
    args = request.args
    query = Plan.query
    if args.get("q"):
        query = query.filter(Plan.fio.ilike(f"%{args['q']}%"))
    if args.get("status") in PLAN_STATUSES:
        query = query.filter_by(status=args["status"])
    for field in ("position", "site", "manager_name", "department"):
        if args.get(field):
            query = query.filter(getattr(Plan, field) == args[field])
    start, end = parse_date(args.get("hire_from")), parse_date(args.get("hire_to"))
    if start:
        query = query.filter(Plan.hire_date >= start)
    if end:
        query = query.filter(Plan.hire_date <= end)
    if args.get("source") == "potok":
        query = query.filter(Plan.source == "potok", Plan.status == ST_DRAFT)
    plans = query.order_by(Plan.hire_date.desc().nulls_last(), Plan.id.desc()).all()
    rows = _risk_rows(plans)
    if args.get("risk") == "yes":
        rows = [r for r in rows if r["risk"]["level"] in ("Средний", "Высокий")]
    if args.get("over") == "yes":
        rows = [r for r in rows if r["risk"]["facts"].overdue > 0]
    everything = Plan.query.all()
    stats = {s: sum(p.status == s for p in everything) for s in PLAN_STATUSES}
    stats["potok"] = sum(p.source == "potok" and p.status == ST_DRAFT for p in everything)

    def options(field):
        return sorted({getattr(p, field) for p in everything if getattr(p, field)})

    return render_template("pvd/desk.html", rows=rows, stats=stats, statuses=PLAN_STATUSES, positions=options("position"), sites=options("site"),
                           managers=options("manager_name"), departments=options("department"))


@bp.get("/manager")
@roles(MANAGER)
def manager_home():
    plans = Plan.query.filter_by(manager_id=g.user.id).order_by(Plan.hire_date.desc().nulls_last()).all()
    return render_template("pvd/people.html", title="Мои новички", rows=_risk_rows(plans), intro="Постановка целей, согласование планов и проверка задач.")


@bp.get("/mentor")
@roles(MENTOR)
def mentor_home():
    plans = Plan.query.filter_by(mentor_id=g.user.id).filter(Plan.status == ST_WORK).all()
    return render_template("pvd/people.html", title="Наставник новичка", rows=_risk_rows(plans), intro="Ваши подопечные, их прогресс и сигналы. План и подтверждение задач остаются за руководителем.")


@bp.get("/my-plan")
@roles(NEWBIE)
def my_plan():
    plan = Plan.query.filter_by(employee_id=g.user.id, status=ST_WORK).first() or Plan.query.filter_by(employee_id=g.user.id).order_by(Plan.id.desc()).first()
    if not plan:
        return render_template("error.html", code="", text="Для вас пока нет плана ввода в должность."), 200
    return redirect(url_for("pvd.plan_view", plan_id=plan.id))


# ---------- Создание и блок 1 ----------

def _apply_block1(plan: Plan, form) -> None:
    for field in ("fio", "position", "department", "upr", "otd", "site"):
        setattr(plan, field, form.get(field, "").strip())
    position = Position.query.filter_by(name=plan.position).first()
    plan.family = position.family if position else plan.family
    svc.set_hire_date(plan, parse_date(form.get("hire_date")))
    manager = db.session.get(User, form.get("manager_id", type=int) or 0)
    if manager and manager.role == MANAGER:
        plan.manager, plan.manager_name, plan.manager_email = manager, manager.short_name, manager.email
    employee = db.session.get(User, form.get("employee_id", type=int) or 0)
    if employee and employee.role == NEWBIE:
        plan.employee = employee


def _block1_context() -> dict:
    return {"managers": _users(MANAGER), "newbies": _users(NEWBIE), "positions": Position.query.order_by(Position.name).all()}


@bp.route("/plans/new", methods=["GET", "POST"])
@roles(RECRUITER, LEARNING)
def plan_new():
    if request.method == "POST":
        if not request.form.get("fio", "").strip():
            flash("Укажите ФИО сотрудника.", "error")
        else:
            plan = Plan(fio=request.form["fio"].strip(), recruiter=g.user if g.user.role == RECRUITER else None)
            db.session.add(plan)
            _apply_block1(plan, request.form)
            db.session.flush()
            audit("ПВД создан, статус «Черновик»", g.user.short_name, plan, kind="rec")
            _send_preboarding(plan)
            db.session.commit()
            flash("ПВД создан.", "ok")
            return redirect(url_for("pvd.build", plan_id=plan.id))
    return render_template("pvd/plan_form.html", plan=None, **_block1_context())


def _send_preboarding(plan: Plan) -> None:
    """Письмо о выходе нового сотрудника уходит один раз, как только блок 1 заполнен."""
    if plan.manager and plan.hire_date and plan.position:
        subject, body = svc.preboarding_letter(plan)
        if notify([plan.manager], subject, body, f"/plans/{plan.id}/goals", f"preboarding:{plan.id}"):
            audit("Руководителю отправлено письмо о выходе нового сотрудника", "Система", plan, kind="sys")


@bp.post("/plans/<int:plan_id>/block1")
@roles(RECRUITER, LEARNING)
def plan_block1(plan_id):
    plan = _plan(plan_id)
    _apply_block1(plan, request.form)
    if not plan.recruiter and g.user.role == RECRUITER:
        plan.recruiter = g.user
    _send_preboarding(plan)
    audit("Изменены данные о сотруднике", g.user.short_name, plan, kind="rec")
    db.session.commit()
    flash("Данные сохранены.", "ok")
    return redirect(url_for("pvd.build", plan_id=plan.id))


# ---------- Конструктор ----------

@bp.get("/plans/<int:plan_id>/build")
@roles(RECRUITER, LEARNING)
def build(plan_id):
    plan = _plan(plan_id)
    library = LibraryTask.query.filter_by(family=plan.family).all() if plan.family else []
    used = {t.title for t in plan.tasks}
    return render_template("pvd/build.html", plan=plan, library=library, used=used, groups=svc.ordered_tasks_by_group(plan),
                           errors=svc.can_send_to_approval(plan), risk=plan_risk(plan),
                           log=AuditLog.query.filter_by(plan_id=plan.id).order_by(AuditLog.ts.desc()).limit(30).all(), **_block1_context())


@bp.post("/plans/<int:plan_id>/std/<block>")
@roles(RECRUITER, LEARNING)
def add_std(plan_id, block):
    plan = _plan(plan_id)
    blocks = catalog.STD_BLOCKS if block == "all" else {block: ""} if block in catalog.STD_BLOCKS else {}
    if not blocks:
        abort(404)
    added = sum(svc.add_std_block(plan, b) for b in blocks)
    audit(f"Добавлены общие блоки: задач {added}", g.user.short_name, plan, kind="rec")
    db.session.commit()
    flash(f"Добавлено задач: {added}" if added else "Эти задачи уже в плане.", "ok")
    return redirect(url_for("pvd.build", plan_id=plan.id))


@bp.post("/plans/<int:plan_id>/library/<int:lib_id>")
@roles(RECRUITER, LEARNING)
def add_library(plan_id, lib_id):
    plan, item = _plan(plan_id), db.session.get(LibraryTask, lib_id)
    if not item:
        abort(404)
    if any(t.title == item.title for t in plan.tasks):
        flash("Эта задача уже в плане.", "error")
    else:
        svc.add_task(plan, item.title, "ind", item.description, item.fmt, item.term, item.responsible, item.algorithm)
        audit(f"Добавлена задача «{item.title}»", g.user.short_name, plan, kind="rec")
        db.session.commit()
    return redirect(url_for("pvd.build", plan_id=plan.id))


def _task_from_form(task: PlanTask, form, plan: Plan) -> list[str]:
    errors = []
    title = form.get("title", "").strip()
    algorithm = [line.strip() for line in form.get("algorithm", "").splitlines() if line.strip()]
    if not title:
        errors.append("Укажите название задачи")
    if not algorithm:
        errors.append("У задачи обязательно должен быть алгоритм выполнения")
    if errors:
        return errors
    task.title, task.description, task.fmt = title, form.get("description", "").strip(), form.get("fmt", "").strip()
    task.responsible, task.algorithm = form.get("responsible", "").strip(), algorithm
    due, term = parse_date(form.get("due_date")), form.get("term", "")
    task.term = term
    task.due_date = due or term_to_date(term, plan.hire_date) or task.due_date
    return []


def _can_edit_task(task: PlanTask) -> bool:
    return _is_staff() or (task.plan.manager_id == g.user.id and (task.source in ("jd", "kpi") or task.group == "mgr"))


def _task_back(task: PlanTask) -> str:
    return url_for("pvd.build", plan_id=task.plan_id) if _is_staff() else url_for("pvd.goals", plan_id=task.plan_id)


@bp.route("/plans/<int:plan_id>/tasks/new", methods=["GET", "POST"])
@roles(RECRUITER, LEARNING, MANAGER)
def task_new(plan_id):
    plan = _plan(plan_id)
    if g.user.role == MANAGER:
        _require_manager(plan)
    group = "mgr" if g.user.role == MANAGER else "extra"
    task = PlanTask(group=group, source="manual", position=svc.next_position(plan), algorithm=[])
    if request.method == "POST":
        errors = _task_from_form(task, request.form, plan)
        if not errors:
            task.plan = plan
            audit(f"Добавлена задача «{task.title}»", g.user.short_name, plan)
            db.session.commit()
            return redirect(_task_back(task))
        for error in errors:
            flash(error, "error")
    return render_template("pvd/task_form.html", plan=plan, task=task, back=url_for("pvd.build" if _is_staff() else "pvd.goals", plan_id=plan.id))


@bp.route("/tasks/<int:task_id>/edit", methods=["GET", "POST"])
@login_required
def task_edit(task_id):
    task = _task(task_id)
    if not _can_edit_task(task):
        abort(403)
    if request.method == "POST":
        errors = _task_from_form(task, request.form, task.plan)
        if not errors:
            audit(f"Изменена задача «{task.title}»", g.user.short_name, task.plan)
            db.session.commit()
            flash("Задача сохранена.", "ok")
            return redirect(_task_back(task))
        for error in errors:
            flash(error, "error")
    return render_template("pvd/task_form.html", plan=task.plan, task=task, back=_task_back(task))


@bp.post("/tasks/<int:task_id>/delete")
@login_required
def task_delete(task_id):
    task = _task(task_id)
    if not _can_edit_task(task):
        abort(403)
    back, plan, title = _task_back(task), task.plan, task.title
    audit(f"Удалена задача «{title}»", g.user.short_name, plan)
    db.session.delete(task)
    db.session.commit()
    flash(f"Задача «{title}» удалена.", "ok")
    return redirect(back)


@bp.post("/plans/<int:plan_id>/request-goals")
@roles(RECRUITER, LEARNING)
def request_goals(plan_id):
    plan = _plan(plan_id)
    if not plan.manager:
        flash("Сначала укажите руководителя в блоке 1.", "error")
    elif plan.status not in (ST_DRAFT, ST_WAIT_GOALS):
        flash("Запросить цели можно только для черновика.", "error")
    else:
        svc.set_status(plan, ST_WAIT_GOALS)
        notify([plan.manager], "Постановка целей и карта KPI на испытательный срок",
               f"Назначьте наставника и поставьте от {catalog.GOAL_MIN} до {catalog.GOAL_MAX} целей: {plan.fio}, {plan.position}.", f"/plans/{plan.id}/goals")
        audit("Запрошены цели у руководителя", g.user.short_name, plan, kind="rec")
        db.session.commit()
        flash("Запрос целей отправлен руководителю.", "ok")
    return redirect(url_for("pvd.build", plan_id=plan.id))


@bp.post("/plans/<int:plan_id>/send-approval")
@roles(RECRUITER, LEARNING)
def send_approval(plan_id):
    plan = _plan(plan_id)
    errors = svc.can_send_to_approval(plan)
    if plan.status not in (ST_DRAFT, ST_WAIT_GOALS):
        errors.append("План уже отправлен")
    if errors:
        for error in errors:
            flash(error, "error")
    else:
        svc.set_status(plan, ST_APPROVAL)
        plan.return_comment = ""
        notify([plan.manager], "План на согласование", f"{plan.fio}, {plan.position}", f"/plans/{plan.id}")
        audit("ПВД отправлен на согласование", g.user.short_name, plan, kind="rec")
        db.session.commit()
        flash("План отправлен на согласование.", "ok")
    return redirect(url_for("pvd.build", plan_id=plan.id))


# ---------- Цели ----------

@bp.get("/plans/<int:plan_id>/goals")
@login_required
def goals(plan_id):
    plan = _plan(plan_id)
    if g.user.role in (NEWBIE, MENTOR):
        abort(403)
    return _render_goals(plan)


def _render_goals(plan: Plan, jd_result: dict | None = None):
    draft, mismatch = plan.kpi_draft, False
    if draft and draft.get("fio") and plan.fio:
        mismatch = plan.fio.split()[0].lower() not in draft["fio"].lower()
    return render_template("pvd/goals.html", plan=plan, editable=plan.manager_id == g.user.id, mentors=_users(MENTOR), draft=draft, mismatch=mismatch,
                           jd_result=jd_result, constructor=svc.constructor_tasks(plan), errors=svc.validate_goals(plan))


def _save_goal_fields(plan: Plan, form) -> None:
    for goal in plan.goals:
        if f"title_{goal.id}" not in form:
            continue
        goal.title = form.get(f"title_{goal.id}", "").strip()
        goal.description = form.get(f"description_{goal.id}", "").strip()
        goal.result = form.get(f"result_{goal.id}", "").strip()
        goal.due_date = parse_date(form.get(f"due_{goal.id}"))
    mentor = db.session.get(User, form.get("mentor_id", type=int) or 0)
    if mentor and mentor.role == MENTOR:
        plan.mentor, plan.mentor_name, plan.mentor_email = mentor, mentor.short_name, form.get("mentor_email", "").strip() or mentor.email


@bp.post("/plans/<int:plan_id>/goals/save")
@roles(MANAGER)
def goals_save(plan_id):
    plan = _plan(plan_id)
    _require_manager(plan)
    _save_goal_fields(plan, request.form)
    action = request.form.get("action", "draft")
    if action == "add":
        if len(plan.goals) >= catalog.GOAL_MAX:
            flash(f"Не больше {catalog.GOAL_MAX} целей.", "error")
        else:
            db.session.add(Goal(plan=plan))
    elif action == "write":
        errors = svc.validate_goals(plan)
        if not plan.mentor:
            errors.append("Назначьте наставника")
        if errors:
            db.session.commit()
            for error in errors:
                flash(error, "error")
            return redirect(url_for("pvd.goals", plan_id=plan.id))
        first_write = not plan.goals_written_at
        plan.goals_written_at = datetime.now()
        audit(f"Записал цели: {len(plan.goals)}, наставник {plan.mentor.short_name}", g.user.short_name, plan, kind="mgr")
        notify([plan.recruiter], "Цели записаны", f"{plan.fio}: целей {len(plan.goals)}, наставник {plan.mentor.short_name}", f"/plans/{plan.id}/build")
        notify([plan.mentor], "Вы назначены наставником", f"{plan.fio}, {plan.position}. Выход {fmt_date(plan.hire_date)}.", "/mentor", f"mentor:{plan.id}:{plan.mentor_id}")
        if plan.status == ST_WORK and not first_write:
            notify([plan.employee, plan.mentor, plan.recruiter], "Руководитель изменил план ввода в должность", f"{plan.fio}: изменены цели", f"/plans/{plan.id}")
        flash("Цели записаны. Менеджер по подбору уведомлён.", "ok")
    db.session.commit()
    return redirect(url_for("pvd.goals", plan_id=plan.id))


@bp.post("/goals/<int:goal_id>/delete")
@roles(MANAGER)
def goal_delete(goal_id):
    goal = _goal(goal_id)
    _require_manager(goal.plan)
    plan = goal.plan
    if len(plan.goals) <= catalog.GOAL_MIN:
        flash(f"Должно остаться не меньше {catalog.GOAL_MIN} целей: сначала добавьте другую.", "error")
    else:
        audit(f"Удалил цель «{goal.title}»", g.user.short_name, plan, kind="mgr")
        db.session.delete(goal)
        db.session.commit()
    return redirect(url_for("pvd.goals", plan_id=plan.id))


@bp.post("/goals/<int:goal_id>/to-task")
@roles(MANAGER)
def goal_to_task(goal_id):
    goal = _goal(goal_id)
    _require_manager(goal.plan)
    plan = goal.plan
    try:
        svc.goal_to_task(goal, g.user.short_name)
        db.session.commit()
        flash("Цель перенесена в «Конструктор задач» и в блок «Индивидуальные задачи в должности».", "ok")
    except svc.RuleError as exc:
        flash(str(exc), "error")
    return redirect(url_for("pvd.goals", plan_id=plan.id) + "#constructor")


@bp.post("/plans/<int:plan_id>/kpi/upload")
@roles(MANAGER)
def kpi_upload(plan_id):
    plan = _plan(plan_id)
    _require_manager(plan)
    file = request.files.get("file")
    if not file or not file.filename.lower().endswith(".xlsx"):
        flash("Нужен файл Excel в формате .xlsx.", "error")
        return redirect(url_for("pvd.goals", plan_id=plan.id))
    try:
        sheets = kpi.read_sheets(file.stream)
    except Exception:  # openpyxl бросает разные ошибки на повреждённых файлах
        flash("Не удалось прочитать файл: проверьте, что это карта KPI в формате .xlsx.", "error")
        return redirect(url_for("pvd.goals", plan_id=plan.id))
    return _store_kpi(plan, sheets, file.filename)


@bp.post("/plans/<int:plan_id>/kpi/demo")
@roles(MANAGER)
def kpi_demo(plan_id):
    if current_app.config["APP_ENV"] == "production":
        abort(404)
    plan = _plan(plan_id)
    _require_manager(plan)
    from ..seed import KPI_SAMPLE

    return _store_kpi(plan, [KPI_SAMPLE], "Пример карты KPI.xlsx")


def _store_kpi(plan: Plan, sheets: list, filename: str):
    result = kpi.parse_workbook(sheets, plan.hire_date)
    if not result:
        flash("В файле не найдена таблица показателей KPI.", "error")
    else:
        result["file"] = filename
        plan.kpi_draft = result
        audit(f"Загружена карта KPI «{filename}»: найдено пунктов {len(result['items'])}", g.user.short_name, plan, kind="mgr")
        db.session.commit()
        flash(f"Из карты извлечено пунктов: {len(result['items'])}. Отметьте нужные и добавьте в цели.", "ok")
    return redirect(url_for("pvd.goals", plan_id=plan.id))


@bp.post("/plans/<int:plan_id>/kpi/add")
@roles(MANAGER)
def kpi_add(plan_id):
    plan = _plan(plan_id)
    _require_manager(plan)
    draft = plan.kpi_draft or {}
    items = draft.get("items", [])
    chosen = [i for i in request.form.getlist("item", type=int) if 0 <= i < len(items)]
    if not chosen:
        flash("Отметьте хотя бы один пункт.", "error")
        return redirect(url_for("pvd.goals", plan_id=plan.id))
    free, added = catalog.GOAL_MAX - len(plan.goals), 0
    for i in chosen[:max(free, 0)]:
        item = items[i]
        due = parse_date(request.form.get(f"due_{i}")) or parse_date(item["due"])
        if plan.probation_end and due and not plan.hire_date <= due <= plan.probation_end:
            flash(f"«{item['title']}»: срок вне испытательного срока, поставлен срок из карты.", "error")
            due = parse_date(item["due"])
        db.session.add(Goal(plan=plan, title=item["title"], description=item["criterion"], result=item["criterion"], due_date=due, origin="kpi",
                            source_note=f"Извлечено из карты KPI «{draft.get('file', '')}»: {item['source']}",
                            meta={k: item[k] for k in ("criterion", "fmt", "responsible", "algorithm", "source")}))
        added += 1
    plan.kpi_draft = None
    if len(chosen) > added:
        flash(f"Добавлено целей: {added}. Остальные не поместились: в блоке не больше {catalog.GOAL_MAX} целей.", "error")
    else:
        flash(f"Добавлено целей: {added}.", "ok")
    audit(f"Из карты KPI добавлено целей: {added}", g.user.short_name, plan, kind="mgr")
    db.session.commit()
    return redirect(url_for("pvd.goals", plan_id=plan.id))


@bp.post("/plans/<int:plan_id>/kpi/reset")
@roles(MANAGER)
def kpi_reset(plan_id):
    plan = _plan(plan_id)
    _require_manager(plan)
    plan.kpi_draft = None
    db.session.commit()
    return redirect(url_for("pvd.goals", plan_id=plan.id))


@bp.post("/plans/<int:plan_id>/jd/parse")
@roles(MANAGER)
def jd_parse(plan_id):
    plan = _plan(plan_id)
    _require_manager(plan)
    text = request.form.get("text", "")
    file = request.files.get("file")
    if file and file.filename:
        try:
            text = ai.extract_text(file.filename, file.stream)
        except ValueError as exc:
            flash(str(exc), "error")
            return redirect(url_for("pvd.goals", plan_id=plan.id))
    if not text.strip():
        flash("Загрузите файл или вставьте текст инструкции.", "error")
        return redirect(url_for("pvd.goals", plan_id=plan.id))
    result = ai.suggest_from_jd(text, plan.position)
    if not result["goals"] and not result["tasks"]:
        flash("В тексте не найдены обязанности. Проверьте, что это должностная инструкция с перечнем обязанностей.", "error")
        return redirect(url_for("pvd.goals", plan_id=plan.id))
    return _render_goals(plan, jd_result=result)


def _json_items(values: list[str], keys: tuple) -> list[dict]:
    """Черновики возвращаются из формы: берём только известные поля."""
    out = []
    for raw in values:
        try:
            item = json.loads(raw)
        except ValueError:
            continue
        if isinstance(item, dict) and all(isinstance(item.get(k), (str, list)) for k in keys):
            out.append({k: item[k] for k in keys})
    return out


@bp.post("/plans/<int:plan_id>/jd/apply")
@roles(MANAGER)
def jd_apply(plan_id):
    plan = _plan(plan_id)
    _require_manager(plan)
    goals_in = _json_items(request.form.getlist("goal"), ("title", "description", "result"))
    tasks_in = _json_items(request.form.getlist("task"), ("title", "description", "algorithm"))
    free = max(catalog.GOAL_MAX - len(plan.goals), 0)
    dues = ai.goal_due_dates(plan.hire_date, free)
    for item, due in zip(goals_in[:free], dues):
        db.session.add(Goal(plan=plan, title=item["title"][:400], description=item["description"], result=item["result"], due_date=due, origin="jd",
                            source_note="Черновик подготовлен ИИ по должностной инструкции"))
    for item in tasks_in:
        steps = [str(x)[:500] for x in item["algorithm"]] if isinstance(item["algorithm"], list) else []
        svc.add_task(plan, item["title"][:300], "jd", item["description"], "Практика", "Первый месяц", "Наставник", steps or catalog.ALG_DEFAULT, "jd", "ИИ · должностная инструкция")
    audit(f"Добавлено из должностной инструкции: целей {min(len(goals_in), free)}, задач {len(tasks_in)}", g.user.short_name, plan, kind="mgr")
    db.session.commit()
    if len(goals_in) > free:
        flash(f"Часть целей не добавлена: в блоке не больше {catalog.GOAL_MAX} целей.", "error")
    flash("Выбранное добавлено в план.", "ok")
    return redirect(url_for("pvd.goals", plan_id=plan.id))


# ---------- Согласование ----------

@bp.post("/plans/<int:plan_id>/approve")
@roles(MANAGER)
def approve(plan_id):
    plan = _plan(plan_id)
    _require_manager(plan)
    if plan.status != ST_APPROVAL:
        flash("План не на согласовании.", "error")
    else:
        svc.set_status(plan, ST_WORK)
        audit("Согласовал ПВД и отправил сотруднику", g.user.short_name, plan, kind="mgr")
        notify([plan.recruiter], "План согласован", plan.fio, f"/plans/{plan.id}")
        notify([plan.mentor], "План подопечного согласован", plan.fio, f"/plans/{plan.id}")
        notify([plan.employee], "Ваш план ввода в должность", "Ознакомьтесь с планом и подтвердите.", f"/plans/{plan.id}")
        db.session.commit()
        flash("План согласован и отправлен новичку.", "ok")
    return redirect(url_for("pvd.plan_view", plan_id=plan.id))


@bp.post("/plans/<int:plan_id>/return")
@roles(MANAGER)
def return_plan(plan_id):
    plan = _plan(plan_id)
    _require_manager(plan)
    comment = request.form.get("comment", "").strip()
    if plan.status != ST_APPROVAL or not comment:
        flash("Укажите, что нужно поправить.", "error")
    else:
        plan.return_comment = comment
        svc.set_status(plan, ST_DRAFT)
        audit(f"Вернул ПВД на доработку: {comment}", g.user.short_name, plan, kind="mgr")
        notify([plan.recruiter], "План возвращён", f"{plan.fio}: {comment}", f"/plans/{plan.id}/build")
        db.session.commit()
        flash("План возвращён менеджеру по подбору.", "ok")
    return redirect(url_for("pvd.plan_view", plan_id=plan.id))


# ---------- План новичка ----------

@bp.get("/plans/<int:plan_id>")
@login_required
def plan_view(plan_id):
    plan = _plan(plan_id)
    today = date.today()
    statuses = {t.id: svc.task_view_status(t, today) for t in plan.tasks}
    counts = {key: sum(v == key for v in statuses.values()) for key in catalog.TASK_STATUS}
    answered = {s.survey_key for s in plan.surveys}
    return render_template("pvd/plan.html", plan=plan, groups=svc.ordered_tasks_by_group(plan), statuses=statuses, counts=counts, progress=svc.progress(plan),
                           answered=answered, risk=plan_risk(plan, today), is_employee=plan.employee_id == g.user.id, is_manager=plan.manager_id == g.user.id,
                           is_staff=_is_staff(), log=AuditLog.query.filter_by(plan_id=plan.id).order_by(AuditLog.ts.desc()).limit(40).all())


@bp.post("/plans/<int:plan_id>/ack")
@roles(NEWBIE)
def ack(plan_id):
    plan = _plan(plan_id)
    _require_employee(plan)
    plan.acknowledged_at = datetime.now()
    audit("Ознакомился с планом: «Ознакомлен и согласен»", g.user.short_name, plan, kind="emp")
    notify([plan.recruiter, plan.manager], "Сотрудник ознакомился", plan.fio, f"/plans/{plan.id}")
    db.session.commit()
    return redirect(url_for("pvd.plan_view", plan_id=plan.id))


@bp.post("/plans/<int:plan_id>/questions")
@roles(NEWBIE)
def questions(plan_id):
    plan = _plan(plan_id)
    _require_employee(plan)
    text = request.form.get("text", "").strip()
    if text:
        plan.newbie_questions = text
        audit(f"Задал вопросы по плану: {text}", g.user.short_name, plan, kind="emp")
        notify([plan.manager, plan.mentor], "Есть вопросы по плану", f"{plan.fio}: {text}", f"/plans/{plan.id}")
        db.session.commit()
        flash("Вопросы отправлены руководителю и наставнику.", "ok")
    return redirect(url_for("pvd.plan_view", plan_id=plan.id))


def _report(item, plan: Plan, kind_label: str, item_key: str) -> None:
    """Общий отчёт новичка по задаче или цели: выполнено либо нужна помощь."""
    action, comment = request.form.get("action"), request.form.get("comment", "").strip()
    if action == "help":
        if len(comment) < 5:
            raise svc.RuleError("Опишите, что мешает выполнить")
        item.status, item.report_comment, item.reported_at = "blocked", comment, datetime.now()
        contact = plan.employee.email if plan.employee else ""
        svc.create_signal(
            plan, "help", "высокий", f"Нужна помощь по {kind_label}: «{item.title}»",
            "Сотрудник сам сообщил, что не может выполнить работу, и объяснил почему. Это самый прямой сигнал: человек просит о помощи, а не молчит.",
            [[f"«{item.title}», срок {fmt_date(item.due_date)}", comment, "neg"]],
            f"Свяжитесь с сотрудником в течение рабочего дня, разберитесь, что мешает, и снимите препятствие или перенесите срок. Контакт: {contact}.", repkey=item_key)
        audit(f"Нажал «Нужна помощь» по {kind_label} «{item.title}»: {comment}", g.user.short_name, plan, kind="emp")
        notify(svc.signal_recipients(plan), "Сотруднику нужна помощь: свяжитесь с ним", f"{plan.fio}: «{item.title}». {comment}", f"/signals?plan={plan.id}")
    elif action == "done":
        item.status, item.report_comment, item.reported_at = "wait", comment, datetime.now()
        item.report_link = svc.safe_link(request.form.get("link"))
        audit(f"Отметил выполнение: «{item.title}»", g.user.short_name, plan, kind="emp")
        notify([plan.manager], f"Проверьте выполнение: {kind_label}", f"{plan.fio}: «{item.title}»", f"/plans/{plan.id}")
    else:
        abort(400)


@bp.post("/tasks/<int:task_id>/report")
@roles(NEWBIE)
def task_report(task_id):
    task = _task(task_id)
    _require_employee(task.plan)
    if task.group in ("pulse", "meet"):
        abort(400)
    try:
        _report(task, task.plan, "задаче", f"t:{task.id}")
        db.session.commit()
        flash("Отчёт отправлен.", "ok")
    except svc.RuleError as exc:
        db.session.rollback()
        flash(str(exc), "error")
    return redirect(url_for("pvd.plan_view", plan_id=task.plan_id))


@bp.post("/goals/<int:goal_id>/report")
@roles(NEWBIE)
def goal_report(goal_id):
    goal = _goal(goal_id)
    _require_employee(goal.plan)
    try:
        _report(goal, goal.plan, "цели", f"g:{goal.id}")
        db.session.commit()
        flash("Отчёт отправлен.", "ok")
    except svc.RuleError as exc:
        db.session.rollback()
        flash(str(exc), "error")
    return redirect(url_for("pvd.plan_view", plan_id=goal.plan_id))


@bp.post("/tasks/<int:task_id>/review")
@roles(MANAGER)
def task_review(task_id):
    task = _task(task_id)
    _require_manager(task.plan)
    decision, comment = request.form.get("decision"), request.form.get("comment", "").strip()
    if decision == "ok":
        task.status, task.review_comment = "done", comment
        audit(f"Подтвердил задачу «{task.title}»", g.user.short_name, task.plan, kind="mgr")
    elif decision == "return":
        if not comment:
            flash("Напишите, что доработать.", "error")
            return redirect(url_for("pvd.plan_view", plan_id=task.plan_id))
        task.status, task.review_comment = "plan", comment
        task.returned_count += 1
        audit(f"Вернул задачу «{task.title}» на доработку: {comment}", g.user.short_name, task.plan, kind="mgr")
        notify([task.plan.employee], "Задача возвращена", f"«{task.title}»: {comment}", f"/plans/{task.plan_id}")
    else:
        abort(400)
    db.session.commit()
    return redirect(url_for("pvd.plan_view", plan_id=task.plan_id))


@bp.post("/goals/<int:goal_id>/review")
@roles(MANAGER)
def goal_review(goal_id):
    goal = _goal(goal_id)
    _require_manager(goal.plan)
    decision = request.form.get("decision")
    if decision not in ("done", "partial", "fail", "plan"):
        abort(400)
    goal.status, goal.review_comment = decision, request.form.get("comment", "").strip()
    audit(f"Поставил итог цели «{goal.title}»: {catalog.GOAL_STATUS[decision][1]}", g.user.short_name, goal.plan, kind="mgr")
    if decision == "plan":
        notify([goal.plan.employee], "Цель возвращена", f"«{goal.title}»: {goal.review_comment}", f"/plans/{goal.plan_id}")
    db.session.commit()
    return redirect(url_for("pvd.plan_view", plan_id=goal.plan_id))


# ---------- Опросы, встреча через месяц, итоги ----------

def _collect_answers(survey: dict, form) -> dict:
    answers = {}
    for i, q in enumerate(survey["q"]):
        if q["ty"] == "intro":
            continue
        name = f"q{i}"
        answers[q["t"]] = "; ".join(form.getlist(name)) if q["ty"] == "many" else form.get(name, "").strip()
    return answers


def _survey_view(plan: Plan, key: str, endpoint_back: str):
    survey = catalog.SURVEYS[key]
    existing = SurveyResponse.query.filter_by(plan_id=plan.id, survey_key=key).first()
    if request.method == "POST":
        try:
            svc.submit_survey(plan, key, _collect_answers(survey, request.form), g.user.short_name)
            if key == "m1":
                plan.m1_done_at = datetime.now()
                for task in plan.tasks:
                    if task.group == "meet" and task.term == "Первый месяц":
                        task.status = "done"
            db.session.commit()
            flash("Ответы сохранены.", "ok")
            return redirect(url_for(endpoint_back, plan_id=plan.id))
        except svc.RuleError as exc:
            flash(str(exc), "error")
    intro = next((q["t"] for q in survey["q"] if q["ty"] == "intro"), "").replace("{{Компания}}", current_app.config["COMPANY_NAME"])
    return render_template("pvd/survey.html", plan=plan, key=key, survey=survey, intro=intro, existing=existing)


@bp.route("/plans/<int:plan_id>/survey/<key>", methods=["GET", "POST"])
@login_required
def survey(plan_id, key):
    plan = _plan(plan_id)
    if key not in ("day1", "week1"):
        abort(404)
    if request.method == "POST":
        _require_employee(plan)
    return _survey_view(plan, key, "pvd.plan_view")


@bp.route("/plans/<int:plan_id>/m1", methods=["GET", "POST"])
@roles(RECRUITER, LEARNING)
def m1(plan_id):
    return _survey_view(_plan(plan_id), "m1", "pvd.plan_view")


@bp.route("/plans/<int:plan_id>/final", methods=["GET", "POST"])
@roles(RECRUITER, LEARNING)
def final(plan_id):
    plan = _plan(plan_id)
    if request.method == "POST":
        verdict = request.form.get("verdict")
        if verdict not in ("ok", "no"):
            flash("Выберите итог: испытательный срок пройден или не пройден.", "error")
        else:
            try:
                svc.finish_plan(plan, verdict == "ok", parse_date(request.form.get("dismissal_date")), request.form.get("initiator", ""),
                                request.form.get("reason", ""), request.form.get("comment", ""), g.user.short_name)
                db.session.commit()
                flash(f"Итог зафиксирован: {plan.status}.", "ok")
                return redirect(url_for("pvd.plan_view", plan_id=plan.id))
            except svc.RuleError as exc:
                flash(str(exc), "error")
    return render_template("pvd/final.html", plan=plan, progress=svc.progress(plan), statuses={t.id: svc.task_view_status(t) for t in plan.tasks})

"""Бизнес-правила ПВД: статусы, цели, задачи, опросы, итоги."""
from datetime import date, datetime
from urllib.parse import urlparse

from ..catalog import COMMON, GOAL_MAX, GOAL_MIN, PREBOARDING_BODY, PREBOARDING_SUBJECT, PROBATION_MONTHS, SURVEYS
from ..extensions import db
from ..models import LEARNING, ST_FAILED, ST_PASSED, Goal, Plan, PlanTask, Signal, SurveyResponse, User
from . import tone as tone_service
from .dates import add_months, add_workdays, fmt_date, prev_workday, term_to_date
from .notify import audit, get_setting, notify


class RuleError(ValueError):
    """Нарушено правило процесса; текст показывается пользователю."""


def safe_link(value: str) -> str:
    """Принимает только http(s)-ссылки, чтобы javascript: не попал в страницу."""
    value = (value or "").strip()
    if not value:
        return ""
    if urlparse(value).scheme not in ("http", "https"):
        raise RuleError("Ссылка должна начинаться с http:// или https://")
    return value


def set_status(plan: Plan, status: str) -> None:
    plan.status = status
    plan.status_changed_at = datetime.now()


def set_hire_date(plan: Plan, hire: date | None) -> None:
    plan.hire_date = hire
    plan.probation_end = prev_workday(add_months(hire, PROBATION_MONTHS)) if hire else None
    for task in plan.tasks:
        if task.term:
            task.due_date = term_to_date(task.term, hire)


def next_position(plan: Plan) -> int:
    return max((t.position or 0 for t in plan.tasks), default=0) + 1


def add_task(plan: Plan, title: str, group: str, description: str = "", fmt: str = "Документ", term: str = "", responsible: str = "",
             algorithm: list | None = None, source: str = "library", source_note: str = "", due_date: date | None = None) -> PlanTask:
    task = PlanTask(plan=plan, title=title, group=group, description=description, fmt=fmt, term=term, responsible=responsible,
                    algorithm=list(algorithm or []), source=source, source_note=source_note, position=next_position(plan),
                    due_date=due_date or term_to_date(term, plan.hire_date))
    db.session.add(task)
    return task


def add_std_block(plan: Plan, block: str) -> int:
    existing = {t.title for t in plan.tasks}
    added = 0
    for title, desc, fmt, term, resp, group, alg in COMMON:
        if group == block and title not in existing:
            add_task(plan, title, group, desc, fmt, term, resp, alg)
            added += 1
    return added


def ordered_tasks_by_group(plan: Plan) -> dict[str, list[PlanTask]]:
    groups: dict[str, list[PlanTask]] = {}
    for task in plan.tasks:
        groups.setdefault(task.group, []).append(task)
    return groups


def task_view_status(task: PlanTask, today: date | None = None) -> str:
    today = today or date.today()
    if task.status in ("done", "wait", "blocked"):
        return task.status
    if task.due_date:
        if task.due_date < today:
            return "over"
        if task.due_date <= add_workdays(today, 3):
            return "soon"
    return "plan"


def progress(plan: Plan) -> int:
    if not plan.tasks:
        return 0
    return int(sum(t.status == "done" for t in plan.tasks) / len(plan.tasks) * 100 + 0.5)


def validate_goals(plan: Plan) -> list[str]:
    errors = []
    n = len(plan.goals)
    if not GOAL_MIN <= n <= GOAL_MAX:
        errors.append(f"Нужно от {GOAL_MIN} до {GOAL_MAX} целей, сейчас {n}")
    for i, g in enumerate(plan.goals, start=1):
        if len((g.title or "").strip()) <= 15:
            errors.append(f"Цель {i}: формулировка должна быть длиннее 15 символов")
        if not (g.description or "").strip():
            errors.append(f"Цель {i}: заполните описание")
        if not (g.result or "").strip():
            errors.append(f"Цель {i}: заполните планируемый результат")
        if not g.due_date:
            errors.append(f"Цель {i}: укажите срок")
        elif plan.hire_date and plan.probation_end and not plan.hire_date <= g.due_date <= plan.probation_end:
            errors.append(f"Цель {i}: срок должен быть в пределах испытательного срока")
    return errors


def goal_to_task(goal: Goal, actor: str) -> PlanTask:
    """Цель из карты KPI становится обычной задачей и сразу видна в «Конструкторе задач»."""
    if goal.origin != "kpi":
        raise RuleError("В задачи переносятся только цели из карты KPI")
    plan, meta = goal.plan, goal.meta or {}
    task = add_task(plan, goal.title, "ind", meta.get("criterion") or goal.result or "", meta.get("fmt", "KPI-показатель"), "",
                    meta.get("responsible", "Сотрудник"), meta.get("algorithm"), "kpi",
                    "ИИ · " + meta.get("source", "карта KPI"), goal.due_date)
    audit(f"перенёс цель «{goal.title}» из блока целей в «Индивидуальные задачи в должности»", actor, plan)
    db.session.delete(goal)
    return task


def constructor_tasks(plan: Plan) -> list[PlanTask]:
    """«Конструктор задач» на экране целей: задачи из ДИ и перенесённые из карты KPI."""
    return [t for t in plan.tasks if t.source in ("jd", "kpi")]


def can_send_to_approval(plan: Plan) -> list[str]:
    errors = []
    if not plan.fio or not plan.position or not plan.hire_date:
        errors.append("Заполните блок 1: ФИО, должность и дату приёма")
    if not plan.manager:
        errors.append("Укажите руководителя")
    if not plan.mentor:
        errors.append("Наставник не назначен: его назначает руководитель вместе с целями")
    if not plan.goals_written_at:
        errors.append("Руководитель ещё не записал цели")
    if not plan.tasks:
        errors.append("В плане нет задач")
    errors.extend(f"У задачи «{t.title}» нет алгоритма выполнения" for t in plan.tasks if not t.algorithm)
    return errors


def preboarding_letter(plan: Plan) -> tuple[str, str]:
    values = {"{{ФИО}}": plan.fio, "{{ДатаПриёма}}": fmt_date(plan.hire_date), "{{Должность}}": plan.position,
              "{{Руководитель}}": plan.manager.full_name if plan.manager else plan.manager_name or "коллега"}
    subject = get_setting("preboarding_subject", PREBOARDING_SUBJECT)
    body = get_setting("preboarding_body", PREBOARDING_BODY)
    for key, value in values.items():
        subject, body = subject.replace(key, value or ""), body.replace(key, value or "")
    return subject, body


def create_signal(plan: Plan, source: str, level: str, title: str, detail: str = "", evidence=None, recommendation: str = "",
                  repkey: str | None = None, deadline: date | None = None) -> Signal:
    days = 1 if source == "help" else tone_service.PLAN_SLA_DAYS.get(level, 5)
    signal = Signal(plan=plan, source=source, level=level, title=title, detail=detail, evidence=evidence or [], recommendation=recommendation,
                    repkey=repkey, deadline=deadline or add_workdays(date.today(), days))
    db.session.add(signal)
    return signal


def signal_recipients(plan: Plan) -> list:
    return [plan.manager, plan.mentor, plan.recruiter]


def submit_survey(plan: Plan, key: str, answers: dict, actor: str) -> list[Signal]:
    survey = SURVEYS[key]
    if SurveyResponse.query.filter_by(plan_id=plan.id, survey_key=key).first():
        raise RuleError("Эта анкета уже заполнена")
    missing = [q["t"] for q in survey["q"] if q.get("req") and not (answers.get(q["t"]) or "").strip()]
    if missing:
        raise RuleError("Ответьте на все обязательные вопросы")
    db.session.add(SurveyResponse(plan=plan, survey_key=key, answers=answers))
    audit(f"Пройдена анкета: {survey['name']}", actor, plan, kind="emp")
    created = [create_signal(plan, survey["source"], d["level"], d["title"], d["detail"], d["evidence"], d["recommendation"])
               for d in tone_service.build_survey_signals(survey, answers)]
    if key in ("day1", "week1"):
        title_part = "первого рабочего дня" if key == "day1" else "первой рабочей недели"
        for task in plan.tasks:
            if task.group == "pulse" and title_part in task.title:
                task.status = "done"
                task.reported_at = datetime.now()
    if created:
        neg = sum(s.level != "средний" for s in created)
        audit(f"Найдено негативных ответов: {neg}, настораживающих: {len(created) - neg}. Создано сигналов: {len(created)}", "ИИ", plan, kind="ai")
        notify(signal_recipients(plan), "Сотрудник сообщил о проблеме: нужен план действий",
               f"{plan.fio}: {survey['name']}. Сигналов: {len(created)}.", f"/signals?plan={plan.id}")
    return created


def finish_plan(plan: Plan, passed: bool, dismissal_date: date | None, initiator: str, reason: str, comment: str, actor: str) -> None:
    any_dismissal = dismissal_date or initiator or reason
    if not passed or any_dismissal:
        missing = [name for name, value in (("дата увольнения", dismissal_date), ("инициатор", initiator), ("причина", reason)) if not value]
        if missing:
            raise RuleError("Блок «Увольнение»: заполните " + ", ".join(missing))
        if reason == "Другое" and len(comment.strip()) < 5:
            raise RuleError("Для причины «Другое» опишите её в комментарии")
    plan.dismissal_date, plan.dismissal_initiator, plan.dismissal_reason, plan.dismissal_comment = dismissal_date, initiator, reason, comment
    plan.final_at = datetime.now()
    set_status(plan, ST_PASSED if passed else ST_FAILED)
    for task in plan.tasks:
        if task.group == "meet" and task.term == "Третий месяц":
            task.status = "done"
    audit("Зафиксирован итог ИС: " + ("пройден" if passed else "не пройден")
          + (f"; увольнение {fmt_date(dismissal_date)}, инициатор: {initiator}, причина: {reason}" if dismissal_date else ""), actor, plan)
    learning = User.query.filter_by(role=LEARNING, active=True).all()
    notify([plan.recruiter, plan.manager, *learning], "Итог испытательного срока", f"{plan.fio}: {plan.status}", f"/plans/{plan.id}")

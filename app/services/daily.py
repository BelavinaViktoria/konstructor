"""Ежедневная проверка: сроки, опросы, встречи, зона риска, эскалации. Запускается планировщиком (`flask daily-tick`).

Повторный запуск в тот же день безопасен: у каждого напоминания есть ключ dedupe_key.
"""
from datetime import date

from ..extensions import db
from ..models import LEARNING, ST_APPROVAL, ST_DRAFT, ST_WAIT_GOALS, ST_WORK, Plan, Signal, User
from .dates import add_workdays, fmt_date, workdays_between
from .notify import audit, notify
from .plans import create_signal, signal_recipients
from .risk import plan_risk


def _learning_managers() -> list[User]:
    return User.query.filter_by(role=LEARNING, active=True).all()


def _check_tasks(plan: Plan, today: date) -> None:
    link = f"/plans/{plan.id}"
    for task in plan.tasks:
        if task.status == "done" or not task.due_date:
            continue
        if task.status == "plan" and task.due_date == add_workdays(today, 3):
            notify([plan.employee], "Приближается срок по задаче", f"«{task.title}»: срок {fmt_date(task.due_date)}", link, f"soon:{task.id}")
        if task.status == "plan" and task.due_date < today:
            notify([plan.employee, plan.manager, plan.mentor], "Просрочена задача", f"{plan.fio}: «{task.title}», срок {fmt_date(task.due_date)}", link, f"over:{task.id}")
        if task.status == "wait" and task.reported_at:
            waited = workdays_between(task.reported_at.date(), today)
            if waited >= 2:
                notify([plan.manager], "Напоминание о подтверждении", f"{plan.fio}: «{task.title}» ждёт проверки", link, f"confirm2:{task.id}")
            if waited >= 4:
                notify([plan.recruiter], "Эскалация по подтверждению", f"{plan.fio}: «{task.title}» не подтверждена 4 рабочих дня", link, f"confirm4:{task.id}")


def _check_surveys(plan: Plan, today: date) -> None:
    if not plan.hire_date or today < plan.hire_date:
        return
    answered = {s.survey_key for s in plan.surveys}
    if "day1" not in answered:
        notify([plan.employee], "Опрос по итогам 1 рабочего дня", "Поделитесь впечатлениями о первом дне", f"/plans/{plan.id}/survey/day1", f"survey:day1:{plan.id}")
    if "week1" not in answered and today >= add_workdays(plan.hire_date, 4):
        notify([plan.employee], "Опрос по итогам 1 рабочей недели", "Расскажите, как прошла первая неделя", f"/plans/{plan.id}/survey/week1", f"survey:week1:{plan.id}")


def _check_meetings(plan: Plan, today: date) -> None:
    for task in plan.tasks:
        if task.group != "meet" or task.status == "done" or not task.due_date:
            continue
        if today >= add_workdays(task.due_date, -3) and today <= task.due_date:
            notify([plan.recruiter], f"Назначьте и проведите: {task.title}", f"{plan.fio}, срок {fmt_date(task.due_date)}", f"/plans/{plan.id}", f"meet:{task.id}")
        if today > task.due_date:
            notify([plan.recruiter, *_learning_managers()], "Эскалация: встреча не проведена", f"{plan.fio}: {task.title}", f"/plans/{plan.id}", f"meetlate:{task.id}")


def _check_risk(plan: Plan, today: date) -> Signal | None:
    risk = plan_risk(plan, today)
    if risk["level"] not in ("Средний", "Высокий"):
        return None
    key = f"риск:{risk['level']}"
    if any(s.source == "risk" and s.repkey == key for s in plan.signals):
        return None
    signal = create_signal(
        plan, "risk", "высокий" if risk["level"] == "Высокий" else "средний", f"Сотрудник в зоне риска «{risk['level'].lower()}»: {risk['score']} баллов",
        "Риск сложился из нескольких факторов одновременно.", [[f[1], f[2], "neg"] for f in risk["factors"]],
        "Проведите разговор один на один в ближайшие два рабочих дня, выясните главную причину и зафиксируйте, что меняете: задачи, сроки, наставника или условия.",
        repkey=key)
    audit(f"Сотрудник перешёл в зону риска «{risk['level'].lower()}» ({risk['score']} баллов)", "Система", plan, kind="sys")
    notify(signal_recipients(plan), "Риск ухода: укажите, что предпринято", f"{plan.fio}: {risk['score']} баллов", f"/signals?plan={plan.id}")
    return signal


def _check_signals(plan: Plan, today: date) -> None:
    for signal in plan.signals:
        if signal.status in ("new", "returned") and signal.deadline and signal.deadline < today:
            notify([*_learning_managers(), plan.recruiter], "Эскалация: сигнал без реакции", f"{plan.fio}: {signal.title}", f"/signals?plan={plan.id}", f"sigesc:{signal.id}")


def _check_preboarding(plan: Plan, today: date) -> None:
    if plan.hire_date and not plan.mentor and today >= add_workdays(plan.hire_date, -3) and today < plan.hire_date:
        notify([plan.manager, plan.recruiter], "Назначьте наставника", f"{plan.fio} выходит {fmt_date(plan.hire_date)}, наставник не указан", f"/plans/{plan.id}/goals", f"nomentor:{plan.id}")


def run(today: date | None = None) -> dict:
    today = today or date.today()
    active = Plan.query.filter_by(status=ST_WORK).all()
    for plan in active:
        _check_tasks(plan, today)
        _check_surveys(plan, today)
        _check_meetings(plan, today)
        _check_risk(plan, today)
        _check_signals(plan, today)
    early = Plan.query.filter(Plan.status.in_((ST_DRAFT, ST_WAIT_GOALS, ST_APPROVAL))).all()
    for plan in early:
        _check_preboarding(plan, today)
    db.session.commit()
    return {"active": len(active), "early": len(early)}

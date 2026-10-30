"""Бизнес-правила ИПР: создание из анкеты, сроки программы, практические задания, средние оценки."""
from datetime import date

from ..catalog import IPR_MEET_MONTHS, IPR_N_HARD, IPR_N_SOFT, IPR_PRACTICE_TEMPLATES
from ..extensions import db
from ..models import IPR_APPROVAL, IPR_DRAFT, IPR_RETURNED, Ipr, IprCandidate, IprMeeting, IprPractice
from .dates import add_months, prev_workday
from .notify import audit, notify
from .plans import RuleError


def needs_talent_q(ipr: Ipr) -> bool:
    return ipr.category in ("itr", "head")


def from_candidate(candidate: IprCandidate, actor: str) -> Ipr:
    """После статуса «Согласован руководителем» анкета становится черновиком ИПР."""
    ipr = Ipr(fio=candidate.fio, position=candidate.position, department=candidate.department, upr=candidate.upr, otd=candidate.otd, site=candidate.site,
              email=candidate.email, phone=candidate.phone, experience=candidate.experience, target=candidate.target, initiator=candidate.initiator,
              manager_name=candidate.manager_name, applied_date=candidate.submitted_at.date() if candidate.submitted_at else date.today())
    db.session.add(ipr)
    db.session.flush()
    audit("Создан черновик ИПР из анкеты кандидата", actor, ipr=ipr)
    return ipr


def set_start_date(ipr: Ipr, start: date | None) -> None:
    """Даты встреч считаются от первичной встречи; уже перенесённые даты не трогаем."""
    ipr.start_date = start
    existing = {m.kind: m for m in ipr.meetings}
    if not start:
        return
    if "first" not in existing:
        db.session.add(IprMeeting(ipr=ipr, kind="first", planned_date=start, status="set"))
    else:
        existing["first"].planned_date = start
    for kind, months in IPR_MEET_MONTHS.items():
        planned = prev_workday(add_months(start, months))
        if kind in existing:
            existing[kind].planned_date = planned
        else:
            db.session.add(IprMeeting(ipr=ipr, kind=kind, planned_date=planned))


def add_default_practices(ipr: Ipr) -> None:
    due = prev_workday(add_months(ipr.start_date, 12)) if ipr.start_date else None
    for title, result, is_ai in IPR_PRACTICE_TEMPLATES:
        db.session.add(IprPractice(ipr=ipr, title=title, result=result, due_date=due, is_ai=is_ai))


def averages(ipr: Ipr) -> dict:
    """Две средние: практические задания (без неактуальных) и онлайн-курсы."""
    practice = [p.pct for p in ipr.practices if p.status != "na"]
    courses = [c.pct for c in ipr.courses]
    return {"practice": int(sum(practice) / len(practice) + 0.5) if practice else None,
            "courses": int(sum(courses) / len(courses) + 0.5) if courses else None}


def selected_skills(ipr: Ipr, kind: str | None = None) -> list:
    return [s for s in ipr.skills if s.selected and (kind is None or s.kind == kind)]


def approval_errors(ipr: Ipr) -> list[str]:
    errors = []
    if not ipr.fio or not ipr.target:
        errors.append("Заполните ФИО и целевую должность")
    if not ipr.manager:
        errors.append("Укажите руководителя")
    if not ipr.start_date:
        errors.append("Укажите дату первичной встречи")
    if needs_talent_q(ipr) and not ipr.tq_done:
        errors.append("Для ИТР и руководителей нужен результат теста Talent Q: пока теста нет, ИПР остаётся черновиком")
    hard, soft = len(selected_skills(ipr, "hard")), len(selected_skills(ipr, "soft"))
    if hard < 1 and soft < 1:
        errors.append(f"Выберите навыки для развития (рекомендуется {IPR_N_HARD} профессиональных и {IPR_N_SOFT} личных)")
    if not [p for p in ipr.practices if p.status != "na"]:
        errors.append("Добавьте хотя бы одно практическое задание")
    return errors


def send_to_approval(ipr: Ipr, actor: str) -> None:
    if ipr.status not in (IPR_DRAFT, IPR_RETURNED):
        raise RuleError("Отправить можно только черновик или возвращённый ИПР")
    errors = approval_errors(ipr)
    if errors:
        raise RuleError("; ".join(errors))
    ipr.status, ipr.return_comment = IPR_APPROVAL, ""
    audit("ИПР отправлен на согласование руководителю", actor, ipr=ipr)
    notify([ipr.manager], "ИПР на согласование", f"{ipr.fio} → {ipr.target}", f"/ipr/{ipr.id}/card")


def change_practice_due(practice: IprPractice, new_due: date | None, actor: str) -> None:
    if not new_due:
        raise RuleError("Укажите новую дату")
    if practice.due_date == new_due:
        return
    old, practice.due_date = practice.due_date, new_due
    ipr = practice.ipr
    audit(f"Изменён срок задания «{practice.title[:80]}»: {old} → {new_due}", actor, ipr=ipr)
    notify([ipr.reservist, ipr.mentor, ipr.manager], "Изменён срок практического задания",
           f"{ipr.fio}: «{practice.title[:120]}», новый срок {new_due.strftime('%d.%m.%Y')}", f"/ipr/{ipr.id}/card")


def strike_practice(practice: IprPractice, reason: str, actor: str) -> None:
    reason = (reason or "").strip()
    if len(reason) < 5:
        raise RuleError("Укажите причину, по которой задание стало неактуальным")
    practice.status, practice.na_reason = "na", reason
    audit(f"Задание «{practice.title[:80]}» отмечено неактуальным: {reason}", actor, ipr=practice.ipr)


def meeting_filter_value(ipr: Ipr, kind: str) -> date | None:
    """Фильтры рабочего стола учитывают перенесённые даты."""
    return next((m.effective_date for m in ipr.meetings if m.kind == kind), None)

"""Оценка риска удержания: выполнение ПВД, качество задач и ответы сотрудника."""
from dataclasses import dataclass
from datetime import date

from ..models import ST_WORK


@dataclass
class Facts:
    active: bool
    days: int
    progress: int
    overdue: int
    returned: int
    pulse: str  # "-", ok, late, alarm
    m1: str  # "-", ok, late, alarm
    goals_at_risk: int


def assess(f: Facts) -> dict:
    factors, score = [], 0
    if f.active:
        expected = min(100, int(f.days / 90 * 100 + 0.5))
        lag = expected - f.progress
        detail = f"выполнено {f.progress}% при ожидаемых {expected}% к {f.days}-му дню работы"
        if lag >= 30:
            score += 40
            factors.append(("lag", "Сильное отставание по плану", detail))
        elif lag >= 15:
            score += 25
            factors.append(("lag", "Отставание по плану", detail))
    if f.overdue > 0:
        score += min(30, f.overdue * 10)
        factors.append(("over", "Просроченные задачи", f"{f.overdue} задач(и) не выполнены в срок"))
    if f.returned > 0:
        score += min(24, f.returned * 8)
        factors.append(("ret", "Вопросы к качеству выполнения", f"{f.returned} задач(и) возвращены руководителем на доработку"))
    if f.pulse == "alarm":
        score += 18
        factors.append(("pulseAlarm", "Тревожные ответы в пульс-опросах", "есть негативные ответы сотрудника"))
    elif f.pulse == "late":
        score += 12
        factors.append(("pulseLate", "Пульс-опрос не пройден в срок", "обратной связи от сотрудника нет"))
    if f.m1 == "alarm":
        score += 25
        factors.append(("expect", "Тревожные ответы на встрече через месяц", "ожидания не совпали с реальностью или есть пробелы в знаниях"))
    elif f.m1 == "late":
        score += 10
        factors.append(("m1Late", "Встреча через месяц не проведена", "контрольная точка пропущена"))
    if f.goals_at_risk > 0:
        score += f.goals_at_risk * 13
        factors.append(("goals", "Цели под угрозой", f"{f.goals_at_risk} цел(и) без результата при истекающем сроке"))
    return {"score": score, "level": level_of(score)[0], "chip": level_of(score)[1], "factors": factors}


def level_of(score: int) -> tuple[str, str]:
    if score >= 70:
        return "Высокий", "c-red"
    if score >= 35:
        return "Средний", "c-orange"
    if score > 0:
        return "Низкий", "c-amber"
    return "Нет", "c-green"


def plan_facts(plan, today: date | None = None) -> Facts:
    today = today or date.today()
    tasks = plan.tasks
    done = sum(t.status == "done" for t in tasks)
    progress = int(done / len(tasks) * 100 + 0.5) if tasks else 0
    overdue = sum(1 for t in tasks if t.status != "done" and t.due_date and t.due_date < today)
    returned = sum(t.returned_count for t in tasks)
    answered = {s.survey_key for s in plan.surveys}

    pulse = "-"
    pulse_tasks = [t for t in tasks if t.group == "pulse"]
    if any(s.source in ("pulse1", "pulse7") and s.level != "средний" for s in plan.signals):
        pulse = "alarm"
    elif any(t.due_date and t.due_date < today and t.status != "done" for t in pulse_tasks):
        pulse = "late"
    elif answered & {"day1", "week1"}:
        pulse = "ok"

    m1 = "-"
    if any(s.source == "m1" and s.level != "средний" for s in plan.signals):
        m1 = "alarm"
    elif plan.m1_done_at:
        m1 = "ok"
    elif any(t.group == "meet" and t.term == "Первый месяц" and t.due_date and t.due_date < today and t.status != "done" for t in tasks):
        m1 = "late"

    goals_at_risk = sum(1 for g in plan.goals if g.status in ("plan", "blocked") and g.due_date and (g.due_date - today).days <= 7)
    days = (today - plan.hire_date).days if plan.hire_date else 0
    return Facts(plan.status == ST_WORK, max(0, days), progress, overdue, returned, pulse, m1, goals_at_risk)


def plan_risk(plan, today: date | None = None) -> dict:
    facts = plan_facts(plan, today)
    result = assess(facts)
    result["facts"] = facts
    return result

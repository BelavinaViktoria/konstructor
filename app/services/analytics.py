"""Срезы по адаптации и поиск закономерностей. Выводы описывают корреляции, а не доказанные причины."""
import csv
import io
from collections import Counter
from datetime import date, datetime

from ..catalog import OUT_LEFT, RISK_FACTOR_NAMES
from ..extensions import db
from ..models import ST_FAILED, ST_PASSED, AdaptationRecord, Plan
from .risk import level_of, plan_risk

DEFAULT_FILTERS = {"date_by": "hire", "from": "", "to": "", "site": "", "department": "", "otd": "", "manager": "", "mentor": "",
                   "recruiter": "", "position": "", "out": "", "lvl": "", "over": ""}
SELECT_FIELDS = [("site", "Площадка"), ("department", "Департамент"), ("otd", "Отдел"), ("manager", "Руководитель"),
                 ("mentor", "Наставник"), ("recruiter", "Менеджер по подбору"), ("position", "Должность")]


def sync_records(today: date | None = None) -> None:
    """Обновляет строки аналитики по живым ПВД: начавшая работу история подтягивается без ручных выгрузок."""
    today = today or date.today()
    existing = {r.plan_id: r for r in AdaptationRecord.query.filter(AdaptationRecord.plan_id.isnot(None))}
    for plan in Plan.query.filter(Plan.hire_date.isnot(None)):
        rec = existing.get(plan.id) or AdaptationRecord(plan_id=plan.id, fio=plan.fio)
        risk = plan_risk(plan, today)
        facts = risk["facts"]
        rec.fio, rec.position, rec.otd, rec.department, rec.site = plan.fio, plan.position, plan.otd, plan.department, plan.site
        rec.manager = plan.manager.short_name if plan.manager else plan.manager_name
        rec.mentor = plan.mentor.short_name if plan.mentor else plan.mentor_name
        rec.recruiter = plan.recruiter.short_name if plan.recruiter else ""
        rec.hire_date, rec.left_date = plan.hire_date, plan.dismissal_date
        rec.days, rec.plan_pct, rec.overdue = facts.days, facts.progress, facts.overdue
        rec.risk, rec.factors = risk["score"], [f[0] for f in risk["factors"]]
        reactions = [(s.plan_at - s.created_at).days for s in plan.signals if s.plan_at]
        rec.reaction_days = max(reactions) if reactions else None
        if plan.status == ST_PASSED:
            rec.outcome = "Уволился после ИС" if plan.dismissal_date else "Прошёл ИС"
        elif plan.status == ST_FAILED:
            rec.outcome = "Не прошёл ИС"
        else:
            rec.outcome = "Работает"
        db.session.add(rec)
    db.session.commit()


def to_row(r: AdaptationRecord) -> dict:
    return {"fio": r.fio, "position": r.position, "otd": r.otd, "department": r.department, "site": r.site, "manager": r.manager,
            "mentor": r.mentor, "recruiter": r.recruiter, "hire": r.hire_date, "left": r.left_date, "out": r.outcome, "days": r.days or 0,
            "plan": r.plan_pct or 0, "over": r.overdue or 0, "risk": r.risk or 0, "react": r.reaction_days, "rf": list(r.factors or []),
            "lvl": level_of(r.risk or 0)[0], "lvl_chip": level_of(r.risk or 0)[1], "gone": r.outcome in OUT_LEFT}


def all_rows() -> list[dict]:
    return [to_row(r) for r in AdaptationRecord.query.order_by(AdaptationRecord.hire_date.desc())]


def _parse(value: str) -> date | None:
    try:
        return datetime.strptime(value, "%Y-%m-%d").date() if value else None
    except ValueError:
        return None


def apply_filters(rows: list[dict], f: dict) -> list[dict]:
    start, end, out = _parse(f.get("from", "")), _parse(f.get("to", "")), []
    for r in rows:
        d = r["hire"] if f.get("date_by", "hire") == "hire" else r["left"]
        if f.get("date_by") == "left" and not d:
            continue
        if d and ((start and d < start) or (end and d > end)):
            continue
        if any(f.get(key) and r[key] != f[key] for key, _ in SELECT_FIELDS):
            continue
        o = f.get("out", "")
        if (o == "gone" and not r["gone"]) or (o == "stay" and r["gone"]) or (o not in ("", "gone", "stay") and r["out"] != o):
            continue
        if f.get("lvl") and r["lvl"] != f["lvl"]:
            continue
        if (f.get("over") == "yes" and not r["over"]) or (f.get("over") == "no" and r["over"]):
            continue
        out.append(r)
    return out


def preset_filters(key: str) -> dict:
    f = dict(DEFAULT_FILTERS)
    if key == "gone":
        f.update(out="gone", date_by="left")
    elif key == "risk":
        f["lvl"] = "Высокий"
    elif key == "over":
        f["over"] = "yes"
    return f


def _share(n: int, d: int) -> int:
    return int(n / d * 100 + 0.5) if d else 0


def _avg(values: list[int]) -> int:
    return int(sum(values) / len(values) + 0.5) if values else 0


def _median(values: list[int]) -> int:
    return sorted(values)[len(values) // 2] if values else 0


def analyze(rows: list[dict], base: list[dict]) -> list[dict]:
    """rows: текущая выборка; base: выборка без фильтров исхода, риска и просрочек (знаменатель «принято за период»)."""
    gone = [r for r in rows if r["gone"]]
    findings = []

    def add(level, code, title, detail, rec, **numbers):
        findings.append({"level": level, "code": code, "title": title, "detail": detail, "rec": rec, "numbers": numbers})

    if len(gone) >= 3:
        top, cnt = Counter(r["manager"] for r in gone).most_common(1)[0]
        sh = _share(cnt, len(gone))
        if sh >= 50:
            hired = sum(r["manager"] == top for r in base)
            add("критический", "концентрация по руководителю", f"{sh}% покинувших компанию: сотрудники одного руководителя",
                f"Из {len(gone)} человек, покинувших компанию за период, {cnt} работали под руководством {top}. "
                f"В этом подразделении за период принято {hired} человек: не удержали {_share(cnt, hired)}% новичков.",
                "Это сигнал к проверке, а не вывод о конкретном человеке. Проведите exit-интервью, поговорите с оставшимися, "
                "сравните объём и реалистичность ПВД в этом отделе и проверьте нагрузку наставников. Вывод делайте на цифрах, а не на впечатлении.",
                доля=sh, ушло=cnt, принято=hired)
        top, cnt = Counter(r["mentor"] for r in gone).most_common(1)[0]
        sh = _share(cnt, len(gone))
        if sh >= 40 and cnt >= 3:
            hired = sum(r["mentor"] == top for r in base)
            add("высокий", "концентрация по наставнику", f"У наставника {top} не удержали {_share(cnt, hired)}% подопечных",
                f"{cnt} из {len(gone)} ушедших были закреплены за одним наставником. Всего за период он вёл {hired} новичков.",
                "Проверьте нагрузку: сколько новичков наставник вёл одновременно и в какой пропорции к основной работе. "
                "Посмотрите, проходил ли он обучение наставников и есть ли у него доступ к алгоритмам задач.", ушло=cnt, вёл=hired)

    risky = [r for r in base if r["risk"] >= 35]
    no_react = [r for r in risky if r["react"] is None]
    slow = [r for r in risky if r["react"] is not None and r["react"] > 10]
    if risky and (len(no_react) + len(slow)) / len(risky) >= 0.4:
        gone_slow = sum(r["react"] is None or r["react"] > 10 for r in gone)
        add("высокий", "реакция на риск", "На риск реагировали с опозданием или не реагировали вовсе",
            f"Из {len(risky)} сотрудников с уровнем риска «средний» и выше по {len(no_react)} нет ни одного действия, ещё по {len(slow)} реакция наступила "
            f"позже 10 дней. Среди покинувших компанию таких {gone_slow} из {len(gone)}. Средний срок реакции: {_avg([r['react'] for r in risky if r['react'] is not None])} дней.",
            "Риск, на который отреагировали в первые три дня, снимается разговором. Установите норматив реакции 3 рабочих дня и обязательную фиксацию того, что сделано.",
            риск=len(risky), без_реакции=len(no_react), поздно=len(slow))

    if len(gone) >= 3:
        d = _median([r["days"] for r in gone])
        w = int(d / 7 + 0.5)
        add("средний", "срок ухода", f"Типичный срок ухода: {d} дней, около {w}-й недели работы",
            f"Половина ушедших покинула компанию до {d}-го дня.",
            f"Поставьте дополнительную контрольную точку на {max(2, w - 1)}-й неделе: короткий звонок менеджера по подбору на 15 минут.", дней=d)

    m1_late = [r for r in gone if "m1Late" in r["rf"]]
    if gone and len(m1_late) / len(gone) >= 0.4:
        add("высокий", "пропущенные встречи", f"У {_share(len(m1_late), len(gone))}% ушедших не была проведена встреча через месяц",
            f"{len(m1_late)} из {len(gone)} покинувших компанию не прошли контрольную точку «встреча через месяц». Именно она вскрывает несовпадение ожиданий.",
            "Сделайте встречу обязательной: пока её итоги не внесены, менеджер по подбору видит задачу просроченной на рабочем столе.", без_встречи=len(m1_late))

    gone_plan = _avg([r["plan"] for r in gone])
    stay_plan = _avg([r["plan"] for r in base if not r["gone"] and r["plan"] > 0])
    if len(gone) >= 3 and stay_plan - gone_plan >= 20:
        add("средний", "выполнение плана", "Уходят те, у кого план ввода в должность не выполнялся",
            f"Средний процент выполнения ПВД у покинувших компанию: {gone_plan}%, у оставшихся: {stay_plan}%. Разрыв {stay_plan - gone_plan} процентных пунктов.",
            "Выполнение плана ниже 40% к середине испытательного срока можно использовать как ранний сигнал наравне с уровнем риска.", ушли=gone_plan, остались=stay_plan)

    factors = Counter(k for r in gone for k in r["rf"]).most_common(3)
    if factors and len(gone) >= 3:
        text = ", ".join(f"{RISK_FACTOR_NAMES.get(k, k)}: {v} раз(а)" for k, v in factors)
        add("средний", "частые факторы", "Чаще всего у ушедших фиксировались одни и те же факторы", text + ".",
            "Разберите это как процесс: «задачи без понимания, как выполнять» указывают на алгоритмы и наставничество, «просроченные задачи» на реалистичность сроков ПВД.")

    order = {"критический": 0, "высокий": 1, "средний": 2}
    return sorted(findings, key=lambda x: order[x["level"]])


def stats(rows: list[dict], base: list[dict]) -> dict:
    gone = [r for r in rows if r["gone"]]
    return {"total": len(rows), "gone": len(gone), "retention": 100 - _share(sum(r["gone"] for r in base), len(base)) if base else 0,
            "avg_plan": _avg([r["plan"] for r in rows if r["plan"] > 0]), "high_risk": sum(r["lvl"] == "Высокий" for r in rows),
            "avg_react": _avg([r["react"] for r in rows if r["react"] is not None])}


def group_bars(rows: list[dict], key: str, limit: int = 8) -> list[tuple[str, int, int]]:
    """Подпись, значение, ширина столбика в процентах."""
    pairs = Counter(r[key] for r in rows if r["gone"]).most_common(limit)
    top = max((n for _, n in pairs), default=1)
    return [(name, n, int(n / top * 100)) for name, n in pairs]


def _csv_cell(value) -> str:
    """Защита от формул: Excel не должен исполнять значения вида =CMD()."""
    text = "" if value is None else str(value)
    return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text


def to_csv(rows: list[dict]) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=";", lineterminator="\r\n")
    writer.writerow(["ФИО", "Должность", "Отдел", "Департамент", "Площадка", "Руководитель", "Наставник", "Менеджер по подбору", "Дата приёма",
                     "Дата увольнения", "Дней в компании", "Выполнение ПВД %", "Просрочки", "Балл риска", "Уровень риска", "Реакция на риск, дней", "Исход", "Факторы риска"])
    for r in rows:
        writer.writerow([_csv_cell(v) for v in (
            r["fio"], r["position"], r["otd"], r["department"], r["site"], r["manager"], r["mentor"], r["recruiter"], r["hire"] or "", r["left"] or "",
            r["days"], r["plan"], r["over"], r["risk"], r["lvl"], "нет реакции" if r["react"] is None else r["react"], r["out"],
            "; ".join(RISK_FACTOR_NAMES.get(k, k) for k in r["rf"]))])
    return "﻿" + buffer.getvalue()

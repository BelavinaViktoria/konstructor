"""Рабочие дни и сроки. Праздники здесь не учитываются: только суббота и воскресенье."""
import calendar
from datetime import date, datetime, timedelta


def is_workday(d: date) -> bool:
    return d.weekday() < 5


def add_workdays(d: date, n: int) -> date:
    step = 1 if n >= 0 else -1
    left = abs(n)
    while left:
        d += timedelta(days=step)
        if is_workday(d):
            left -= 1
    return d


def workdays_between(start: date, end: date) -> int:
    """Сколько рабочих дней прошло после start до end включительно."""
    n, cur = 0, start
    while cur < end:
        cur += timedelta(days=1)
        if is_workday(cur):
            n += 1
    return n


def prev_workday(d: date) -> date:
    while not is_workday(d):
        d -= timedelta(days=1)
    return d


def add_months(d: date, months: int) -> date:
    month_index = d.month - 1 + months
    year = d.year + month_index // 12
    month = month_index % 12 + 1
    return date(year, month, min(d.day, calendar.monthrange(year, month)[1]))


def term_to_date(term: str, hire: date | None) -> date | None:
    """Срок задачи из конструктора превращается в дату от даты приёма."""
    if not hire:
        return None
    if term == "Первый день":
        return hire
    if term == "Первая неделя":
        return add_workdays(hire, 4)
    months = {"Первый месяц": 1, "Второй месяц": 2, "Третий месяц": 3}.get(term)
    return prev_workday(add_months(hire, months)) if months else None


def parse_date(value: str | None) -> date | None:
    if not value:
        return None
    for fmt in ("%Y-%m-%d", "%d.%m.%Y"):
        try:
            return datetime.strptime(value.strip(), fmt).date()
        except ValueError:
            continue
    return None


def fmt_date(d: date | None) -> str:
    return d.strftime("%d.%m.%Y") if d else "—"

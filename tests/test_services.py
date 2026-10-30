from datetime import date

from app import catalog
from app.seed import DAY1_ANSWERS, KPI_SAMPLE, WEEK1_ANSWERS
from app.services import ai, analytics, kpi, tone
from app.services.dates import add_workdays, prev_workday, term_to_date
from app.services.risk import Facts, assess


def test_workdays_skip_weekend():
    friday = date(2026, 8, 14)
    assert add_workdays(friday, 1) == date(2026, 8, 17)
    assert add_workdays(date(2026, 8, 17), -1) == friday
    assert prev_workday(date(2026, 8, 16)) == friday
    assert term_to_date("Первая неделя", date(2026, 8, 17)) == date(2026, 8, 21)
    assert term_to_date("Первый день", None) is None


def test_risk_score_matches_prototype_rules():
    # 55% при ожидаемых 69%: отставание меньше 15, остальное складывается как в прототипе
    result = assess(Facts(True, 62, 55, 3, 2, "alarm", "alarm", 1))
    assert result["score"] == 30 + 16 + 18 + 25 + 13
    assert result["level"] == "Высокий"
    assert assess(Facts(True, 10, 10, 0, 0, "-", "-", 0))["level"] == "Нет"
    assert assess(Facts(True, 90, 40, 0, 0, "-", "-", 0))["score"] == 40


def test_tone_counts_match_prototype_journal():
    # В журнале прототипа: опрос дня 2 негативных и 1 настораживающий, недели 1 и 3
    day1 = tone.build_survey_signals(catalog.SURVEYS["day1"], DAY1_ANSWERS)
    assert sorted(s["level"] for s in day1) == ["высокий", "высокий", "средний"]
    week1 = tone.build_survey_signals(catalog.SURVEYS["week1"], WEEK1_ANSWERS)
    assert sorted(s["level"] for s in week1) == ["высокий", "средний", "средний", "средний"]
    assert any("Наставник недоступен" in s["title"] for s in week1)


def test_tone_answers():
    long_q = {"ty": "long", "t": "Какие трудности возникали?"}
    one_q = {"ty": "one", "t": "Вопрос"}
    assert tone.tone(long_q, "Нет трудностей")["t"] == "ok"
    assert tone.tone(long_q, "Нет доступа в CRM")["t"] == "neg"
    assert tone.tone(one_q, "Нет, не начал")["t"] == "neg"
    assert tone.tone(one_q, "Да, полностью понятны")["t"] == "ok"
    assert tone.tone(one_q, "Скорее понятны, но остались вопросы")["t"] == "warn"
    assert tone.tone({"ty": "know", "t": "x"}, "Не знает")["t"] == "neg"


def test_kpi_parse_sample_without_money():
    result = kpi.parse_workbook([KPI_SAMPLE], date(2026, 8, 17))
    items = result["items"]
    assert len(items) == 6  # 3 показателя и 3 SMART-задачи, строка «Смарт задачи» заменена перечнем
    assert result["fio"] == "Иванов Иван Иванович"
    text = str(items)
    assert "1000" not in text and "рубл" not in text and "00000000" not in text
    assert all(i["algorithm"] for i in items)
    assert all(date(2026, 8, 17) <= date.fromisoformat(i["due"]) <= date(2026, 11, 17) for i in items)


def test_kpi_not_a_card():
    assert kpi.parse_workbook([{"name": "x", "g": {1: {1: "просто текст"}}}], None) is None


def test_rules_from_jd_extracts_duties():
    text = "Обязанности:\n1. Организует закупки материалов для производства\n2. Контролирует сроки поставки и качество\nОбщие положения"
    result = ai.rules_from_jd(text)
    assert [t["title"] for t in result["tasks"]] == ["Организует закупки материалов для производства", "Контролирует сроки поставки и качество"]
    assert all(t["algorithm"] for t in result["tasks"])


def _row(**kw):
    base = dict(fio="x", position="p", otd="o", department="d", site="s", manager="М", mentor="Н", recruiter="Р", hire=date(2026, 1, 1), left=None,
                out="Работает", days=50, plan=80, over=0, risk=0, react=None, rf=[], lvl="Нет", lvl_chip="", gone=False)
    base.update(kw)
    return base


def test_analyze_finds_manager_concentration():
    gone = [_row(fio=str(i), manager="Гордеев", mentor=f"Н{i}", gone=True, out="Уволился в ИС", days=30, plan=20, risk=90, rf=["over"]) for i in range(4)]
    stay = [_row(fio=f"s{i}", manager=f"М{i}", mentor=f"Н{i}", plan=90) for i in range(4)]
    rows = gone + stay
    codes = {f["code"]: f for f in analytics.analyze(rows, rows)}
    assert "концентрация по руководителю" in codes
    assert codes["концентрация по руководителю"]["level"] == "критический"
    assert "выполнение плана" in codes


def test_analyze_empty_has_no_findings():
    assert analytics.analyze([], []) == []


def test_filters_and_csv_injection():
    rows = [_row(fio="=CMD()", gone=True, out="Уволился в ИС", left=date(2026, 3, 1)), _row(fio="Б", over=2)]
    assert len(analytics.apply_filters(rows, {"out": "gone"})) == 1
    assert len(analytics.apply_filters(rows, {"over": "yes"})) == 1
    assert len(analytics.apply_filters(rows, {"date_by": "left"})) == 1
    csv_text = analytics.to_csv(rows)
    assert "'=CMD()" in csv_text and csv_text.startswith("﻿")

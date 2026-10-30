"""Сквозные сценарии ПВД через HTTP."""
from datetime import date, timedelta

from conftest import user

from app.extensions import db
from app.models import MANAGER, MENTOR, NEWBIE, RECRUITER, Goal, Notification, Plan, PlanTask, Signal
from app.seed import DAY1_ANSWERS
from app.services import daily
from app.services.dates import add_workdays


def _make_plan(login, hire=None):
    client = login(RECRUITER)
    hire = hire or date.today() + timedelta(days=10)
    response = client.post("/plans/new", data={"fio": "Иванов Иван Иванович", "position": "Менеджер по продажам", "hire_date": hire.isoformat(),
                                               "manager_id": user(MANAGER).id, "employee_id": user(NEWBIE).id})
    assert response.status_code == 302
    return Plan.query.one()


def _goal_form(plan, action="write"):
    data = {"action": action, "mentor_id": user(MENTOR).id}
    for goal in plan.goals:
        data[f"title_{goal.id}"] = goal.title or f"Освоить важную функцию номер {goal.id}"
        data[f"description_{goal.id}"] = "Описание цели"
        data[f"result_{goal.id}"] = "Результат цели"
        data[f"due_{goal.id}"] = (plan.hire_date + timedelta(days=30)).isoformat()
    return data


def _prepare_goals(login, plan):
    client = login(MANAGER)
    for _ in range(3):
        client.post(f"/plans/{plan.id}/goals/save", data={"action": "add"})
    return client


def test_preboarding_letter_on_creation(login):
    plan = _make_plan(login)
    note = Notification.query.filter_by(user_id=plan.manager_id).one()
    assert plan.fio in note.subject and "наставника" in note.body
    assert plan.probation_end > plan.hire_date


def test_full_cycle_to_final(login):
    plan = _make_plan(login, date.today() - timedelta(days=5))
    client = login(RECRUITER)
    client.post(f"/plans/{plan.id}/std/all")
    assert len(plan.tasks) == 6
    response = client.post(f"/plans/{plan.id}/send-approval", follow_redirects=True)
    assert "Руководитель ещё не записал цели".encode() in response.data
    assert plan.status == "Черновик"

    client.post(f"/plans/{plan.id}/request-goals")
    assert plan.status == "Ожидает целей"

    client = _prepare_goals(login, plan)
    # без наставника цели не записываются
    no_mentor = _goal_form(plan)
    no_mentor.pop("mentor_id")
    response = client.post(f"/plans/{plan.id}/goals/save", data=no_mentor, follow_redirects=True)
    assert "Назначьте наставника".encode() in response.data
    assert plan.goals_written_at is None

    client.post(f"/plans/{plan.id}/goals/save", data=_goal_form(plan))
    assert plan.goals_written_at is not None and plan.mentor_id == user(MENTOR).id

    client = login(RECRUITER)
    client.post(f"/plans/{plan.id}/send-approval")
    assert plan.status == "На согласовании"

    client = login(MANAGER)
    client.post(f"/plans/{plan.id}/approve")
    assert plan.status == "В работе"

    client = login(NEWBIE)
    assert client.get(f"/plans/{plan.id}").status_code == 200
    client.post(f"/plans/{plan.id}/ack")
    assert plan.acknowledged_at is not None
    task = next(t for t in plan.tasks if t.group == "must")
    client.post(f"/tasks/{task.id}/report", data={"action": "done", "comment": "Сделал", "link": "https://example.com/x"})
    assert task.status == "wait"

    client = login(MANAGER)
    client.post(f"/tasks/{task.id}/review", data={"decision": "return", "comment": "Доработать"})
    assert task.status == "plan" and task.returned_count == 1
    client = login(NEWBIE)
    client.post(f"/tasks/{task.id}/report", data={"action": "done", "comment": "Исправил"})
    client = login(MANAGER)
    client.post(f"/tasks/{task.id}/review", data={"decision": "ok"})
    assert task.status == "done"

    client = login(RECRUITER)
    response = client.post(f"/plans/{plan.id}/final", data={"verdict": "no"}, follow_redirects=True)
    assert "Блок «Увольнение»".encode() in response.data
    assert plan.status == "В работе"
    client.post(f"/plans/{plan.id}/final", data={"verdict": "ok"})
    assert plan.status == "Завершён: ИС пройден"


def test_goals_limits(login):
    plan = _make_plan(login)
    client = login(MANAGER)
    for _ in range(7):
        client.post(f"/plans/{plan.id}/goals/save", data={"action": "add"})
    assert len(plan.goals) == 5
    first = plan.goals[0].id
    client.post(f"/goals/{first}/delete")
    assert len(plan.goals) == 4
    response = client.post(f"/plans/{plan.id}/goals/save", data=_goal_form(plan), follow_redirects=True)
    assert plan.goals_written_at is not None, response.data.decode()[:300]
    client.post(f"/goals/{plan.goals[0].id}/delete")
    client.post(f"/goals/{plan.goals[0].id}/delete")
    assert len(plan.goals) == 3  # меньше трёх удалить нельзя


def test_kpi_goal_moves_to_task_constructor(login):
    """Регресс: перенесённая цель должна сразу отображаться в «Конструкторе задач»."""
    plan = _make_plan(login)
    client = login(MANAGER)
    client.post(f"/plans/{plan.id}/kpi/demo")
    assert len(plan.kpi_draft["items"]) == 6
    client.post(f"/plans/{plan.id}/kpi/add", data={"item": [0, 1]})
    assert plan.kpi_draft is None
    goal = next(g for g in plan.goals if g.origin == "kpi")
    title = goal.title
    response = client.post(f"/goals/{goal.id}/to-task", follow_redirects=True)
    page = response.data.decode()
    assert title in page.split('id="constructor"')[1]
    assert "Из карты KPI" in page.split('id="constructor"')[1]
    task = PlanTask.query.filter_by(title=title).one()
    assert task.source == "kpi" and task.group == "ind" and task.algorithm
    assert not Goal.query.filter_by(title=title).first()
    assert client.get(f"/tasks/{task.id}/edit").status_code == 200
    client.post(f"/tasks/{task.id}/delete")
    assert not PlanTask.query.filter_by(title=title).first()


def test_manual_goal_cannot_move(login):
    plan = _make_plan(login)
    client = login(MANAGER)
    client.post(f"/plans/{plan.id}/goals/save", data={"action": "add"})
    goal = plan.goals[0]
    client.post(f"/goals/{goal.id}/to-task")
    assert db.session.get(Goal, goal.id) is not None


def test_jd_parse_and_apply(login):
    plan = _make_plan(login)
    client = login(MANAGER)
    response = client.post(f"/plans/{plan.id}/jd/parse", data={"text": "1. Организует закупки материалов для производства\n2. Контролирует сроки поставки и качество"})
    assert "Организует закупки".encode() in response.data
    import json
    item = {"title": "Организует закупки", "description": "Из ДИ", "algorithm": ["Шаг 1"]}
    client.post(f"/plans/{plan.id}/jd/apply", data={"task": [json.dumps(item, ensure_ascii=False)]})
    task = PlanTask.query.filter_by(title="Организует закупки").one()
    assert task.source == "jd" and task.algorithm == ["Шаг 1"]
    assert "Организует закупки".encode() in client.get(f"/plans/{plan.id}/goals").data


def _plan_in_work(login):
    plan = _make_plan(login, date.today() - timedelta(days=5))
    plan.status, plan.mentor, plan.recruiter = "В работе", user(MENTOR), user(RECRUITER)
    db.session.commit()
    return plan


def test_survey_creates_signals_and_mentor_takes(login):
    plan = _plan_in_work(login)
    client = login(NEWBIE)
    from app.catalog import SURVEYS
    form = {f"q{i}": DAY1_ANSWERS.get(q["t"], "") for i, q in enumerate(SURVEYS["day1"]["q"]) if q["ty"] != "intro"}
    client.post(f"/plans/{plan.id}/survey/day1", data=form)
    assert Signal.query.count() == 3
    signal = Signal.query.first()

    client = login(MENTOR)
    client.post(f"/signals/{signal.id}/take", data={"action_plan": "Подам заявку в ИТ", "action_due": date.today().isoformat()})
    assert signal.status == "work" and signal.owner_role == "Наставник"
    note = Notification.query.filter_by(user_id=plan.manager_id, subject="Наставник взял сигнал в работу: план мероприятий").one()
    assert "Подам заявку в ИТ" in note.body

    client = login(MANAGER)  # выполнить может только взявший
    assert client.post(f"/signals/{signal.id}/done", data={"result": "Готово, всё сделано"}).status_code == 403

    client = login(MENTOR)
    client.post(f"/signals/{signal.id}/done", data={"result": "Доступы выданы сегодня"})
    assert signal.status == "done"
    client = login(RECRUITER)
    client.post(f"/signals/{signal.id}/return", data={"comment": "Уточните дату"})
    assert signal.status == "returned"


def test_help_creates_signal_with_short_deadline(login):
    plan = _plan_in_work(login)
    client = login(RECRUITER)
    client.post(f"/plans/{plan.id}/std/must")
    task = plan.tasks[0]
    client = login(NEWBIE)
    client.post(f"/tasks/{task.id}/report", data={"action": "help", "comment": "Не могу найти регламент"})
    signal = Signal.query.filter_by(source="help").one()
    assert task.status == "blocked" and signal.level == "высокий"
    assert signal.deadline == add_workdays(date.today(), 1)


def test_daily_tick_idempotent_and_finds_overdue(login):
    plan = _plan_in_work(login)
    client = login(RECRUITER)
    client.post(f"/plans/{plan.id}/std/all")
    late = PlanTask(plan=plan, title="Старая задача", group="ind", algorithm=["шаг"], due_date=date.today() - timedelta(days=3))
    db.session.add(late)
    db.session.commit()
    daily.run()
    first = Notification.query.count()
    assert Notification.query.filter_by(subject="Просрочена задача").count() >= 3
    daily.run()
    assert Notification.query.count() == first


def test_access_control(login, app):
    plan = _make_plan(login)
    client = login("mgr2@example.com")
    assert client.get(f"/plans/{plan.id}").status_code == 403
    assert client.get(f"/plans/{plan.id}/goals").status_code == 403
    assert client.post(f"/plans/{plan.id}/approve").status_code == 403
    client = login(NEWBIE)
    assert client.get("/desk").status_code == 403
    assert client.get("/analytics").status_code == 403
    assert client.get("/admin").status_code == 403
    assert client.get(f"/plans/{plan.id}/goals").status_code == 403
    assert app.test_client().get("/desk").status_code == 302


def test_task_without_algorithm_rejected(login):
    plan = _make_plan(login)
    client = login(RECRUITER)
    response = client.post(f"/plans/{plan.id}/tasks/new", data={"title": "Новая", "algorithm": ""}, follow_redirects=True)
    assert "алгоритм".encode() in response.data
    assert not PlanTask.query.filter_by(title="Новая").first()
    client.post(f"/plans/{plan.id}/tasks/new", data={"title": "Новая", "algorithm": "Шаг 1\nШаг 2"})
    assert PlanTask.query.filter_by(title="Новая").one().algorithm == ["Шаг 1", "Шаг 2"]


def test_all_screens_render(login):
    plan = _plan_in_work(login)
    for role, urls in {
        RECRUITER: ["/desk", "/plans/new", f"/plans/{plan.id}/build", f"/plans/{plan.id}/goals", f"/plans/{plan.id}/m1", f"/plans/{plan.id}/final", "/signals", "/analytics", "/analytics?analyze=1", "/analytics/report", "/notifications"],
        MANAGER: ["/manager", f"/plans/{plan.id}/goals", f"/plans/{plan.id}", "/signals"],
        MENTOR: ["/mentor", f"/plans/{plan.id}", "/signals"],
        NEWBIE: ["/my-plan", f"/plans/{plan.id}", f"/plans/{plan.id}/survey/day1", f"/plans/{plan.id}/survey/week1"],
    }.items():
        client = login(role)
        for url in urls:
            assert client.get(url, follow_redirects=True).status_code == 200, url

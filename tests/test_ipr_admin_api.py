"""ИПР, админ-панель, интеграции, безопасность, демо-данные."""
from datetime import date, timedelta

from conftest import PASSWORD, user

from app.extensions import db
from app.models import LEARNING, MANAGER, MENTOR, NEWBIE, RESERVIST, TOP, Ipr, IprCandidate, IprMeeting, IprPractice, IprSkill, Notification, Plan, User
from app.seed import seed_demo


def _ipr(login, category="line"):
    client = login(LEARNING)
    client.post("/ipr/new", data={"fio": "Резервист Тест", "target": "Начальник", "category": category, "start_date": date.today().isoformat(),
                                  "manager_id": user(MANAGER).id, "mentor_id": user(MENTOR).id, "reservist_id": user(RESERVIST).id})
    return Ipr.query.one(), client


def _ready(client, ipr):
    client.post(f"/ipr/{ipr.id}/skills/add", data={"title": "Делегирование", "kind": "soft"})
    client.post(f"/ipr/{ipr.id}/practices/defaults")


def test_candidate_becomes_draft_once(login):
    db.session.add(IprCandidate(fio="Ковалёва Дарья", target="Руководитель группы", manager_name="Кротов Д. С."))
    db.session.commit()
    candidate = IprCandidate.query.one()
    client = login(LEARNING)
    client.post(f"/ipr/candidates/{candidate.id}", data={"status": "mgr_ok"})
    client.post(f"/ipr/candidates/{candidate.id}", data={"status": "mgr_ok"})
    assert Ipr.query.filter_by(fio="Ковалёва Дарья", status="Черновик").count() == 1


def test_meetings_calculated_from_start(login):
    ipr, _ = _ipr(login)
    kinds = {m.kind for m in ipr.meetings}
    assert kinds == {"first", "m2", "bf", "fin"}
    m2 = next(m for m in ipr.meetings if m.kind == "m2")
    assert m2.planned_date > ipr.start_date


def test_talent_q_required_for_head_keeps_draft(login):
    ipr, client = _ipr(login, "head")
    _ready(client, ipr)
    client.post(f"/ipr/{ipr.id}/send")
    assert ipr.status == "Черновик"
    client.post(f"/ipr/{ipr.id}/talentq", data={"tq_done": date.today().isoformat(), "tq_rows": "Делегирование; 2,4; 4"})
    assert ipr.tq_rows == [["Делегирование", 2.4, 4.0]]
    client.post(f"/ipr/{ipr.id}/send")
    assert ipr.status == "На согласовании у руководителя"


def test_ipr_full_cycle_and_averages(login):
    ipr, client = _ipr(login)
    _ready(client, ipr)
    client.post(f"/ipr/{ipr.id}/courses/add", data={"title": "Курс", "url": "https://example.com/c"})
    client.post(f"/ipr/{ipr.id}/courses/add", data={"title": "Плохой", "url": "javascript:alert(1)"})
    assert [c.title for c in ipr.courses] == ["Курс"]
    client.post(f"/ipr/{ipr.id}/send")

    client = login(MANAGER)
    response = client.post(f"/ipr/{ipr.id}/decision", data={"decision": "return"}, follow_redirects=True)
    assert "поправить".encode() in response.data
    client.post(f"/ipr/{ipr.id}/decision", data={"decision": "ok"})
    assert ipr.status == "В работе"

    p1, p2, p3 = ipr.practices
    client = login(RESERVIST)
    assert client.get("/ipr/my").status_code == 200
    client.post(f"/ipr/practices/{p1.id}/progress", data={"pct": 50})
    client.post(f"/ipr/practices/{p2.id}/progress", data={"pct": 100, "action": "done"})
    assert p1.status == "work" and p2.status == "done"

    client = login(LEARNING)
    response = client.post(f"/ipr/practices/{p3.id}/strike", data={"reason": "кор"}, follow_redirects=True)
    assert p3.status != "na"
    client.post(f"/ipr/practices/{p3.id}/strike", data={"reason": "Подразделение реорганизовано"})
    assert p3.status == "na"
    card = client.get(f"/ipr/{ipr.id}/card").data.decode()
    assert "75%" in card  # среднее считается без зачёркнутого задания

    new_due = date.today() + timedelta(days=200)
    client.post(f"/ipr/practices/{p1.id}/due", data={"due_date": new_due.isoformat()})
    assert p1.due_date == new_due
    assert Notification.query.filter_by(user_id=user(RESERVIST).id, subject="Изменён срок практического задания").count() == 1
    assert response.status_code == 200


def test_reservist_sees_only_selected_skills(login):
    ipr, client = _ipr(login)
    db.session.add(IprSkill(ipr=ipr, kind="hard", title="Невыбранный навык", selected=False))
    db.session.add(IprSkill(ipr=ipr, kind="hard", title="Выбранный навык", selected=True))
    ipr.status = "В работе"
    db.session.commit()
    page = login(RESERVIST).get("/ipr/my").data.decode()
    assert "Выбранный навык" in page and "Невыбранный навык" not in page


def test_desk_filter_uses_moved_meeting_date(login):
    ipr, client = _ipr(login)
    meeting = IprMeeting.query.filter_by(ipr_id=ipr.id, kind="m2").one()
    moved = meeting.planned_date + timedelta(days=40)
    client.post(f"/ipr/meetings/{meeting.id}", data={"moved_date": moved.isoformat(), "status": "set"})
    assert meeting.effective_date == moved
    assert "Резервист Тест".encode() in client.get(f"/ipr/desk?m2_from={moved.isoformat()}&m2_to={moved.isoformat()}").data
    original = moved - timedelta(days=40)
    assert "Резервист Тест".encode() not in client.get(f"/ipr/desk?m2_from={original.isoformat()}&m2_to={original.isoformat()}").data


def test_ipr_access(login):
    ipr, _ = _ipr(login)
    client = login("mgr2@example.com")
    assert client.get(f"/ipr/{ipr.id}/card").status_code == 403
    client = login(TOP)
    assert client.get("/ipr/top").status_code == 200
    assert client.get(f"/ipr/{ipr.id}/card").status_code == 200
    client = login(NEWBIE)
    assert client.get(f"/ipr/{ipr.id}/card").status_code == 403


def test_all_ipr_and_admin_screens_render(login):
    ipr, client = _ipr(login)
    for url in ("/overview", "/ipr/candidates", "/ipr/desk", "/ipr/new", f"/ipr/{ipr.id}/build", f"/ipr/{ipr.id}/card", "/ipr/top", "/admin", "/admin/library/new"):
        assert client.get(url).status_code == 200, url


def test_admin_user_and_library(login, app):
    client = login(LEARNING)
    client.post("/admin/users", data={"full_name": "Новый Сотрудник", "email": "Created@Example.com", "role": "mentor", "password": "short"})
    assert not User.query.filter_by(email="created@example.com").first()
    client.post("/admin/users", data={"full_name": "Новый Сотрудник Петрович", "email": "Created@Example.com", "role": "mentor", "password": "long-enough-pass"})
    created = User.query.filter_by(email="created@example.com").one()
    assert created.short_name == "Новый С. П."
    client.post(f"/admin/users/{created.id}/toggle")
    assert created.active is False
    me = user(LEARNING)
    client.post(f"/admin/users/{me.id}/toggle")
    assert me.active is True
    client.post("/admin/library/new", data={"family": "sales", "title": "Без алгоритма", "algorithm": ""})
    from app.models import LibraryTask
    assert not LibraryTask.query.filter_by(title="Без алгоритма").first()
    client.post("/admin/library/new", data={"family": "sales", "title": "С алгоритмом", "algorithm": "Шаг"})
    assert LibraryTask.query.filter_by(title="С алгоритмом").one().algorithm == ["Шаг"]


def test_potok_api(client):
    url = "/api/potok/offer"
    body = {"id": "abc", "fio": "Морозов Алексей", "position": "Кладовщик", "hire_date": "2030-01-15", "manager_email": "mgr@example.com"}
    assert client.post(url, json=body).status_code == 401
    assert client.post(url, json=body, headers={"X-API-Key": "wrong"}).status_code == 401
    response = client.post(url, json=body, headers={"X-API-Key": "potok-key"})
    assert response.status_code == 201 and response.json["created"] is True
    plan = Plan.query.one()
    assert plan.source == "potok" and plan.status == "Черновик" and plan.family == "logi" and plan.manager_id == user(MANAGER).id
    assert client.post(url, json=body, headers={"X-API-Key": "potok-key"}).json["created"] is False
    assert Plan.query.count() == 1
    assert client.post(url, json={"id": "x"}, headers={"X-API-Key": "potok-key"}).status_code == 400


def test_integration_off_without_key(app):
    app.config["FORMS_API_KEY"] = ""
    assert app.test_client().post("/api/forms/candidate", json={"id": "1", "fio": "А"}).status_code == 503


def test_login_lockout(client):
    for _ in range(5):
        client.post("/login", data={"email": "rec@example.com", "password": "wrong"})
    response = client.post("/login", data={"email": "rec@example.com", "password": PASSWORD})
    assert response.status_code == 200 and "Слишком много попыток".encode() in response.data


def test_open_redirect_blocked(client):
    response = client.post("/login?next=https://evil.example/", data={"email": "rec@example.com", "password": PASSWORD})
    assert "evil.example" not in response.headers["Location"]


def test_csrf_enforced(app):
    app.config["WTF_CSRF_ENABLED"] = True
    assert app.test_client().post("/login", data={"email": "rec@example.com", "password": PASSWORD}).status_code == 400


def test_security_headers(client):
    response = client.get("/login")
    assert response.headers["X-Frame-Options"] == "DENY" and "default-src 'self'" in response.headers["Content-Security-Policy"]


def test_escaping_in_templates(login):
    client = login(LEARNING)
    client.post("/plans/new", data={"fio": "<script>alert(1)</script>"})
    assert b"<script>alert(1)" not in client.get("/desk").data


def test_seed_demo_has_no_real_domains(app):
    for u in User.query.all():
        db.session.delete(u)
    db.session.commit()
    accounts = seed_demo("demo-password-1")
    assert len(accounts) == 7
    assert all(u.email.endswith("@example.com") for u in User.query.all())
    assert Plan.query.count() == 5 and Ipr.query.count() == 2
    assert IprPractice.query.count() == 3

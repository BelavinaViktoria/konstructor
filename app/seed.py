"""Справочники и вымышленные демо-данные. Реальных сотрудников и внутренних адресов здесь быть не должно."""
from datetime import date, datetime, timedelta

from flask import current_app
from werkzeug.security import generate_password_hash

from . import catalog
from .extensions import db
from .models import (
    IPR_DRAFT, IPR_WORK, LEARNING, MANAGER, MENTOR, NEWBIE, RECRUITER, RESERVIST, ST_APPROVAL, ST_DRAFT, ST_WAIT_GOALS, ST_WORK, TOP, AdaptationRecord, Goal, Ipr, IprCandidate,
    IprCourse, IprPractice, IprSkill, LibraryTask, Plan, Position, User,
)
from .services import ipr as ipr_svc
from .services import plans as plan_svc
from .services.dates import add_months, add_workdays, prev_workday
from .services.notify import audit

# Пример карты KPI в той же структуре, что выдаёт kpi.read_sheets
KPI_SAMPLE = {"name": "карта сентябрь", "g": {
    1: {1: "Выплата за сентябрь"}, 2: {1: "Иванов Иван Иванович, таб. номер 00000000, Менеджер по продажам"},
    3: {1: "Подразделение", 2: "Должность", 3: "Показатель (КПЭ)", 5: "Периодичность оценки", 6: "Условия расчета премии", 7: "Поправочный К", 8: "Премия по показателю, руб.", 10: "Ответственный за предоставление данных"},
    4: {3: "№", 4: "Наименование"},
    5: {1: "Отдел прямых продаж", 2: "Менеджер по продажам", 3: "1", 4: "Выполнение плана продаж", 5: "месяц", 6: "100% плана продаж отдела за месяц", 7: "1", 8: "8000", 10: "Руководитель отдела прямых продаж Сидорова А.В."},
    6: {3: "2", 4: "Привлечение новых клиентов", 5: "месяц", 6: "1 новый клиент с первой отгрузкой = 1000 рублей, без потолка", 8: "5000", 10: "Руководитель отдела прямых продаж Сидорова А.В."},
    7: {3: "3", 4: "Ведение сделок в CRM", 5: "месяц", 6: "100% сделок и звонков заведены в CRM в день контакта", 7: "1", 8: "3000", 10: "Руководитель отдела прямых продаж Сидорова А.В."},
    8: {3: "4", 4: "Смарт задачи", 5: "месяц", 6: "В срок: К = 1,0", 7: "1", 8: "4000", 10: "Руководитель отдела прямых продаж Сидорова А.В."},
    9: {6: "С нарушением срока: К = 0,8"}, 10: {6: "Не выполнена: К = 0"},
    12: {1: "Сотрудник:"}, 14: {1: "(должность)", 4: "(ФИО)", 6: "(подпись)"},
    16: {1: "1. Провести 20 встреч с потенциальными клиентами"}, 17: {1: "2. Подготовить и отправить 5 коммерческих предложений"},
    18: {1: "3. Сдать аттестацию по скрипту продаж и стандартам работы с клиентами"},
}}

OTD = {
    "Отдел прямых продаж": ("Коммерческий департамент", "Головной офис", "Петров П. П."),
    "Отдел ключевых клиентов": ("Коммерческий департамент", "Головной офис", "Соловьёв И. Р."),
    "Отдел логистики": ("Операционный департамент", "Юг", "Козлов А. Н."),
    "Цех №2": ("Производственный департамент", "Север", "Орлов С. И."),
    "Цех №4": ("Производственный департамент", "Север", "Гордеев В. М."),
    "Бухгалтерия": ("Административный департамент", "Головной офис", "Белова Н. К."),
    "Отдел персонала": ("Административный департамент", "Головной офис", "Рябова Е. Н."),
}
R1, R2, R3 = "Рябова Е. Н.", "Титова М. Р.", "Осипова В. Г."
# ФИО, должность, отдел, наставник, менеджер по подбору, дней назад принят, исход, дней работы, % ПВД, просрочки, риск, реакция, факторы
HISTORY = [
    ("Абрамов П. К.", "Оператор станка", "Цех №4", "Кротов Д. С.", R2, 390, "Не прошёл ИС", 91, 41, 6, 118, 24, ["lag", "over", "ret", "pulseAlarm", "blocked", "m1Late"]),
    ("Сергеев В. А.", "Оператор станка", "Цех №4", "Кротов Д. С.", R2, 355, "Уволился в ИС", 24, 18, 3, 96, None, ["lag", "over", "blocked", "support", "m1Late"]),
    ("Ильин Р. С.", "Слесарь-ремонтник", "Цех №4", "Кротов Д. С.", R2, 334, "Уволился в ИС", 31, 26, 4, 104, 21, ["lag", "over", "ret", "blocked", "m1Late"]),
    ("Богданов А. И.", "Оператор станка", "Цех №4", "Ершов А. П.", R3, 292, "Уволился в ИС", 28, 22, 3, 92, None, ["lag", "over", "pulseAlarm", "blocked"]),
    ("Макаров Д. Л.", "Наладчик", "Цех №4", "Кротов Д. С.", R2, 271, "Уволился в ИС", 37, 34, 5, 110, 26, ["lag", "over", "ret", "climate", "blocked", "m1Late"]),
    ("Ковалёв С. Ю.", "Оператор станка", "Цех №4", "Кротов Д. С.", R2, 243, "Уволился в ИС", 41, 29, 4, 101, 18, ["lag", "over", "ret", "pulseAlarm", "m1Late"]),
    ("Демидов К. В.", "Слесарь-ремонтник", "Цех №4", "Ершов А. П.", R3, 208, "Уволился в ИС", 26, 20, 2, 88, None, ["lag", "over", "support", "blocked"]),
    ("Логинов М. П.", "Оператор станка", "Цех №4", "Кротов Д. С.", R2, 180, "Уволился в ИС", 33, 31, 4, 97, 16, ["lag", "over", "ret", "climate", "m1Late"]),
    ("Шестаков И. О.", "Наладчик", "Цех №4", "Ершов А. П.", R2, 152, "Уволился в ИС", 45, 38, 3, 84, 22, ["lag", "over", "blocked", "m1Late"]),
    ("Устинова Г. А.", "Оператор станка", "Цех №4", "Ершов А. П.", R3, 117, "Работает", 117, 64, 1, 38, 5, ["over"]),
    ("Тарасов Г. Ю.", "Инженер-технолог", "Цех №2", "Сорокин П. А.", R1, 194, "Прошёл ИС", 119, 96, 0, 8, None, []),
    ("Егоров И. Л.", "Слесарь-ремонтник", "Цех №2", "Сорокин П. А.", R3, 102, "Работает", 102, 48, 1, 22, 4, ["over"]),
    ("Филатов А. Н.", "Слесарь-ремонтник", "Цех №2", "Сорокин П. А.", R3, 257, "Уволился после ИС", 168, 88, 1, 26, 6, ["over", "goals"]),
    ("Абрикосов Л. В.", "Менеджер по продажам", "Отдел прямых продаж", "Сидорова А. В.", R2, 411, "Прошёл ИС", 364, 94, 1, 18, 3, ["over"]),
    ("Громова Н. И.", "Менеджер по продажам", "Отдел прямых продаж", "Сидорова А. В.", R2, 362, "Прошёл ИС", 315, 90, 0, 10, None, []),
    ("Ефимов С. С.", "Менеджер по продажам", "Отдел прямых продаж", "Сидорова А. В.", R3, 285, "Уволился в ИС", 70, 52, 3, 78, 9, ["lag", "over", "expect", "goals"]),
    ("Карпова А. Д.", "Специалист по продажам", "Отдел прямых продаж", "Сидорова А. В.", R3, 229, "Прошёл ИС", 182, 86, 1, 20, 4, ["over"]),
    ("Соловьёва К. И.", "Менеджер по ключевым клиентам", "Отдел ключевых клиентов", "Мешков А. А.", R1, 327, "Прошёл ИС", 280, 95, 0, 4, None, []),
    ("Дроздов П. Р.", "Менеджер по ключевым клиентам", "Отдел ключевых клиентов", "Мешков А. А.", R2, 264, "Прошёл ИС", 217, 89, 1, 14, 3, ["over"]),
    ("Родионов А. В.", "Специалист по логистике", "Отдел логистики", "Литвинов Е. С.", R2, 376, "Прошёл ИС", 329, 93, 0, 6, None, []),
    ("Кононова Е. П.", "Кладовщик", "Отдел логистики", "Литвинов Е. С.", R3, 278, "Уволился в ИС", 49, 43, 2, 72, 12, ["lag", "over", "tools", "support"]),
    ("Щербаков И. Н.", "Водитель-экспедитор", "Отдел логистики", "Литвинов Е. С.", R2, 201, "Прошёл ИС", 154, 87, 1, 16, 4, ["over"]),
    ("Виноградова Н. С.", "Бухгалтер", "Бухгалтерия", "Фомина Ю. А.", R1, 202, "Прошёл ИС", 155, 100, 0, 0, None, []),
    ("Лебедева А. П.", "Специалист по персоналу", "Отдел персонала", "Осипова В. Г.", R1, 169, "Прошёл ИС", 122, 100, 0, 0, None, []),
]

DAY1_ANSWERS = {
    "Было ли организовано рабочее место для Вас: ПК/ноутбук, телефон, канцелярия?": "Подготовлено частично",
    "Получена ли учетная запись для входа в корпоративную почту и рабочие системы?": "Нет, доступы не получены",
    "Познакомились ли Вы с непосредственным руководителем и коллегами?": "Да, познакомился и с руководителем, и с командой",
    "Опишите, пожалуйста, трудности, с которыми Вы могли столкнуться в первый рабочий день": "Не было доступа в CRM весь день, не смог начать работу с клиентской базой",
    "Понимаете ли Вы, где находится комната приема пищи, санитарные комнаты и другие бытовые помещения?": "Да, мне всё показали",
    "Вероятно, мы могли упустить что-то важное. Будем рады вашим комментариям и предложениям": "Команда встретила тепло, спасибо",
}
WEEK1_ANSWERS = {
    "Являются ли ваши обязанности четкими и понятными для Вас?": "Скорее понятны, но остались вопросы",
    "Какие трудности возникали у Вас в первую рабочую неделю?": "Наставник почти всю неделю был в командировке, вопросы задавать некому",
    "Удалось ли Вам начать прохождение обязательного тренинга?": "Да, прошёл полностью",
    "Есть ли у вас все инструменты для быстрого и эффективного выполнения задач?": "Частично, кое-чего не хватает",
    "Кто лучше всего помогает Вам адаптироваться на новом месте?": "Коллеги по отделу; Справляюсь самостоятельно",
    "Как Вы оцениваете атмосферу в коллективе?": "Доброжелательная; Рабочая, деловая",
    "Вероятно, мы могли упустить что-то важное. Будем рады вашим комментариям и предложениям": "",
}


def seed_catalog() -> None:
    """Справочники: повторный запуск ничего не дублирует."""
    if not Position.query.first():
        db.session.add_all(Position(name=n, family=f) for n, f in catalog.POSITIONS)
    if not LibraryTask.query.first():
        for family, items in catalog.LIBRARY.items():
            for kind, title, fmt, term, responsible, algorithm in items:
                db.session.add(LibraryTask(family=family, kind=kind, title=title, fmt=fmt, term=term, responsible=responsible, algorithm=algorithm))
    db.session.commit()


def _user(full_name, short, email, role, password_hash) -> User:
    user = User(full_name=full_name, short_name=short, email=email, role=role, password_hash=password_hash)
    db.session.add(user)
    return user


def _plan(fio, position, otd, hire, status, recruiter, manager=None, mentor=None, employee=None, family="sales", std=True, library=True, **kw) -> Plan:
    department, site, _ = OTD.get(otd, ("", "", ""))
    plan = Plan(fio=fio, position=position, otd=otd, department=department, site=site, family=family, status=status, recruiter=recruiter, manager=manager, mentor=mentor, employee=employee,
                manager_name=manager.short_name if manager else "", manager_email=manager.email if manager else "",
                mentor_name=mentor.short_name if mentor else "", mentor_email=mentor.email if mentor else "", **kw)
    db.session.add(plan)
    plan_svc.set_hire_date(plan, hire)
    if std:
        for block in catalog.STD_BLOCKS:
            plan_svc.add_std_block(plan, block)
    if library:
        for item in LibraryTask.query.filter_by(family=family).all():
            plan_svc.add_task(plan, item.title, "ind", item.description, item.fmt, item.term, item.responsible, item.algorithm)
    db.session.flush()
    return plan


def _goals(plan: Plan, hire: date) -> None:
    for i, (title, description, result) in enumerate([
        ("Освоить CRM и вести все сделки в системе", "Пройти обучение по CRM, завести все свои сделки, научиться выгружать отчёты", "100% сделок заведены в CRM, отчёт формируется без помощи наставника"),
        ("Выйти на план по обработке новых заявок", "Освоить скрипт первичного контакта, обрабатывать заявки из очереди", "Отработка 100% поступающих заявок за месяц"),
        ("Снизить издержки по логистике своих сделок", "Проанализировать маршруты доставки по своим сделкам, предложить оптимизацию", "Издержки процесса снижены на 10%"),
    ], start=1):
        db.session.add(Goal(plan=plan, title=title, description=description, result=result, due_date=prev_workday(add_months(hire, i))))
    plan.goals_written_at = datetime.now()


def seed_demo(password: str) -> list[tuple[str, str]]:
    """Демо-набор: все пользователи и примеры на каждом этапе процесса."""
    if User.query.first():
        raise RuntimeError("В базе уже есть пользователи: демо-данные не загружены.")
    pw = generate_password_hash(password, method=current_app.config["PASSWORD_HASH_METHOD"])
    today = date.today()

    learning = _user("Рябова Елена Николаевна", "Рябова Е. Н.", "learning@example.com", LEARNING, pw)
    recruiter = _user("Титова Марина Романовна", "Титова М. Р.", "recruiter@example.com", RECRUITER, pw)
    petrov = _user("Петров Пётр Петрович", "Петров П. П.", "manager@example.com", MANAGER, pw)
    orlov = _user("Орлов Сергей Ильич", "Орлов С. И.", "manager2@example.com", MANAGER, pw)
    sidorova = _user("Сидорова Анна Владимировна", "Сидорова А. В.", "mentor@example.com", MENTOR, pw)
    ershov = _user("Ершов Алексей Павлович", "Ершов А. П.", "mentor2@example.com", MENTOR, pw)
    ivanov = _user("Иванов Иван Иванович", "Иванов И. И.", "newbie@example.com", NEWBIE, pw)
    panov = _user("Панов Роман Викторович", "Панов Р. В.", "newbie2@example.com", NEWBIE, pw)
    sokolova = _user("Соколова Татьяна Игоревна", "Соколова Т. И.", "reservist@example.com", RESERVIST, pw)
    volkov = _user("Волков Сергей Николаевич", "Волков С. Н.", "top@example.com", TOP, pw)
    db.session.flush()

    # ПВД на разных этапах
    hire = add_workdays(today, -8)
    plan = _plan("Иванов Иван Иванович", "Менеджер по продажам", "Отдел прямых продаж", hire, ST_WORK, recruiter, petrov, sidorova, ivanov)
    _goals(plan, hire)
    plan.acknowledged_at = datetime.now() - timedelta(days=7)
    for task in plan.tasks:
        if task.term == "Первый день" and task.group == "must":
            task.status = "done"
    plan_svc.submit_survey(plan, "day1", DAY1_ANSWERS, ivanov.short_name)
    plan_svc.submit_survey(plan, "week1", WEEK1_ANSWERS, ivanov.short_name)
    audit("ПВД согласован и отправлен сотруднику", petrov.short_name, plan, kind="mgr")

    hire = add_workdays(today, -25)
    plan = _plan("Панов Роман Викторович", "Оператор станка", "Цех №4", hire, ST_WORK, recruiter, orlov, ershov, panov, family="prod")
    _goals(plan, hire)
    for task in plan.tasks[:2]:
        task.status = "done"
    plan.tasks[3].returned_count = 2

    hire = add_workdays(today, 5)
    _goals(_plan("Кузнецов Дмитрий Васильевич", "Мастер участка", "Цех №2", hire, ST_APPROVAL, recruiter, orlov, ershov, family="prod"), hire)
    _plan("Смирнова Ольга Андреевна", "Менеджер по логистике", "Отдел логистики", add_workdays(today, 7), ST_WAIT_GOALS, recruiter, petrov, family="logi", library=False)
    _plan("Морозов Алексей Сергеевич", "Кладовщик", "Отдел логистики", add_workdays(today, 12), ST_DRAFT, recruiter, family="logi", std=False, library=False, source="potok", external_id="demo-potok-1")
    db.session.flush()

    # История для аналитики
    for fio, position, otd, mentor, rec, ago, outcome, days, pct, over, risk, react, factors in HISTORY:
        department, site, manager = OTD[otd]
        hired = today - timedelta(days=ago)
        db.session.add(AdaptationRecord(fio=fio, position=position, otd=otd, department=department, site=site, manager=manager, mentor=mentor, recruiter=rec, hire_date=hired,
                                        left_date=hired + timedelta(days=days) if outcome in catalog.OUT_LEFT else None, outcome=outcome, days=days, plan_pct=pct, overdue=over, risk=risk,
                                        reaction_days=react, factors=factors))

    # ИПР
    ipr = Ipr(fio="Соколова Татьяна Игоревна", position="Специалист по снабжению", department="Департамент снабжения", upr="Управление закупок", otd="Отдел снабжения", site="Головной офис",
              email=sokolova.email, phone="+7 (900) 000-00-01", experience="4 года 2 месяца", target="Руководитель отдела снабжения", category="head", initiator="Руководитель",
              position_holder="Кротов Дмитрий Сергеевич", applied_date=today - timedelta(days=120), status=IPR_WORK, reservist=sokolova, manager=petrov, manager_name=petrov.short_name, manager_email=petrov.email,
              mentor=sidorova, mentor_name=sidorova.short_name, mentor_email=sidorova.email, tq_sent=today - timedelta(days=110), tq_done=today - timedelta(days=100),
              tq_rows=[["Управление командой и постановка задач", 3.1, 4.5], ["Ведение переговоров", 4.2, 4.5], ["Делегирование", 2.4, 4.0]])
    db.session.add(ipr)
    db.session.flush()
    ipr_svc.set_start_date(ipr, today - timedelta(days=90))
    for title, selected in [("Категорийное управление закупками", True), ("Бюджетирование закупок и контроль себестоимости", True), ("Анализ рынка поставщиков", False)]:
        db.session.add(IprSkill(ipr=ipr, kind="hard", title=title, selected=selected, rationale="Из должностной инструкции"))
    for title, selected in [("Управление командой и постановка задач", True), ("Делегирование", True), ("Системное мышление", False)]:
        db.session.add(IprSkill(ipr=ipr, kind="soft", title=title, selected=selected))
    for title, pct, assigned in [("Школа руководителя: базовый курс", 10, True), ("Делегирование", 0, True)]:
        db.session.add(IprCourse(ipr=ipr, title=title, pct=pct, assigned=assigned))
    db.session.flush()
    ipr_svc.add_default_practices(ipr)
    db.session.flush()
    first_practice = IprPractice.query.filter_by(ipr_id=ipr.id).first()
    first_practice.pct, first_practice.status = 20, "work"

    db.session.add(Ipr(fio="Миронов Артём Сергеевич", position="Инженер-технолог", department="Производственный департамент", otd="Цех №2", site="Север", target="Начальник участка", category="itr",
                       initiator="Сотрудник", applied_date=today - timedelta(days=60), status=IPR_DRAFT, manager=orlov, manager_name=orlov.short_name, experience="3 года"))
    db.session.add(IprCandidate(fio="Ковалёва Дарья Сергеевна", position="Специалист по закупкам", department="Департамент снабжения", upr="Управление закупок", otd="Отдел закупок", manager_name="Кротов Д. С.",
                                target="Руководитель группы закупок", email="candidate1@example.com", experience="3 года", site="Головной офис", motive="Три года веду закупки. Хочу развиваться в управлении командой."))
    db.session.add(IprCandidate(fio="Сорокин Максим Игоревич", position="Мастер участка", department="Производственный департамент", otd="Цех №1", manager_name="Орлов С. И.", target="Начальник цеха",
                                email="candidate2@example.com", experience="5 лет", site="Север", initiator="Руководитель", motive="Стабильно выполняет план участка, наставник для новых мастеров."))
    db.session.commit()
    return [(catalog_label(u), u.email) for u in (learning, recruiter, petrov, sidorova, ivanov, sokolova, volkov)]


def catalog_label(user: User) -> str:
    return user.role_label

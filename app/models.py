"""Модель данных: пользователи, ПВД, сигналы, ИПР, журнал и уведомления."""
from datetime import datetime

from .extensions import db

# Роли
RECRUITER = "recruiter"
MANAGER = "manager"
NEWBIE = "newbie"
LEARNING = "learning"
MENTOR = "mentor"
RESERVIST = "reservist"
TOP = "top"

ROLE_LABELS = {
    RECRUITER: "Менеджер по подбору",
    MANAGER: "Руководитель",
    NEWBIE: "Новичок",
    LEARNING: "Менеджер по обучению",
    MENTOR: "Наставник",
    RESERVIST: "Резервист",
    TOP: "Руководитель управления / зам. ГД",
}

# Статусы ПВД
ST_DRAFT = "Черновик"
ST_WAIT_GOALS = "Ожидает целей"
ST_APPROVAL = "На согласовании"
ST_WORK = "В работе"
ST_PASSED = "Завершён: ИС пройден"
ST_FAILED = "Завершён: ИС не пройден"
PLAN_STATUSES = [ST_DRAFT, ST_WAIT_GOALS, ST_APPROVAL, ST_WORK, ST_PASSED, ST_FAILED]

# Статусы ИПР
IPR_DRAFT = "Черновик"
IPR_APPROVAL = "На согласовании у руководителя"
IPR_APPROVED = "Утверждён руководителем"
IPR_WORK = "В работе"
IPR_RETURNED = "Возвращён на доработку"
IPR_DONE = "Завершён"
IPR_STATUSES = [IPR_DRAFT, IPR_APPROVAL, IPR_APPROVED, IPR_WORK, IPR_RETURNED, IPR_DONE]


class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(200), unique=True, nullable=False)
    full_name = db.Column(db.String(200), nullable=False)
    short_name = db.Column(db.String(100), nullable=False)
    role = db.Column(db.String(20), nullable=False)
    password_hash = db.Column(db.String(300), nullable=False)
    active = db.Column(db.Boolean, default=True, nullable=False)
    failed_logins = db.Column(db.Integer, default=0, nullable=False)
    locked_until = db.Column(db.DateTime)

    @property
    def role_label(self):
        return ROLE_LABELS.get(self.role, self.role)


class Setting(db.Model):
    key = db.Column(db.String(60), primary_key=True)
    value = db.Column(db.Text, nullable=False, default="")


class Position(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    family = db.Column(db.String(30), nullable=False)


class LibraryTask(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    family = db.Column(db.String(30), nullable=False)
    kind = db.Column(db.String(20), nullable=False, default="prof")
    title = db.Column(db.String(300), nullable=False)
    description = db.Column(db.Text, default="")
    fmt = db.Column(db.String(60), default="Документ")
    term = db.Column(db.String(60), default="Первая неделя")
    responsible = db.Column(db.String(120), default="Наставник")
    algorithm = db.Column(db.JSON, default=list, nullable=False)


class Plan(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    fio = db.Column(db.String(200), nullable=False)
    position = db.Column(db.String(200), default="")
    department = db.Column(db.String(200), default="")
    upr = db.Column(db.String(200), default="")
    otd = db.Column(db.String(200), default="")
    site = db.Column(db.String(120), default="")
    family = db.Column(db.String(30), default="")
    hire_date = db.Column(db.Date)
    probation_end = db.Column(db.Date)
    status = db.Column(db.String(40), default=ST_DRAFT, nullable=False, index=True)
    source = db.Column(db.String(20), default="manual")
    external_id = db.Column(db.String(100), unique=True)
    employee_id = db.Column(db.ForeignKey("user.id"))
    manager_id = db.Column(db.ForeignKey("user.id"))
    mentor_id = db.Column(db.ForeignKey("user.id"))
    recruiter_id = db.Column(db.ForeignKey("user.id"))
    manager_name = db.Column(db.String(200), default="")
    manager_email = db.Column(db.String(200), default="")
    mentor_name = db.Column(db.String(200), default="")
    mentor_email = db.Column(db.String(200), default="")
    created_at = db.Column(db.DateTime, default=datetime.now)
    status_changed_at = db.Column(db.DateTime, default=datetime.now)
    goals_written_at = db.Column(db.DateTime)
    return_comment = db.Column(db.Text, default="")
    acknowledged_at = db.Column(db.DateTime)
    newbie_questions = db.Column(db.Text, default="")
    m1_done_at = db.Column(db.DateTime)
    final_at = db.Column(db.DateTime)
    dismissal_date = db.Column(db.Date)
    dismissal_initiator = db.Column(db.String(60), default="")
    dismissal_reason = db.Column(db.String(120), default="")
    dismissal_comment = db.Column(db.Text, default="")
    kpi_draft = db.Column(db.JSON)

    employee = db.relationship("User", foreign_keys=[employee_id])
    manager = db.relationship("User", foreign_keys=[manager_id])
    mentor = db.relationship("User", foreign_keys=[mentor_id])
    recruiter = db.relationship("User", foreign_keys=[recruiter_id])
    tasks = db.relationship("PlanTask", backref="plan", cascade="all, delete-orphan", order_by="PlanTask.position")
    goals = db.relationship("Goal", backref="plan", cascade="all, delete-orphan", order_by="Goal.id")
    surveys = db.relationship("SurveyResponse", backref="plan", cascade="all, delete-orphan")
    signals = db.relationship("Signal", backref="plan", cascade="all, delete-orphan", order_by="Signal.id.desc()")


class PlanTask(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    plan_id = db.Column(db.ForeignKey("plan.id"), nullable=False, index=True)
    group = db.Column(db.String(20), nullable=False, default="ind")  # must, pulse, meet, ind, jd, mgr, extra
    source = db.Column(db.String(20), default="library")  # library, jd, kpi, manual
    source_note = db.Column(db.String(200), default="")
    title = db.Column(db.String(300), nullable=False)
    description = db.Column(db.Text, default="")
    fmt = db.Column(db.String(60), default="Документ")
    term = db.Column(db.String(60), default="")
    due_date = db.Column(db.Date)
    responsible = db.Column(db.String(120), default="")
    algorithm = db.Column(db.JSON, default=list, nullable=False)
    position = db.Column(db.Integer, default=0)
    status = db.Column(db.String(20), default="plan", nullable=False)  # plan, wait, blocked, done
    report_comment = db.Column(db.Text, default="")
    report_link = db.Column(db.String(500), default="")
    reported_at = db.Column(db.DateTime)
    review_comment = db.Column(db.Text, default="")
    returned_count = db.Column(db.Integer, default=0, nullable=False)


class Goal(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    plan_id = db.Column(db.ForeignKey("plan.id"), nullable=False, index=True)
    title = db.Column(db.String(400), nullable=False, default="")
    description = db.Column(db.Text, default="")
    result = db.Column(db.Text, default="")
    due_date = db.Column(db.Date)
    origin = db.Column(db.String(20), default="manual")  # manual, kpi, jd
    source_note = db.Column(db.String(300), default="")
    meta = db.Column(db.JSON)
    status = db.Column(db.String(20), default="plan", nullable=False)  # plan, wait, blocked, done, partial, fail
    report_comment = db.Column(db.Text, default="")
    report_link = db.Column(db.String(500), default="")
    reported_at = db.Column(db.DateTime)
    review_comment = db.Column(db.Text, default="")


class SurveyResponse(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    plan_id = db.Column(db.ForeignKey("plan.id"), nullable=False, index=True)
    survey_key = db.Column(db.String(20), nullable=False)
    answers = db.Column(db.JSON, nullable=False)
    submitted_at = db.Column(db.DateTime, default=datetime.now)
    __table_args__ = (db.UniqueConstraint("plan_id", "survey_key"),)


class Signal(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    plan_id = db.Column(db.ForeignKey("plan.id"), nullable=False, index=True)
    source = db.Column(db.String(20), nullable=False)  # pulse1, pulse7, m1, risk, help
    level = db.Column(db.String(20), nullable=False, default="средний")
    title = db.Column(db.String(400), nullable=False)
    detail = db.Column(db.Text, default="")
    evidence = db.Column(db.JSON, default=list)
    recommendation = db.Column(db.Text, default="")
    status = db.Column(db.String(20), default="new", nullable=False, index=True)  # new, work, done, closed, returned
    created_at = db.Column(db.DateTime, default=datetime.now)
    deadline = db.Column(db.Date)
    owner_id = db.Column(db.ForeignKey("user.id"))
    owner_role = db.Column(db.String(30), default="")
    action_plan = db.Column(db.Text, default="")
    action_due = db.Column(db.Date)
    plan_at = db.Column(db.DateTime)
    result = db.Column(db.Text, default="")
    result_at = db.Column(db.DateTime)
    closed_by_id = db.Column(db.ForeignKey("user.id"))
    closed_at = db.Column(db.DateTime)
    return_comment = db.Column(db.Text, default="")
    repkey = db.Column(db.String(40))

    owner = db.relationship("User", foreign_keys=[owner_id])
    closed_by = db.relationship("User", foreign_keys=[closed_by_id])


class AdaptationRecord(db.Model):
    """Строка аналитики: одна на сотрудника. Живые ПВД обновляются автоматически, история загружается отдельно."""

    id = db.Column(db.Integer, primary_key=True)
    plan_id = db.Column(db.ForeignKey("plan.id"), unique=True)
    fio = db.Column(db.String(200), nullable=False)
    position = db.Column(db.String(200), default="")
    otd = db.Column(db.String(200), default="")
    department = db.Column(db.String(200), default="")
    site = db.Column(db.String(120), default="")
    manager = db.Column(db.String(200), default="")
    mentor = db.Column(db.String(200), default="")
    recruiter = db.Column(db.String(200), default="")
    hire_date = db.Column(db.Date)
    left_date = db.Column(db.Date)
    outcome = db.Column(db.String(40), default="Работает")
    days = db.Column(db.Integer, default=0)
    plan_pct = db.Column(db.Integer, default=0)
    overdue = db.Column(db.Integer, default=0)
    risk = db.Column(db.Integer, default=0)
    reaction_days = db.Column(db.Integer)
    factors = db.Column(db.JSON, default=list)


class AuditLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    ts = db.Column(db.DateTime, default=datetime.now, index=True)
    plan_id = db.Column(db.ForeignKey("plan.id"), index=True)
    ipr_id = db.Column(db.ForeignKey("ipr.id"), index=True)
    actor = db.Column(db.String(200), default="Система")
    text = db.Column(db.Text, nullable=False)
    kind = db.Column(db.String(20), default="act")


class Notification(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.ForeignKey("user.id"), nullable=False, index=True)
    subject = db.Column(db.String(300), nullable=False)
    body = db.Column(db.Text, default="")
    link = db.Column(db.String(300), default="")
    created_at = db.Column(db.DateTime, default=datetime.now)
    read_at = db.Column(db.DateTime)
    dedupe_key = db.Column(db.String(200), index=True)
    emailed_at = db.Column(db.DateTime)


class IprCandidate(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    submitted_at = db.Column(db.DateTime, default=datetime.now)
    external_id = db.Column(db.String(100), unique=True)
    fio = db.Column(db.String(200), nullable=False)
    position = db.Column(db.String(200), default="")
    department = db.Column(db.String(200), default="")
    upr = db.Column(db.String(200), default="")
    otd = db.Column(db.String(200), default="")
    site = db.Column(db.String(120), default="")
    manager_name = db.Column(db.String(200), default="")
    target = db.Column(db.String(200), default="")
    email = db.Column(db.String(200), default="")
    phone = db.Column(db.String(60), default="")
    experience = db.Column(db.String(60), default="")
    motive = db.Column(db.Text, default="")
    initiator = db.Column(db.String(60), default="Сотрудник")
    status = db.Column(db.String(20), default="new", nullable=False)  # new, mgr_ok, mgr_no, lnd_no
    comment = db.Column(db.Text, default="")


class Ipr(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    fio = db.Column(db.String(200), nullable=False)
    position = db.Column(db.String(200), default="")
    department = db.Column(db.String(200), default="")
    upr = db.Column(db.String(200), default="")
    otd = db.Column(db.String(200), default="")
    site = db.Column(db.String(120), default="")
    email = db.Column(db.String(200), default="")
    phone = db.Column(db.String(60), default="")
    experience = db.Column(db.String(60), default="")
    target = db.Column(db.String(200), default="")
    category = db.Column(db.String(10), default="line")  # line, itr, head
    initiator = db.Column(db.String(60), default="")
    position_holder = db.Column(db.String(200), default="")
    applied_date = db.Column(db.Date)
    start_date = db.Column(db.Date)
    status = db.Column(db.String(40), default=IPR_DRAFT, nullable=False, index=True)
    return_comment = db.Column(db.Text, default="")
    tq_sent = db.Column(db.Date)
    tq_done = db.Column(db.Date)
    tq_rows = db.Column(db.JSON, default=list)
    manager_name = db.Column(db.String(200), default="")
    manager_email = db.Column(db.String(200), default="")
    mentor_name = db.Column(db.String(200), default="")
    mentor_email = db.Column(db.String(200), default="")
    reservist_id = db.Column(db.ForeignKey("user.id"))
    manager_id = db.Column(db.ForeignKey("user.id"))
    mentor_id = db.Column(db.ForeignKey("user.id"))
    created_at = db.Column(db.DateTime, default=datetime.now)

    reservist = db.relationship("User", foreign_keys=[reservist_id])
    manager = db.relationship("User", foreign_keys=[manager_id])
    mentor = db.relationship("User", foreign_keys=[mentor_id])
    skills = db.relationship("IprSkill", backref="ipr", cascade="all, delete-orphan", order_by="IprSkill.id")
    courses = db.relationship("IprCourse", backref="ipr", cascade="all, delete-orphan", order_by="IprCourse.id")
    practices = db.relationship("IprPractice", backref="ipr", cascade="all, delete-orphan", order_by="IprPractice.id")
    meetings = db.relationship("IprMeeting", backref="ipr", cascade="all, delete-orphan", order_by="IprMeeting.id")
    books = db.relationship("IprBook", backref="ipr", cascade="all, delete-orphan", order_by="IprBook.id")


class IprSkill(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    ipr_id = db.Column(db.ForeignKey("ipr.id"), nullable=False, index=True)
    kind = db.Column(db.String(10), default="hard")  # hard, soft
    title = db.Column(db.String(300), nullable=False)
    rationale = db.Column(db.Text, default="")
    selected = db.Column(db.Boolean, default=False, nullable=False)
    mastery = db.Column(db.String(10), default="")  # yes, part, no
    mastery_note = db.Column(db.Text, default="")


class IprCourse(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    ipr_id = db.Column(db.ForeignKey("ipr.id"), nullable=False, index=True)
    title = db.Column(db.String(300), nullable=False)
    url = db.Column(db.String(500), default="")
    pct = db.Column(db.Integer, default=0, nullable=False)
    assigned = db.Column(db.Boolean, default=False, nullable=False)


class IprPractice(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    ipr_id = db.Column(db.ForeignKey("ipr.id"), nullable=False, index=True)
    title = db.Column(db.Text, nullable=False)
    result = db.Column(db.Text, default="")
    due_date = db.Column(db.Date)
    pct = db.Column(db.Integer, default=0, nullable=False)
    status = db.Column(db.String(10), default="plan", nullable=False)  # plan, work, done, conf, na
    na_reason = db.Column(db.Text, default="")
    comments = db.Column(db.JSON, default=list)
    replaces_id = db.Column(db.Integer)
    is_ai = db.Column(db.Boolean, default=False, nullable=False)


class IprMeeting(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    ipr_id = db.Column(db.ForeignKey("ipr.id"), nullable=False, index=True)
    kind = db.Column(db.String(10), nullable=False)  # first, m2, bf, fin
    planned_date = db.Column(db.Date)
    moved_date = db.Column(db.Date)
    status = db.Column(db.String(10), default="plan", nullable=False)  # plan, set, done, fail
    note = db.Column(db.Text, default="")

    @property
    def effective_date(self):
        return self.moved_date or self.planned_date


class IprBook(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    ipr_id = db.Column(db.ForeignKey("ipr.id"), nullable=False, index=True)
    author = db.Column(db.String(200), default="")
    title = db.Column(db.String(300), nullable=False)
    skill = db.Column(db.String(300), default="")

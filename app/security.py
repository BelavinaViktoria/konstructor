"""Вход, роли и проверка доступа к конкретным записям."""
from functools import wraps

from flask import abort, g, redirect, request, session, url_for

from .extensions import db
from .models import LEARNING, MANAGER, MENTOR, NEWBIE, RECRUITER, RESERVIST, TOP, User


def load_user() -> None:
    uid = session.get("uid")
    user = db.session.get(User, uid) if uid else None
    g.user = user if user and user.active else None


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not g.user:
            return redirect(url_for("auth.login", next=request.path))
        return view(*args, **kwargs)
    return wrapped


def roles(*allowed):
    def decorator(view):
        @wraps(view)
        @login_required
        def wrapped(*args, **kwargs):
            if g.user.role not in allowed:
                abort(403)
            return view(*args, **kwargs)
        return wrapped
    return decorator


def can_view_plan(user: User, plan) -> bool:
    if user.role in (RECRUITER, LEARNING):
        return True
    return user.id in (plan.manager_id, plan.mentor_id, plan.employee_id)


def can_view_ipr(user: User, ipr) -> bool:
    if user.role in (LEARNING, TOP):
        return True
    return user.id in (ipr.manager_id, ipr.mentor_id, ipr.reservist_id)


HOME = {
    RECRUITER: "pvd.desk", LEARNING: "pvd.overview", MANAGER: "pvd.manager_home", NEWBIE: "pvd.my_plan",
    MENTOR: "pvd.mentor_home", RESERVIST: "ipr.my", TOP: "ipr.top",
}

_PVD = "pvd"
_IPR = "ipr"
_NAV = {
    RECRUITER: [("Рабочий стол", "pvd.desk", _PVD), ("Создать ПВД", "pvd.plan_new", _PVD), ("Сигналы", "signals.index", _PVD), ("Аналитика", "analytics.index", _PVD)],
    LEARNING: [("Обзор", "pvd.overview", None), ("Рабочий стол", "pvd.desk", _PVD), ("Сигналы", "signals.index", _PVD), ("Аналитика", "analytics.index", _PVD),
               ("Кандидаты в резерв", "ipr.candidates", _IPR), ("Рабочий стол ИПР", "ipr.desk", _IPR), ("Создать ИПР", "ipr.new", _IPR),
               ("Состав резерва", "ipr.top", _IPR), ("Админ-панель", "admin.index", None)],
    MANAGER: [("Мои новички", "pvd.manager_home", _PVD), ("Сигналы", "signals.index", _PVD), ("Мои резервисты", "ipr.desk", _IPR)],
    NEWBIE: [("Мой план", "pvd.my_plan", _PVD)],
    MENTOR: [("Наставник ПВД", "pvd.mentor_home", _PVD), ("Сигналы", "signals.index", _PVD), ("Наставник ИПР", "ipr.desk", _IPR)],
    RESERVIST: [("Мой план развития", "ipr.my", _IPR)],
    TOP: [("Состав резерва", "ipr.top", _IPR), ("Аналитика", "analytics.index", _PVD)],
}


def nav_for(user) -> list:
    return _NAV.get(user.role, []) if user else []

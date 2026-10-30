"""Входящие интеграции. Доступ по ключу в заголовке X-API-Key; пустой ключ означает, что интеграция выключена."""
import hmac

from flask import Blueprint, abort, current_app, jsonify, request

from ..extensions import db
from ..models import IprCandidate, Plan, Position, User
from ..services import plans as svc
from ..services.dates import parse_date
from ..services.notify import audit

bp = Blueprint("api", __name__, url_prefix="/api")


def _check_key(config_key: str) -> None:
    expected = current_app.config.get(config_key, "")
    if not expected:
        abort(503, "Интеграция не настроена")
    if not hmac.compare_digest(request.headers.get("X-API-Key", ""), expected):
        abort(401)


def _payload() -> dict:
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        abort(400, "Нужен JSON-объект")
    return data


def _text(data: dict, key: str, limit: int = 200) -> str:
    return str(data.get(key) or "").strip()[:limit]


@bp.get("/health")
def health():
    return jsonify(status="ok")


@bp.post("/potok/offer")
def potok_offer():
    """«Принял оффер» в Потоке: на рабочем столе появляется черновик ПВД с заполненными полями."""
    _check_key("POTOK_API_KEY")
    data = _payload()
    external_id, fio = _text(data, "id", 100), _text(data, "fio")
    if not external_id or not fio:
        abort(400, "Обязательные поля: id, fio")
    existing = Plan.query.filter_by(external_id=external_id).first()
    if existing:
        return jsonify(id=existing.id, created=False)
    plan = Plan(fio=fio, position=_text(data, "position"), department=_text(data, "department"), upr=_text(data, "upr"), otd=_text(data, "otd"),
                site=_text(data, "site"), source="potok", external_id=external_id, manager_name=_text(data, "manager_name"), manager_email=_text(data, "manager_email").lower())
    db.session.add(plan)
    position = Position.query.filter_by(name=plan.position).first()
    plan.family = position.family if position else ""
    svc.set_hire_date(plan, parse_date(_text(data, "hire_date", 20)))
    if plan.manager_email:
        plan.manager = User.query.filter_by(email=plan.manager_email).first()
    db.session.flush()
    audit("Создан черновик ПВД из Потока: кандидат принял оффер", "Система", plan, kind="sys")
    db.session.commit()
    return jsonify(id=plan.id, created=True), 201


@bp.post("/forms/candidate")
def forms_candidate():
    """Анкета из Яндекс Формы попадает в реестр кандидатов в резерв."""
    _check_key("FORMS_API_KEY")
    data = _payload()
    external_id, fio = _text(data, "id", 100), _text(data, "fio")
    if not external_id or not fio:
        abort(400, "Обязательные поля: id, fio")
    existing = IprCandidate.query.filter_by(external_id=external_id).first()
    if existing:
        return jsonify(id=existing.id, created=False)
    candidate = IprCandidate(external_id=external_id, fio=fio, position=_text(data, "position"), department=_text(data, "department"), upr=_text(data, "upr"),
                             otd=_text(data, "otd"), site=_text(data, "site"), manager_name=_text(data, "manager_name"), target=_text(data, "target"),
                             email=_text(data, "email"), phone=_text(data, "phone", 60), experience=_text(data, "experience", 60),
                             motive=_text(data, "motive", 4000), initiator=_text(data, "initiator", 60) or "Сотрудник")
    db.session.add(candidate)
    db.session.commit()
    return jsonify(id=candidate.id, created=True), 201

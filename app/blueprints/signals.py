"""Сигналы о проблемах: план действий, выполнение, проверка."""
from datetime import datetime

from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for

from .. import catalog
from ..extensions import db
from ..models import LEARNING, MANAGER, MENTOR, RECRUITER, Plan, Signal
from ..security import can_view_plan, roles
from ..services.dates import fmt_date, parse_date
from ..services.notify import audit, notify

bp = Blueprint("signals", __name__, url_prefix="/signals")


def _signal(signal_id: int) -> Signal:
    signal = db.session.get(Signal, signal_id)
    if not signal:
        abort(404)
    if not can_view_plan(g.user, signal.plan):
        abort(403)
    return signal


def _visible_query():
    query = Signal.query.join(Plan, Signal.plan_id == Plan.id)
    if g.user.role == MANAGER:
        query = query.filter(Plan.manager_id == g.user.id)
    elif g.user.role == MENTOR:
        query = query.filter(Plan.mentor_id == g.user.id)
    return query


def _can_take(signal: Signal) -> bool:
    """План действий пишет руководитель или наставник этого сотрудника."""
    return signal.status in ("new", "returned") and g.user.id in (signal.plan.manager_id, signal.plan.mentor_id)


@bp.get("")
@roles(RECRUITER, LEARNING, MANAGER, MENTOR)
def index():
    tab = request.args.get("tab", "todo")
    query = _visible_query()
    plan_id = request.args.get("plan", type=int)
    term = request.args.get("q", "").strip()
    if plan_id:
        query = query.filter(Signal.plan_id == plan_id)
        tab = request.args.get("tab", "all")
    if term:
        query = query.filter(Plan.fio.ilike(f"%{term}%"))
    all_rows = query.order_by(Signal.created_at.desc()).all()
    tabs = []
    current = next((t for t in catalog.SIGNAL_TABS if t[0] == tab), catalog.SIGNAL_TABS[0])
    for key, label, statuses in catalog.SIGNAL_TABS:
        tabs.append((key, label, sum(statuses is None or s.status in statuses for s in all_rows)))
    rows = [s for s in all_rows if current[2] is None or s.status in current[2]]
    return render_template("signals/index.html", rows=rows, tabs=tabs, tab=current[0], q=term, plan_id=plan_id, can_take=_can_take, now=datetime.now().date())


@bp.post("/<int:signal_id>/take")
@roles(MANAGER, MENTOR)
def take(signal_id):
    signal = _signal(signal_id)
    if not _can_take(signal):
        abort(403)
    text, due = request.form.get("action_plan", "").strip(), parse_date(request.form.get("action_due"))
    if len(text) < 5 or not due:
        flash("Укажите план мероприятий и срок.", "error")
        return redirect(request.referrer or url_for("signals.index"))
    plan, as_mentor = signal.plan, g.user.role == MENTOR
    signal.status, signal.owner, signal.owner_role = "work", g.user, "Наставник" if as_mentor else "Руководитель"
    signal.action_plan, signal.action_due, signal.plan_at = text, due, datetime.now()
    audit(f"Взял сигнал «{signal.title}» в работу: {text[:120]}", g.user.short_name, plan, kind="mnt" if as_mentor else "mgr")
    if as_mentor:
        notify([plan.manager], "Наставник взял сигнал в работу: план мероприятий",
               f"{plan.fio}. Сигнал: {signal.title}. План наставника {g.user.short_name}: {text}. Срок: {fmt_date(due)}.", f"/signals?plan={plan.id}")
    notify([plan.recruiter], "Сигнал в работе", f"{plan.fio}: {signal.title}. Ответственный: {g.user.short_name}.", f"/signals?plan={plan.id}")
    db.session.commit()
    flash("Сигнал взят в работу." + (" Руководителю ушло уведомление с вашим планом." if as_mentor else ""), "ok")
    return redirect(url_for("signals.index", tab="work"))


@bp.post("/<int:signal_id>/done")
@roles(MANAGER, MENTOR)
def done(signal_id):
    signal = _signal(signal_id)
    if signal.status != "work" or signal.owner_id != g.user.id:
        abort(403)
    result = request.form.get("result", "").strip()
    if len(result) < 5:
        flash("Опишите результат.", "error")
        return redirect(request.referrer or url_for("signals.index"))
    signal.status, signal.result, signal.result_at = "done", result, datetime.now()
    audit(f"Отметил выполнение сигнала «{signal.title}»: {result[:120]}", g.user.short_name, signal.plan)
    notify([signal.plan.recruiter], "Сигнал ждёт вашей проверки", f"{signal.plan.fio}: {signal.title}", f"/signals?plan={signal.plan_id}")
    db.session.commit()
    flash("Отмечено выполненным: сигнал ушёл на проверку.", "ok")
    return redirect(url_for("signals.index", tab="done"))


@bp.post("/<int:signal_id>/close")
@roles(RECRUITER, LEARNING)
def close(signal_id):
    signal = _signal(signal_id)
    if signal.status != "done":
        abort(400)
    signal.status, signal.closed_by, signal.closed_at = "closed", g.user, datetime.now()
    audit(f"Закрыл сигнал «{signal.title}»", g.user.short_name, signal.plan, kind="rec")
    db.session.commit()
    flash("Сигнал закрыт.", "ok")
    return redirect(url_for("signals.index", tab="closed"))


@bp.post("/<int:signal_id>/return")
@roles(RECRUITER, LEARNING)
def return_signal(signal_id):
    signal = _signal(signal_id)
    comment = request.form.get("comment", "").strip()
    if signal.status != "done" or not comment:
        flash("Укажите, что доработать.", "error")
        return redirect(request.referrer or url_for("signals.index"))
    signal.status, signal.return_comment = "returned", comment
    audit(f"Вернул сигнал «{signal.title}» в работу: {comment}", g.user.short_name, signal.plan, kind="rec")
    notify([signal.owner], "Сигнал возвращён в работу", f"{signal.plan.fio}: {comment}", f"/signals?plan={signal.plan_id}")
    db.session.commit()
    return redirect(url_for("signals.index"))

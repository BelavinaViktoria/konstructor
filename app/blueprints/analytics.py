"""Аналитика и отчётность."""
from flask import Blueprint, Response, render_template, request

from .. import catalog
from ..models import LEARNING, RECRUITER, TOP
from ..security import roles
from ..services import ai
from ..services import analytics as svc

bp = Blueprint("analytics", __name__, url_prefix="/analytics")


def _filters() -> tuple[dict, str]:
    preset = request.args.get("preset", "")
    f = svc.preset_filters(preset)
    if not preset:
        f.update({k: request.args.get(k, "") for k in f})
        f["date_by"] = "left" if f["date_by"] == "left" else "hire"
    return f, preset or "all"


def _data() -> dict:
    svc.sync_records()
    rows = svc.all_rows()
    f, preset = _filters()
    selected = svc.apply_filters(rows, f)
    base = svc.apply_filters(rows, {**f, "out": "", "lvl": "", "over": "", "date_by": "hire"})
    return {"filters": f, "preset": preset, "rows": selected, "base": base, "all_rows": rows}


@bp.get("")
@roles(RECRUITER, LEARNING, TOP)
def index():
    d = _data()
    options = {key: sorted({r[key] for r in d["all_rows"] if r[key]}) for key, _ in svc.SELECT_FIELDS}
    findings, summary = None, None
    if request.args.get("analyze"):
        findings = svc.analyze(d["rows"], d["base"])
        summary = ai.analytics_summary(findings, {"в выборке": len(d["rows"]), "ушли": sum(r["gone"] for r in d["rows"])})
    return render_template("analytics/index.html", presets=catalog.ANALYTICS_PRESETS, fields=svc.SELECT_FIELDS, options=options, stats=svc.stats(d["rows"], d["base"]),
                           bars_otd=svc.group_bars(d["rows"], "otd"), bars_manager=svc.group_bars(d["rows"], "manager"), findings=findings, summary=summary,
                           query=request.query_string.decode(), **{k: d[k] for k in ("filters", "preset", "rows")})


@bp.get("/export.csv")
@roles(RECRUITER, LEARNING, TOP)
def export_csv():
    d = _data()
    return Response(svc.to_csv(d["rows"]), mimetype="text/csv; charset=utf-8", headers={"Content-Disposition": "attachment; filename=adaptation_report.csv"})


@bp.get("/report")
@roles(RECRUITER, LEARNING, TOP)
def report():
    d = _data()
    return render_template("analytics/report.html", rows=d["rows"], findings=svc.analyze(d["rows"], d["base"]), stats=svc.stats(d["rows"], d["base"]), filters=d["filters"])

"""Конструктор ПВД и ИПР: фабрика приложения."""
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, g, render_template

from . import catalog
from .config import BASE_DIR, load_config
from .extensions import csrf, db
from .models import ROLE_LABELS, Notification
from .security import load_user, nav_for
from .services.dates import fmt_date


def create_app(overrides: dict | None = None) -> Flask:
    load_dotenv(BASE_DIR / ".env")
    app = Flask(__name__, instance_path=str(BASE_DIR / "instance"))
    app.config.update(load_config())
    app.config.update(overrides or {})
    Path(app.instance_path).mkdir(parents=True, exist_ok=True)

    db.init_app(app)
    csrf.init_app(app)

    from .blueprints import admin, analytics, api, auth, ipr, pvd, signals

    for module in (auth, pvd, signals, analytics, ipr, admin, api):
        app.register_blueprint(module.bp)
    csrf.exempt(api.bp)

    app.before_request(load_user)
    app.jinja_env.filters["d"] = fmt_date

    @app.context_processor
    def inject():
        user = g.get("user")
        unread = Notification.query.filter_by(user_id=user.id, read_at=None).count() if user else 0
        return {"cat": catalog, "current_user": user, "nav": nav_for(user), "unread": unread, "ROLE_LABELS": ROLE_LABELS,
                "company": app.config["COMPANY_NAME"], "is_demo": app.config["APP_ENV"] != "production"}

    @app.after_request
    def security_headers(response):
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        response.headers.setdefault("Content-Security-Policy",
                                    "default-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; form-action 'self'; frame-ancestors 'none'")
        return response

    @app.errorhandler(403)
    def forbidden(_):
        return render_template("error.html", code=403, text="У вашей роли нет доступа к этой странице."), 403

    @app.errorhandler(404)
    def not_found(_):
        return render_template("error.html", code=404, text="Страница не найдена."), 404

    @app.errorhandler(413)
    def too_large(_):
        return render_template("error.html", code=413, text="Файл слишком большой: максимум 10 МБ."), 413

    from .cli import register_cli

    register_cli(app)
    return app

"""Команды: flask init-db, seed-catalog, create-admin, seed-demo, daily-tick."""
import secrets

import click
from flask import current_app
from werkzeug.security import generate_password_hash

from .extensions import db
from .models import LEARNING, User


def register_cli(app) -> None:
    @app.cli.command("init-db")
    def init_db():
        """Создаёт таблицы и справочники."""
        from .seed import seed_catalog

        db.create_all()
        seed_catalog()
        click.echo("База данных готова.")

    @app.cli.command("create-admin")
    @click.option("--email", prompt=True)
    @click.option("--name", prompt="ФИО")
    @click.password_option()
    def create_admin(email, name, password):
        """Создаёт менеджера по обучению: у этой роли есть админ-панель."""
        from .blueprints.admin import short_name

        db.session.add(User(email=email.strip().lower(), full_name=name, short_name=short_name(name), role=LEARNING,
                            password_hash=generate_password_hash(password, method=current_app.config["PASSWORD_HASH_METHOD"])))
        db.session.commit()
        click.echo("Пользователь создан.")

    @app.cli.command("seed-demo")
    @click.option("--password", default=None, help="Пароль демо-пользователей. Если не задан, будет создан случайный.")
    def seed_demo_command(password):
        """Заполняет базу вымышленными данными. На сервере не запускается."""
        from .seed import seed_catalog, seed_demo

        if current_app.config["APP_ENV"] == "production":
            raise click.ClickException("Демо-данные нельзя загружать при APP_ENV=production.")
        db.create_all()
        seed_catalog()
        password = password or secrets.token_urlsafe(9)
        accounts = seed_demo(password)
        click.echo(f"Пароль всех демо-пользователей: {password}")
        for label, email in accounts:
            click.echo(f"  {label}: {email}")

    @app.cli.command("daily-tick")
    def daily_tick():
        """Ежедневная проверка сроков и рисков. Запускайте планировщиком раз в день."""
        from .services import daily

        click.echo(daily.run())

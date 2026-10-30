import pytest
from werkzeug.security import generate_password_hash

from app import create_app
from app.extensions import db
from app.models import LEARNING, MANAGER, MENTOR, NEWBIE, RECRUITER, RESERVIST, TOP, User
from app.seed import seed_catalog

PASSWORD = "test-password-123"
EMAILS = {RECRUITER: "rec@example.com", LEARNING: "lnd@example.com", MANAGER: "mgr@example.com", MENTOR: "mnt@example.com",
          NEWBIE: "new@example.com", RESERVIST: "res@example.com", TOP: "top@example.com"}


@pytest.fixture
def app():
    app = create_app({"TESTING": True, "SECRET_KEY": "test", "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:", "WTF_CSRF_ENABLED": False, "APP_ENV": "testing",
                      "SMTP_HOST": "", "ANTHROPIC_API_KEY": "", "POTOK_API_KEY": "potok-key", "FORMS_API_KEY": "forms-key", "PASSWORD_HASH_METHOD": "pbkdf2:sha256:1000"})
    with app.app_context():
        db.create_all()
        seed_catalog()
        pw = generate_password_hash(PASSWORD, method="pbkdf2:sha256:1000")
        for role, email in EMAILS.items():
            db.session.add(User(email=email, full_name=f"Тестов {role.title()} Иванович", short_name=f"Тестов {role[0].upper()}.", role=role, password_hash=pw))
        db.session.add(User(email="mgr2@example.com", full_name="Другой Руководитель Петрович", short_name="Другой Р. П.", role=MANAGER, password_hash=pw))
        db.session.commit()
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def login(client):
    def _login(role_or_email):
        client.post("/logout")
        email = EMAILS.get(role_or_email, role_or_email)
        response = client.post("/login", data={"email": email, "password": PASSWORD})
        assert response.status_code == 302
        return client
    return _login


def user(role):
    return User.query.filter_by(email=EMAILS[role]).first()

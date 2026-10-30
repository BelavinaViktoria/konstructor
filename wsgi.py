"""Точка входа: `python wsgi.py` запускает приложение через waitress."""
import os

from app import create_app

app = create_app()

if __name__ == "__main__":
    from waitress import serve

    serve(app, host=os.environ.get("HOST", "127.0.0.1"), port=int(os.environ.get("PORT", "5000")))

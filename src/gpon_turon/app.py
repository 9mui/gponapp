import logging
import threading
import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from flask import Flask, jsonify

from .db import close_db, init_db
from .routes import olts_bp, onu_bp
from .routes.olts import run_refresh_all_once
from .settings import Settings

_auto_refresh_lock = threading.Lock()
_auto_refresh_started = False
_TZ_TASHKENT = ZoneInfo("Asia/Tashkent")


def _to_tashkent(value) -> str:
    if value in (None, ""):
        return "-"
    if isinstance(value, datetime):
        dt = value
    else:
        txt = str(value).strip()
        try:
            dt = datetime.fromisoformat(txt)
        except ValueError:
            return txt

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(_TZ_TASHKENT).strftime("%Y-%m-%d %H:%M:%S")


def create_app() -> Flask:
    settings = Settings.from_env()

    if not logging.getLogger().handlers:
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        )

    app = Flask(
        __name__,
        template_folder=str(settings.base_dir / "templates"),
        static_folder=str(settings.base_dir / "static"),
    )
    app.config["SECRET_KEY"] = settings.secret_key
    app.config["SETTINGS"] = settings
    app.jinja_env.filters["tz_tashkent"] = _to_tashkent

    init_db(settings.db_path, settings.schema_path)
    app.teardown_appcontext(lambda exc: close_db())
    app.register_blueprint(olts_bp)
    app.register_blueprint(onu_bp)
    _start_auto_refresh_thread(app, settings)

    @app.get("/health")
    def health():
        return jsonify(
            {
                "status": "ok",
                "app_env": settings.app_env,
                "db_path": str(settings.db_path),
            }
        )

    return app


def _start_auto_refresh_thread(app: Flask, settings: Settings) -> None:
    global _auto_refresh_started
    if app.testing or not settings.auto_refresh_enabled:
        return

    with _auto_refresh_lock:
        if _auto_refresh_started:
            return
        _auto_refresh_started = True

    interval_sec = max(60, settings.auto_refresh_interval_minutes * 60)
    logger = logging.getLogger(__name__)

    def _worker():
        logger.info("auto_refresh thread started interval_minutes=%s", settings.auto_refresh_interval_minutes)
        while True:
            with app.app_context():
                started, total, ok_count, fail_count, _ = run_refresh_all_once()
                if not started:
                    logger.info("auto_refresh skipped: previous cycle still running")
                elif total > 0:
                    logger.info("auto_refresh cycle done total=%s success=%s failed=%s", total, ok_count, fail_count)
            time.sleep(interval_sec)

    t = threading.Thread(target=_worker, name="auto-refresh-olt", daemon=True)
    t.start()

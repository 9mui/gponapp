import os
from dataclasses import dataclass
from pathlib import Path

try:
    from dotenv import load_dotenv
except ModuleNotFoundError:  # Optional dependency in minimal environments
    def load_dotenv(*args, **kwargs):
        return False


BASE_DIR = Path(__file__).resolve().parents[2]
load_dotenv(BASE_DIR / ".env")


def _as_bool(value: str, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    base_dir: Path
    app_env: str
    host: str
    port: int
    debug: bool
    secret_key: str
    db_path: Path
    schema_path: Path
    snmp_timeout: float
    snmp_retries: int
    auto_refresh_enabled: bool
    auto_refresh_interval_minutes: int

    @classmethod
    def from_env(cls) -> "Settings":
        db_path = Path(os.getenv("DB_PATH", BASE_DIR / "instance" / "gpon_turon.db"))
        schema_path = Path(os.getenv("SCHEMA_PATH", BASE_DIR / "schema.sql"))
        return cls(
            base_dir=BASE_DIR,
            app_env=os.getenv("APP_ENV", "development"),
            host=os.getenv("APP_HOST", "0.0.0.0"),
            port=int(os.getenv("APP_PORT", "5001")),
            debug=_as_bool(os.getenv("APP_DEBUG"), default=True),
            secret_key=os.getenv("SECRET_KEY", "change-me-in-production"),
            db_path=db_path,
            schema_path=schema_path,
            snmp_timeout=float(os.getenv("SNMP_TIMEOUT", "1.0")),
            snmp_retries=int(os.getenv("SNMP_RETRIES", "1")),
            auto_refresh_enabled=_as_bool(os.getenv("AUTO_REFRESH_ENABLED"), default=True),
            auto_refresh_interval_minutes=int(os.getenv("AUTO_REFRESH_INTERVAL_MINUTES", "15")),
        )

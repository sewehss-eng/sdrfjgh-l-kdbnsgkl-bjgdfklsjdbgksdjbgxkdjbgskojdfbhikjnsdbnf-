"""
Конфигурация бота доступа к платным каналам.

Все настройки читаются из файла .env (он лежит рядом с config.py).
Можно и просто прописать значения прямо здесь — .env имеет приоритет,
если переменная в нём задана, она перезапишет значение из кода.
"""

from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
ENV_FILE = BASE_DIR / ".env"


def load_env(path: Path = ENV_FILE) -> None:
    """Минимальный загрузчик .env, чтобы не тянуть зависимость python-dotenv."""
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            os.environ[key] = value


load_env()


def _int_env(key: str, default: int) -> int:
    try:
        return int(os.getenv(key, "").strip())
    except (TypeError, ValueError):
        return default


# ─────────────────────────────────────────────
#  Telegram
# ─────────────────────────────────────────────
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()

# ID админов через запятую: 111111111,222222222
ADMIN_IDS: list[int] = [
    int(x)
    for x in os.getenv("ADMIN_IDS", "").replace(" ", "").split(",")
    if x.isdigit()
]

# ─────────────────────────────────────────────
#  База данных
# ─────────────────────────────────────────────
DB_FILE = os.getenv("DB_FILE", str(BASE_DIR / "bot.db"))
LOG_FILE = os.getenv("LOG_FILE", str(BASE_DIR / "logs" / "bot.log"))

# ─────────────────────────────────────────────
#  Поведение подписок
# ─────────────────────────────────────────────
DEFAULT_DAYS = _int_env("DEFAULT_DAYS", 30)          # быстрая кнопка «30 дней»
DEFAULT_WARN_DAYS = _int_env("DEFAULT_WARN_DAYS", 7)  # за сколько дней предупреждать
MAX_DAYS = _int_env("MAX_DAYS", 3650)

# Как часто бот проверяет истёкшие подписки (в минутах)
CHECK_INTERVAL_MINUTES = _int_env("CHECK_INTERVAL_MINUTES", 1)

# Часовой пояс для планировщика (например Europe/Moscow)
TIMEZONE = os.getenv("TIMEZONE", "UTC")

# ─────────────────────────────────────────────
#  Ссылки
# ─────────────────────────────────────────────
# Заявки на вступление: True — вход только после одобрения ботом
USE_JOIN_REQUESTS = os.getenv("USE_JOIN_REQUESTS", "1") not in ("0", "false", "False")
# Срок жизни персональной ссылки в секундах (0 = без срока)
INVITE_LINK_TTL = _int_env("INVITE_LINK_TTL", 0)
# Сколько раз по ссылке можно войти (1 = ссылка одноразовая)
INVITE_MEMBER_LIMIT = _int_env("INVITE_MEMBER_LIMIT", 1)


def validate() -> None:
    if not BOT_TOKEN:
        raise RuntimeError(
            "Не задан BOT_TOKEN. Создайте файл .env рядом с config.py "
            "или пропишите BOT_TOKEN в config.py"
        )
    if not ADMIN_IDS:
        raise RuntimeError(
            "Не заданы ADMIN_IDS (ID админов через запятую) — см. .env.example"
        )
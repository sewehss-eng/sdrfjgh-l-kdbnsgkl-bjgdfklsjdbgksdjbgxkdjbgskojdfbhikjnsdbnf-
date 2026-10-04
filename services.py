"""Сервис: приглашения, кики, общая ссылка канала."""

from __future__ import annotations

import logging
import secrets
from datetime import datetime, timedelta, timezone

from aiogram import Bot

from config import INVITE_MEMBER_LIMIT, USE_JOIN_REQUESTS
from database import db

log = logging.getLogger("access")

# Кеш общей ссылки канала: channel_db_id -> invite_link
_link_cache: dict[int, str] = {}


def now() -> datetime:
    return datetime.now(timezone.utc)


def _expires_delta(expires_iso: str):
    """Остаток времени до конца доступа (может быть отрицательным)."""
    exp = datetime.fromisoformat(expires_iso)
    if exp.tzinfo is None:
        exp = exp.replace(tzinfo=timezone.utc)
    return exp - now()


def days_left(expires_iso: str) -> int:
    """Дней до конца доступа для отображения (округление вверх, не меньше 0)."""
    delta = _expires_delta(expires_iso)
    if delta <= timedelta(0):
        return 0
    return -(-delta.total_seconds() // 86400)  # ceil


def is_expired(expires_iso: str) -> bool:
    """Точная проверка: срок закончился или нет (без суточного округления)."""
    return _expires_delta(expires_iso) <= timedelta(0)


def new_token() -> str:
    return secrets.token_urlsafe(10)[:16]


# ───────────────────────────  общая ссылка канала  ───────────────────────────


async def get_or_create_channel_link(bot: Bot, channel_chat_id: int, channel_db_id: int) -> str:
    """Возвращает ОДНУ постоянную общую ссылку канала.

    Сначала ищет уже сохранённую в БД (и кеше), создаёт новую только если её нет.
    """
    cached = _link_cache.get(channel_db_id)
    if cached:
        return cached

    # Пробуем взять сохранённую ссылку из БД
    row = await db.get_channel(channel_db_id)
    if row and row["invite_link"]:
        _link_cache[channel_db_id] = row["invite_link"]
        return row["invite_link"]

    try:
        chat = await bot.get_chat(channel_chat_id)
    except Exception as e:
        log.error("Не удалось получить канал %s: %s", channel_chat_id, e)
        raise

    try:
        # Telegram не разрешает member_limit у ссылок с заявкой на вступление.
        invite_kwargs = {
            "name": "CourseBot",
            "creates_join_request": USE_JOIN_REQUESTS,
        }
        if not USE_JOIN_REQUESTS:
            invite_kwargs["member_limit"] = INVITE_MEMBER_LIMIT or None

        invite = await bot.create_chat_invite_link(
            channel_chat_id,
            **invite_kwargs,
        )
        # Сохраняем в БД, чтобы бот не создавал ссылку заново после перезапуска
        await db.set_invite_link(channel_db_id, invite.invite_link)
        _link_cache[channel_db_id] = invite.invite_link
        return invite.invite_link
    except Exception as e:
        log.error("Не удалось создать ссылку для канала %s: %s", channel_chat_id, e)
        # fallback: username-ссылка
        if getattr(chat, "username", None):
            return f"https://t.me/{chat.username}"
        raise


async def invalidate_channel_link(channel_db_id: int) -> None:
    _link_cache.pop(channel_db_id, None)


# ─────────────────────────────  выдача доступа  ──────────────────────────────


def format_duration(days: int, duration_minutes: int | None = None) -> str:
    """Форматирует срок ссылки для сообщений пользователю и админу."""
    if duration_minutes is not None:
        if duration_minutes % 60 == 0:
            return f"{duration_minutes // 60} ч."
        return f"{duration_minutes} мин."
    return f"{days} дн."


async def grant(
    bot: Bot,
    user_id: int,
    channel_id: int,
    days: int = 0,
    duration_minutes: int | None = None,
) -> datetime:
    """Выдаёт/продлевает доступ; возвращает новую дату окончания."""
    row = await db.get_channel(channel_id)
    if not row:
        raise ValueError(f"Канал {channel_id} не найден")
    return await db.grant_access(user_id, channel_id, days, duration_minutes)


async def notify_grant(
    bot: Bot,
    user_id: int,
    channel_id: int,
    days: int,
    expires: datetime,
    duration_minutes: int | None = None,
) -> None:
    """Уведомляет пользователя о выдаче доступа со ссылкой на канал."""
    row = await db.get_channel(channel_id)
    try:
        link = await get_or_create_channel_link(bot, row["chat_id"], channel_id)
        text = (
            f"✅ <b>Доступ выдан!</b>\n"
            f"─────────────────────\n"
            f"🎓 {row['title']}\n"
            f"⏳ Продлён на <b>+{format_duration(days, duration_minutes)}</b>\n"
            f"📅 До: <b>{expires.strftime('%d.%m.%Y %H:%M')}</b>\n\n"
            f"👉 <a href=\"{link}\">Войти в канал</a>"
        )
        from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="🚀 В канал", url=link)],
                [InlineKeyboardButton(text="🏠 Главное меню", callback_data="home")],
            ]
        )
        await bot.send_message(user_id, text, reply_markup=kb, disable_web_page_preview=True)
    except Exception as e:
        log.error("Не удалось уведомить %s: %s", user_id, e)


# ───────────────────────────────  кик  ───────────────────────────────


async def kick(bot: Bot, user_id: int, channel_id: int) -> bool:
    row = await db.get_channel(channel_id)
    if not row:
        return False
    try:
        await bot.ban_chat_member(row["chat_id"], user_id)
        await bot.unban_chat_member(row["chat_id"], user_id)  # чистый кик
        await db.revoke_access(user_id, channel_id)
        log.info("Пользователь %s кикнут из канала %s", user_id, row["title"])
        return True
    except Exception as e:
        log.error("Ошибка кика %s из %s: %s", user_id, row["chat_id"], e)
        return False


# ─────────────────────────  обработка истечения  ─────────────────────────


async def process_expired(bot: Bot) -> tuple[int, int]:
    """Кикает всех с истёкшим доступом. Возвращает (удалено, живых)."""
    rows = await db.get_active_accesses()
    removed = 0
    alive = 0
    for a in rows:
        if is_expired(a["expires_at"]):
            ok = await kick(bot, a["user_id"], a["channel_id"])
            if ok:
                removed += 1
                try:
                    await bot.send_message(
                        a["user_id"],
                        f"❌ <b>Подписка истекла</b>\n─────────────────────\n"
                        f"{a['emoji']} {a['title']}\n"
                        f"Доступ закрыт. Свяжитесь с админом для продления.",
                    )
                except Exception:
                    pass
            else:
                alive += 1
        else:
            alive += 1
    return removed, alive

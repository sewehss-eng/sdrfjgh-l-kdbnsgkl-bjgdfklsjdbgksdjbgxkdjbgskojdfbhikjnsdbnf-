"""Планировщик: проверка истечения + напоминания."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from aiogram import Bot

from config import CHECK_INTERVAL_MINUTES, DEFAULT_WARN_DAYS
from database import db
from services import days_left, process_expired

log = logging.getLogger("scheduler")

# Кеш последних напоминаний: (user_id, channel_id) -> дата последней отправки
_last_reminder: dict[tuple[int, int], str] = {}


async def send_reminder(bot: Bot, user_id: int, channel_title: str, channel_emoji: str, left: int) -> None:
    text = (
        f"⏰ <b>Подписка скоро закончится</b>\n"
        "─────────────────────\n"
        f"{channel_emoji} {channel_title}\n"
        f"⏳ Осталось <b>{left}</b> дн.\n\n"
        f"Свяжитесь с администратором для продления."
    )
    try:
        await bot.send_message(user_id, text)
    except Exception as e:
        log.error("Не удалось отправить напоминание %s: %s", user_id, e)


async def check_and_notify(bot: Bot) -> None:
    """Проверяет истёкшие и отправляет напоминания (раз в сутки на канал)."""
    rows = await db.get_active_accesses()

    # 1. Кикаем истёкших
    removed, alive = await process_expired(bot)
    if removed:
        log.info("Кикнуто %s пользователей", removed)

    # 2. Напоминания
    notified = 0
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    for a in rows:
        left = days_left(a["expires_at"])
        if left <= 0:
            continue  # уже кикнут или будет кикнут
        if not a["notify_enabled"]:
            continue
        warn_days = a["warn_days"] or DEFAULT_WARN_DAYS
        if left > warn_days:
            continue

        key = (a["user_id"], a["channel_id"])
        if _last_reminder.get(key) == today:
            continue  # уже отправляли сегодня

        await send_reminder(bot, a["user_id"], a["title"], a["emoji"], left)
        _last_reminder[key] = today
        notified += 1

    if notified:
        log.info("Отправлено %s напоминаний", notified)


async def scheduler_loop(bot: Bot) -> None:
    """Бесконечный цикл проверки."""
    while True:
        try:
            await check_and_notify(bot)
        except Exception as e:
            log.error("Ошибка в планировщике: %s", e)
        await asyncio.sleep(max(1, CHECK_INTERVAL_MINUTES) * 60)


def start_scheduler(bot: Bot) -> asyncio.Task:
    """Запускает фоновую задачу планировщика."""
    return asyncio.create_task(scheduler_loop(bot))

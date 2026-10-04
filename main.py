"""Точка входа бота доступа к платным каналам."""

from __future__ import annotations

import asyncio
import os
import sys

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from loguru import logger

from config import BOT_TOKEN, LOG_FILE, validate
from database import db
from handlers.admin import admin_router
from handlers.join import join_router
from handlers.user import user_router
from scheduler import start_scheduler

os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
logger.add(LOG_FILE, rotation="10 MB", compression="zip", level="INFO", encoding="utf-8")


async def main() -> None:
    try:
        validate()
    except RuntimeError as e:
        logger.error(str(e))
        sys.exit(1)

    logger.info("Запуск бота...")

    await db.connect()

    bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()

    # Порядок важен: сначала /start <token> (join), потом остальные
    dp.include_router(join_router)
    dp.include_router(admin_router)
    dp.include_router(user_router)

    # Планировщик (истечение + напоминания)
    start_scheduler(bot)

    logger.info("Бот запущен и готов к работе!")

    try:
        await dp.start_polling(bot)
    finally:
        await db.close()
        await bot.session.close()
        logger.info("Сессия бота закрыта.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Бот остановлен вручную.")
    except Exception as e:
        logger.critical(f"Критическая ошибка при запуске: {e}")

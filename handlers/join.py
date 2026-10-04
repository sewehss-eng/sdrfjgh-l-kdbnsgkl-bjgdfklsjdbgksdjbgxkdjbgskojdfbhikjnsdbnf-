"""Обработка /start <token>, заявок в канал и выдачи доступа."""

from __future__ import annotations

import logging

from aiogram import Bot, F, Router
from aiogram.types import CallbackQuery, ChatJoinRequest, Message

from config import ADMIN_IDS, DEFAULT_DAYS
from database import db
from keyboards import approve_join_kb, main_menu_kb
from services import days_left, format_duration, grant, notify_grant

log = logging.getLogger("join")

join_router = Router(name="join")


# ─────────────────────────  /start <token>  ─────────────────────────


@join_router.message(F.text.startswith("/start "))
async def process_token(message: Message) -> None:
    token = message.text.split(maxsplit=1)[1].strip()
    user_id = message.from_user.id
    await db.add_user(user_id, message.from_user.full_name, message.from_user.username or "")

    ticket = await db.get_ticket(token)
    if not ticket:
        await message.answer(
            "❌ Ссылка недействительна или уже использована.",
            reply_markup=main_menu_kb(user_id in ADMIN_IDS),
        )
        return

    if ticket["used_by"] is not None:
        if ticket["used_by"] == user_id:
            # уже активировал — показываем доступ
            await message.answer(
                "✅ Вы уже активировали эту ссылку.",
                reply_markup=main_menu_kb(user_id in ADMIN_IDS),
            )
            return
        await message.answer(
            "❌ Эта ссылка уже была использована другим пользователем.",
            reply_markup=main_menu_kb(user_id in ADMIN_IDS),
        )
        return

    channel = await db.get_channel(ticket["channel_id"])
    if not channel:
        await message.answer("❌ Канал не найден.", reply_markup=main_menu_kb(user_id in ADMIN_IDS))
        return

    days = ticket["days"]
    duration_minutes = ticket["duration_minutes"]
    expires = await grant(
        message.bot, user_id, channel["id"], days, duration_minutes
    )
    await db.mark_ticket_used(token, user_id)

    # Уведомляем пользователя со ссылкой на вход
    await notify_grant(
        message.bot, user_id, channel["id"], days, expires, duration_minutes
    )

    # Уведомляем админов
    for admin_id in ADMIN_IDS:
        try:
            await message.bot.send_message(
                admin_id,
                f"🔗 <b>Ссылка активирована</b>\n─────────────────────\n"
                f"👤 {message.from_user.full_name} (ID {user_id})\n"
                f"🎓 Канал: {channel['emoji']} {channel['title']}\n"
                f"⏳ +{format_duration(days, duration_minutes)}",
            )
        except Exception:
            pass

    log.info("Тикет %s активирован пользователем %s", token, user_id)


# ─────────────────────────  заявки в канал  ─────────────────────────


@join_router.chat_join_request(F.chat.id)
async def on_join_request(request: ChatJoinRequest, bot: Bot) -> None:
    user_id = request.from_user.id
    chat_id = request.chat.id

    channel = await db.get_channel_by_chat(chat_id)
    if not channel:
        # Не наш канал — не трогаем
        return

    await db.add_user(user_id, request.from_user.full_name, request.from_user.username or "")

    # Проверяем, есть ли активный доступ
    access = await db.get_access(user_id, channel["id"])
    left = days_left(access["expires_at"]) if access else 0

    if left > 0:
        # Есть доступ — автоматически одобряем
        try:
            await request.approve()
            log.info("Заявка от %s в канал %s одобрена автоматически", user_id, channel["title"])
        except Exception as e:
            log.error("Не удалось одобрить заявку: %s", e)
        return

    # Нет доступа — уведомляем админов
    user_mention = (
        f"@{request.from_user.username}" if request.from_user.username else str(user_id)
    )
    text = (
        f"📩 <b>Новая заявка на вступление</b>\n"
        "─────────────────────\n"
        f"👤 {user_mention} (ID {user_id})\n"
        f"🎓 Канал: {channel['emoji']} {channel['title']}\n"
        f"⏳ Доступ: {'❌ нет'}"
    )
    kb = approve_join_kb(channel["id"], user_id, DEFAULT_DAYS)
    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(admin_id, text, reply_markup=kb)
        except Exception as e:
            log.error("Не удалось уведомить админа %s: %s", admin_id, e)


# ─────────────────────────  кнопки одобрения  ─────────────────────────


@join_router.callback_query(F.data.startswith("join:ok:"))
async def approve_join(call: CallbackQuery) -> None:
    parts = call.data.split(":")
    channel_id = int(parts[2])
    user_id = int(parts[3])
    days = int(parts[4]) if len(parts) > 4 else 30

    channel = await db.get_channel(channel_id)
    if not channel:
        await call.answer("Канал не найден", show_alert=True)
        return

    # Выдаём доступ
    expires = await grant(call.bot, user_id, channel_id, days)

    # Одобряем заявку
    try:
        # Ищем активную заявку
        # aiogram: approve через bot.approve_chat_join_request
        await call.bot.approve_chat_join_request(channel["chat_id"], user_id)
        log.info("Заявка %s в канал %s одобрена", user_id, channel["title"])
    except Exception as e:
        log.error("Не удалось одобрить заявку: %s", e)

    # Уведомляем пользователя
    await notify_grant(call.bot, user_id, channel_id, days, expires)

    await call.message.edit_text(
        f"✅ <b>Заявка одобрена</b>\n─────────────────────\n"
        f"👤 {user_id}\n"
        f"🎓 {channel['title']}\n"
        f"⏳ +{days} дн.",
    )
    await call.answer()


@join_router.callback_query(F.data.startswith("join:no:"))
async def decline_join(call: CallbackQuery) -> None:
    parts = call.data.split(":")
    channel_id = int(parts[2])
    user_id = int(parts[3])

    channel = await db.get_channel(channel_id)
    if not channel:
        await call.answer("Канал не найден", show_alert=True)
        return

    try:
        await call.bot.decline_chat_join_request(channel["chat_id"], user_id)
        log.info("Заявка %s в канал %s отклонена", user_id, channel["title"])
    except Exception as e:
        log.error("Не удалось отклонить заявку: %s", e)

    await call.message.edit_text(
        f"✖️ <b>Заявка отклонена</b>\n─────────────────────\n"
        f"👤 {user_id}\n"
        f"🎓 {channel['title']}"
    )
    await call.answer()

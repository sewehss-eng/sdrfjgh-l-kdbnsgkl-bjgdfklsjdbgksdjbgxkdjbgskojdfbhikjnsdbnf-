"""Хендлеры пользователя: главное меню, каталог, доступы, настройки."""

from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from aiogram.exceptions import TelegramBadRequest

from config import ADMIN_IDS, DEFAULT_WARN_DAYS
from database import db
from keyboards import (
    MAIN_MENU,
    HELP,
    access_join_kb,
    access_list_kb,
    back_to_main_kb,
    categories_kb,
    channel_category_back_kb,
    channel_links_kb,
    fmt_date,
    main_menu_kb,
    settings_kb,
    warn_days_kb,
)
from services import days_left, get_or_create_channel_link

user_router = Router(name="user")

log = logging.getLogger("user")


@user_router.callback_query(F.data == "noop")
async def _noop(call: CallbackQuery) -> None:
    """Заглушка для кнопок-плейсхолдеров (без действия)."""
    await call.answer()


def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


# ──────────────────────────────  /start  ──────────────────────────────


@user_router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext) -> None:
    await state.clear()
    user = message.from_user
    await db.add_user(user.id, user.full_name, user.username or "")
    await message.answer(MAIN_MENU, reply_markup=main_menu_kb(is_admin(user.id)))


@user_router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(HELP.format(warn=DEFAULT_WARN_DAYS), reply_markup=back_to_main_kb())


# ──────────────────────────  главное меню  ──────────────────────────


@user_router.callback_query(F.data == "home")
async def home(call: CallbackQuery) -> None:
    await call.message.edit_text(
        MAIN_MENU, reply_markup=main_menu_kb(is_admin(call.from_user.id))
    )
    await call.answer()


# ──────────────────────────  каталог  ──────────────────────────


@user_router.callback_query(F.data == "cat:list")
async def catalog(call: CallbackQuery) -> None:
    cats = await db.get_categories()
    if not cats:
        await call.message.edit_text(
            "🗂 Категории пока не добавлены.",
            reply_markup=back_to_main_kb(is_admin(call.from_user.id)),
        )
        await call.answer()
        return
    text = "🗂 <b>Категории курсов</b>\nВыберите категорию, чтобы увидеть каналы 👇"
    try:
        await call.message.edit_text(text, reply_markup=categories_kb(cats))
    except TelegramBadRequest as exc:
        if "there is no text in the message to edit" not in str(exc):
            raise
        await call.message.answer(text, reply_markup=categories_kb(cats))
    await call.answer()


@user_router.callback_query(F.data.startswith("cat:open:"))
async def catalog_open(call: CallbackQuery) -> None:
    category_id = int(call.data.split(":")[2])
    cat = await db.get_category(category_id)
    channels = await db.get_channels(category_id)
    if not cat:
        await call.answer("Категория не найдена", show_alert=True)
        return

    if not channels:
        await call.message.edit_text(
            f"{cat['emoji']} <b>{cat['title']}</b>\n\nВ этой категории пока нет каналов.",
            reply_markup=back_to_main_kb(is_admin(call.from_user.id)),
        )
        await call.answer()
        return

    rows = []
    items = []
    from services import get_or_create_channel_link

    for c in channels:
        acc = await db.get_access(call.from_user.id, c["id"])
        item = {"title": c["title"], "emoji": c["emoji"], "id": c["id"]}
        if acc and days_left(acc["expires_at"]) > 0:
            left = days_left(acc["expires_at"])
            rows.append(
                f"{c['emoji']} <b>{c['title']}</b> — ✅ осталось <b>{left} дн.</b>"
            )
            try:
                item["invite_link"] = await get_or_create_channel_link(
                    call.bot, c["chat_id"], c["id"]
                )
            except Exception:
                item["invite_link"] = None
        else:
            rows.append(f"{c['emoji']} <b>{c['title']}</b> — 🔒 нет доступа")
        items.append(item)

    text = (
        f"{cat['emoji']} <b>{cat['title']}</b>\n"
        "───────────────────\n" + "\n".join(rows)
    )
    try:
        await call.message.edit_text(text, reply_markup=channel_links_kb(items))
    except TelegramBadRequest as exc:
        if "there is no text in the message to edit" not in str(exc):
            raise
        await call.message.answer(text, reply_markup=channel_links_kb(items))
    await call.answer()


@user_router.callback_query(F.data.startswith("ch:view:"))
async def channel_view(call: CallbackQuery) -> None:
    channel_id = int(call.data.split(":")[2])
    ch = await db.get_channel(channel_id)
    if not ch:
        await call.answer("Канал не найден", show_alert=True)
        return
    acc = await db.get_access(call.from_user.id, channel_id)
    active = acc and days_left(acc["expires_at"]) > 0
    price = f"{ch['price']} руб." if ch["price"] else "уточняйте у администратора"
    text = (
        f"{ch['emoji']} <b>{ch['title']}</b>\n"
        "───────────────────\n"
        f"💰 Цена: <b>{price}</b>\n"
        f"\n{ch['description'] or 'Описание курса пока не добавлено.'}"
    )
    if active:
        text += f"\n\n✅ Доступ активен до <b>{fmt_date(acc['expires_at'])}</b>"
    elif acc:
        text += "\n\n❌ Срок доступа истёк"
    invite_link = None
    if active:
        try:
            invite_link = await get_or_create_channel_link(call.bot, ch["chat_id"], channel_id)
        except Exception as e:
            log.error("Не удалось создать ссылку для канала %s: %s", channel_id, e)
    markup = channel_category_back_kb(ch["category_id"], invite_link)
    if ch["schedule_file_id"]:
        try:
            await call.message.delete()
        except Exception:
            pass
        await call.message.answer_photo(
            ch["schedule_file_id"], caption=text, reply_markup=markup
        )
    else:
        try:
        await call.message.edit_text(text, reply_markup=markup)
    except TelegramBadRequest as exc:
        if "there is no text in the message to edit" not in str(exc):
            raise
        await call.message.answer(text, reply_markup=markup)
    await call.answer()


# ──────────────────────────  мои доступы  ──────────────────────────


@user_router.callback_query(F.data == "my:access")
async def my_access(call: CallbackQuery) -> None:
    rows = await db.get_user_accesses(call.from_user.id)
    if not rows:
        await call.message.edit_text(
            "💎 <b>Мои доступы</b>\n─────────────────────\n"
            "У вас пока нет доступов к каналам.\n"
            "Получите ссылку от администратора.",
            reply_markup=back_to_main_kb(is_admin(call.from_user.id)),
        )
        await call.answer()
        return

    items = []
    lines = []
    from services import get_or_create_channel_link

    for a in rows:
        left = days_left(a["expires_at"])
        item = {
            "title": a["title"],
            "emoji": a["emoji"],
            "channel_id": a["channel_id"],
            "days_left": left,
            "invite_link": None,
        }
        if left > 0:
            try:
                item["invite_link"] = await get_or_create_channel_link(
                    call.bot, a["chat_id"], a["channel_id"]
                )
            except Exception as e:
                log.error(
                    "Не удалось создать ссылку для канала %s: %s", a["channel_id"], e
                )
                item["invite_link"] = None
        items.append(item)
        status = f"✅ {left} дн." if left > 0 else "❌ истёк"
        lines.append(
            f"{a['emoji']} <b>{a['title']}</b>\n"
            f"   📅 до {fmt_date(a['expires_at'])} · {status}"
        )
    text = "💎 <b>Мои доступы</b>\n─────────────────────\n" + "\n".join(lines)
    await call.message.edit_text(text, reply_markup=access_list_kb(items))
    await call.answer()


@user_router.callback_query(F.data.startswith("acc:link:"))
async def access_get_link(call: CallbackQuery) -> None:
    """Повторная попытка создать ссылку на канал."""
    channel_id = int(call.data.split(":")[2])
    a = await db.get_access(call.from_user.id, channel_id)
    if not a:
        await call.answer("Доступ не найден", show_alert=True)
        return
    ch = await db.get_channel(channel_id)
    if not ch:
        await call.answer("Канал не найден", show_alert=True)
        return
    left = days_left(a["expires_at"])
    if left <= 0:
        await call.answer("Срок доступа истёк", show_alert=True)
        return
    try:
        link = await get_or_create_channel_link(call.bot, ch["chat_id"], channel_id)
    except Exception as e:
        log.error("Не удалось создать ссылку для канала %s: %s", channel_id, e)
        await call.answer(
            "❌ Не удалось создать ссылку.\n\n"
            "Убедитесь, что бот — администратор канала с правом «Приглашать пользователей».",
            show_alert=True,
        )
        return
    text = (
        "🔗 <b>Ссылка на вход</b>\n─────────────────────\n"
        f"{ch['emoji']} {ch['title']}\n"
        f"⏳ Осталось: <b>{left} дн.</b>\n\n"
        '👉 <a href="' + link + '">Войти в канал</a>'
    )
    await call.message.answer(text, disable_web_page_preview=True)
    await call.answer()


@user_router.callback_query(F.data.startswith("acc:view:"))
async def access_view(call: CallbackQuery) -> None:
    channel_id = int(call.data.split(":")[2])
    a = await db.get_access(call.from_user.id, channel_id)
    if not a:
        await call.answer("Доступ не найден", show_alert=True)
        return
    ch = await db.get_channel(channel_id)
    if not ch:
        await call.answer("Канал не найден", show_alert=True)
        return
    left = days_left(a["expires_at"])
    text = (
        f"{ch['emoji']} <b>{ch['title']}</b>\n"
        "───────────────────\n"
        f"📅 Доступ до: <b>{fmt_date(a['expires_at'])}</b>\n"
        f"⏳ Осталось: <b>{left} дн.</b>"
    )
    link = None
    if left > 0:
        try:
            from services import get_or_create_channel_link

            link = await get_or_create_channel_link(call.bot, ch["chat_id"], channel_id)
        except Exception:
            link = None
    await call.message.edit_text(
        text, reply_markup=access_join_kb(link, channel_id, is_admin(call.from_user.id))
    )
    await call.answer()


# ──────────────────────────  настройки  ──────────────────────────


@user_router.callback_query(F.data == "set:menu")
async def settings_menu(call: CallbackQuery) -> None:
    user = await db.get_user(call.from_user.id)
    if not user:
        await call.answer("Пользователь не найден", show_alert=True)
        return
    await call.message.edit_text(
        "⚙️ <b>Настройки уведомлений</b>\n"
        "─────────────────────\n"
        "Бот напомнит о скором окончании подписки.",
        reply_markup=settings_kb(user),
    )
    await call.answer()


@user_router.callback_query(F.data == "set:toggle")
async def settings_toggle(call: CallbackQuery) -> None:
    user = await db.get_user(call.from_user.id)
    if not user:
        return
    new_val = 0 if user["notify_enabled"] else 1
    await db.set_notify(call.from_user.id, new_val)
    await call.answer("Напоминания " + ("включены 🔔" if new_val else "выключены 🔕"))
    await settings_menu(call)


@user_router.callback_query(F.data == "set:toggle_daily")
async def settings_toggle_daily(call: CallbackQuery) -> None:
    user = await db.get_user(call.from_user.id)
    if not user:
        return
    new_val = 0 if user["daily_reminder"] else 1
    await db.set_daily_reminder(call.from_user.id, new_val)
    await call.answer("Ежедневные напоминания " + ("включены 📆" if new_val else "выключены"))
    await settings_menu(call)


@user_router.callback_query(F.data == "set:warn")
async def settings_warn(call: CallbackQuery) -> None:
    user = await db.get_user(call.from_user.id)
    if not user:
        return
    await call.message.edit_text(
        "⚙️ <b>За сколько дней предупреждать?</b>\n"
        "─────────────────────\n"
        f"Сейчас: за <b>{user['warn_days']}</b> дн.",
        reply_markup=warn_days_kb(user["warn_days"]),
    )
    await call.answer()


@user_router.callback_query(F.data.startswith("set:warn_set:"))
async def settings_warn_set(call: CallbackQuery) -> None:
    days = int(call.data.split(":")[2])
    await db.set_warn_days(call.from_user.id, days)
    await call.answer(f"Будем предупреждать за {days} дн.")
    await settings_menu(call)

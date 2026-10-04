"""Хендлеры администратора: категории, каналы, ссылки-тикеты, статистика."""

from __future__ import annotations

import logging
import re
from datetime import datetime

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from config import ADMIN_IDS, DEFAULT_DAYS, MAX_DAYS
from database import db
from keyboards import (
    ADMIN_MENU,
    admin_categories_kb,
    admin_channel_card_kb,
    admin_channels_kb,
    admin_link_list_kb,
    admin_menu_kb,
    admin_user_card_kb,
    admin_user_grant_kb,
    admin_users_kb,
    back_to_main_kb,
)
from services import (
    format_duration,
    grant,
    is_expired,
    invalidate_channel_link,
    new_token,
    notify_grant,
)

log = logging.getLogger("admin")

admin_router = Router(name="admin")

# Кеш username бота, чтобы не дёргать API каждый раз
_bot_username: str | None = None


async def _get_bot_username(bot) -> str:
    global _bot_username
    if _bot_username is None:
        me = await bot.get_me()
        _bot_username = me.username
    return _bot_username


# ────────────────────────────  FSM  ────────────────────────────


class AddCategory(StatesGroup):
    title = State()
    emoji = State()


class AddChannel(StatesGroup):
    waiting_forward = State()
    waiting_category = State()
    waiting_price = State()
    waiting_description = State()
    waiting_image = State()
    waiting_schedule = State()


class ChannelInfo(StatesGroup):
    price = State()
    description = State()
    image = State()
    schedule = State()


class CustomDays(StatesGroup):
    days = State()


class UserSearch(StatesGroup):
    query = State()


class UserGrant(StatesGroup):
    duration = State()


# ────────────────────────────  доступ  ────────────────────────────


def _is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


async def _admin_only(call: CallbackQuery) -> bool:
    if not _is_admin(call.from_user.id):
        await call.answer("⛔️ Только для администраторов", show_alert=True)
        return False
    return True


# ────────────────────────────  меню  ────────────────────────────


@admin_router.callback_query(F.data == "adm:menu")
async def admin_menu(call: CallbackQuery) -> None:
    if not await _admin_only(call):
        return
    await call.message.edit_text(ADMIN_MENU, reply_markup=admin_menu_kb())
    await call.answer()


# ────────────────────────────  пользователи  ─────────────────────────


async def _render_users(call: CallbackQuery, page: int = 0) -> None:
    page_size = 8
    users = await db.get_users(limit=page_size, offset=page * page_size)
    total = await db.count_users()
    if not users:
        await call.message.edit_text(
            "👥 Пользователей пока нет.",
            reply_markup=admin_users_kb([], page, page > 0, False),
        )
        return
    await call.message.edit_text(
        f"👥 <b>Пользователи</b> · страница {page + 1}\n"
        "─────────────────────\n"
        f"Всего: <b>{total}</b>\nВыберите пользователя:",
        reply_markup=admin_users_kb(
            users,
            page,
            page > 0,
            (page + 1) * page_size < total,
        ),
    )


@admin_router.callback_query(F.data == "adm:users")
@admin_router.callback_query(F.data.startswith("adm:users:"))
async def users_list(call: CallbackQuery) -> None:
    if not await _admin_only(call):
        return
    parts = call.data.split(":")
    page = int(parts[2]) if len(parts) > 2 else 0
    await _render_users(call, max(0, page))
    await call.answer()


@admin_router.callback_query(F.data == "adm:user_search")
async def user_search_start(call: CallbackQuery, state: FSMContext) -> None:
    if not await _admin_only(call):
        return
    await state.set_state(UserSearch.query)
    await call.message.edit_text(
        "🔎 <b>Поиск пользователя</b>\n"
        "─────────────────────\n"
        "Введите Telegram ID, username или имя:",
        reply_markup=back_to_main_kb(True),
    )
    await call.answer()


@admin_router.message(UserSearch.query)
async def user_search_result(message: Message, state: FSMContext) -> None:
    if not _is_admin(message.from_user.id):
        return
    query = (message.text or "").strip()
    if not query:
        await message.answer("❌ Введите ID, username или имя:")
        return
    users = await db.search_users(query)
    await state.clear()
    if not users:
        await message.answer(
            "🔎 Пользователи не найдены.",
            reply_markup=admin_users_kb([], 0, False, False),
        )
        return
    buttons = [
        [
            InlineKeyboardButton(
                text=f"👤 {u['full_name'][:24]}" + (f" @{u['username']}" if u['username'] else ""),
                callback_data=f"adm:user:{u['user_id']}",
            )
        ]
        for u in users
    ]
    buttons.append([InlineKeyboardButton(text="⬅️ К пользователям", callback_data="adm:users")])
    await message.answer(
        f"🔎 Найдено пользователей: <b>{len(users)}</b>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )


async def _render_user_card(call: CallbackQuery, user_id: int) -> bool:
    user = await db.get_user(user_id)
    if not user:
        return False
    accesses = await db.get_user_accesses(user_id)
    lines = [
        "👤 <b>Карточка пользователя</b>",
        "─────────────────────",
        f"Имя: <b>{user['full_name'] or '—'}</b>",
        f"Username: @{user['username']}" if user["username"] else "Username: —",
        f"ID: <code>{user_id}</code>",
        "",
        "💎 <b>Доступы:</b>",
    ]
    if accesses:
        for access in accesses:
            status = "❌ истёк" if is_expired(access["expires_at"]) else "✅ активен"
            expires = datetime.fromisoformat(access["expires_at"])
            lines.append(
                f"{access['emoji']} {access['title']} — {status} до "
                f"{expires.strftime('%d.%m.%Y %H:%M')}"
            )
    else:
        lines.append("Нет выданных доступов.")
    channels = await db.get_channels()
    await call.message.edit_text(
        "\n".join(lines),
        reply_markup=admin_user_card_kb(user_id, channels),
    )
    return True


@admin_router.callback_query(F.data.startswith("adm:user:"))
async def user_card(call: CallbackQuery) -> None:
    if not await _admin_only(call):
        return
    user_id = int(call.data.split(":")[2])
    rendered = await _render_user_card(call, user_id)
    if rendered:
        await call.answer()
    else:
        await call.answer("Пользователь не найден", show_alert=True)


@admin_router.callback_query(F.data.startswith("adm:user_grant:"))
async def user_grant_start(call: CallbackQuery) -> None:
    if not await _admin_only(call):
        return
    user_id = int(call.data.split(":")[2])
    channels = await db.get_channels()
    if not channels:
        await call.answer("Сначала добавьте канал", show_alert=True)
        return
    await call.message.edit_text(
        "➕ <b>Выберите канал</b> для выдачи или продления доступа:",
        reply_markup=admin_user_grant_kb(user_id, channels),
    )
    await call.answer()


@admin_router.callback_query(F.data.startswith("adm:user_channel:"))
async def user_grant_channel(call: CallbackQuery, state: FSMContext) -> None:
    if not await _admin_only(call):
        return
    _, _, user_id, channel_id = call.data.split(":")
    await state.set_state(UserGrant.duration)
    await state.update_data(user_id=int(user_id), channel_id=int(channel_id))
    await call.message.edit_text(
        "✍️ <b>Укажите срок доступа</b>\n"
        "─────────────────────\n"
        "Введите дни или минуты: <code>30</code>, <code>10м</code>, <code>10 мин</code>.",
        reply_markup=back_to_main_kb(True),
    )
    await call.answer()


@admin_router.message(UserGrant.duration)
async def user_grant_duration(message: Message, state: FSMContext) -> None:
    if not _is_admin(message.from_user.id):
        return
    value = (message.text or "").strip().lower()
    minutes_match = re.fullmatch(r"(\d+)\s*(?:м|мин|минута|минуты|минут)", value)
    if minutes_match:
        days = 0
        duration_minutes = int(minutes_match.group(1))
        if duration_minutes < 1:
            await message.answer("❌ Количество минут должно быть больше нуля:")
            return
    else:
        try:
            days = int(value)
        except ValueError:
            await message.answer("❌ Введите срок: например <code>30</code> или <code>10м</code>.")
            return
        if days < 1 or days > MAX_DAYS:
            await message.answer(f"❌ Число дней должно быть от 1 до {MAX_DAYS}:")
            return
        duration_minutes = None

    data = await state.get_data()
    await state.clear()
    user_id = int(data["user_id"])
    channel_id = int(data["channel_id"])
    channel = await db.get_channel(channel_id)
    if not channel or not await db.get_user(user_id):
        await message.answer("Пользователь или канал не найден.", reply_markup=admin_menu_kb())
        return
    expires = await grant(message.bot, user_id, channel_id, days, duration_minutes)
    await notify_grant(message.bot, user_id, channel_id, days, expires, duration_minutes)
    await message.answer(
        "✅ <b>Доступ выдан или продлён</b>\n"
        "─────────────────────\n"
        f"👤 ID: <code>{user_id}</code>\n"
        f"📢 {channel['emoji']} {channel['title']}\n"
        f"⏳ Срок: <b>{format_duration(days, duration_minutes)}</b>\n"
        f"📅 До: <b>{expires.strftime('%d.%m.%Y %H:%M')}</b>",
        reply_markup=admin_user_card_kb(user_id, await db.get_channels()),
    )


# ────────────────────────────  категории  ────────────────────────────


@admin_router.callback_query(F.data == "adm:cat_list")
async def cat_list(call: CallbackQuery) -> None:
    if not await _admin_only(call):
        return
    cats = await db.get_categories()
    if not cats:
        await call.message.edit_text(
            "🗂 Категории пока не добавлены.\nНажмите «➕ Добавить категорию».",
            reply_markup=admin_categories_kb(cats),
        )
        await call.answer()
        return
    await call.message.edit_text(
        "🗂 <b>Категории</b>\nНажмите на категорию, чтобы удалить её.",
        reply_markup=admin_categories_kb(cats),
    )
    await call.answer()


@admin_router.callback_query(F.data == "adm:cat_add")
async def cat_add_start(call: CallbackQuery, state: FSMContext) -> None:
    if not await _admin_only(call):
        return
    await call.answer()
    await state.set_state(AddCategory.title)
    await call.message.edit_text(
        "➕ <b>Новая категория</b>\n"
        "─────────────────────\n"
        "Введите название категории (например, «Программирование»):",
        reply_markup=back_to_main_kb(True),
    )


@admin_router.message(AddCategory.title)
async def cat_add_title(message: Message, state: FSMContext) -> None:
    if not _is_admin(message.from_user.id):
        return
    title = message.text.strip()
    if len(title) > 64:
        await message.answer("❌ Слишком длинное название (макс. 64 символа).")
        return
    await state.update_data(title=title)
    await state.set_state(AddCategory.emoji)
    await message.answer(
        f"✅ Название: <b>{title}</b>\n\n"
        "Теперь отправьте эмодзи для категории (например, 💻) или нажмите /skip:"
    )


@admin_router.message(AddCategory.emoji)
async def cat_add_emoji(message: Message, state: FSMContext) -> None:
    if not _is_admin(message.from_user.id):
        return
    if message.text.strip() == "/skip":
        emoji = "📁"
    else:
        emoji = message.text.strip()[:4] or "📁"
    data = await state.get_data()
    await db.add_category(data["title"], emoji)
    await state.clear()
    await message.answer(
        f"✅ Категория <b>{emoji} {data['title']}</b> добавлена!",
        reply_markup=admin_menu_kb(),
    )


@admin_router.callback_query(F.data.startswith("adm:cat_del:"))
async def cat_delete(call: CallbackQuery) -> None:
    if not await _admin_only(call):
        return
    category_id = int(call.data.split(":")[2])
    cat = await db.get_category(category_id)
    if not cat:
        await call.answer("Категория не найдена", show_alert=True)
        return
    await db.delete_category(category_id)
    await call.answer(f"Категория «{cat['title']}» удалена")
    await cat_list(call)


# ────────────────────────────  каналы  ────────────────────────────


@admin_router.callback_query(F.data == "adm:ch_list")
async def ch_list(call: CallbackQuery) -> None:
    if not await _admin_only(call):
        return
    channels = await db.get_channels()
    if not channels:
        await call.message.edit_text(
            "📢 Каналы пока не добавлены.\nНажмите «➕ Добавить канал».",
            reply_markup=admin_channels_kb(channels),
        )
        await call.answer()
        return
    await call.message.edit_text(
        "📢 <b>Каналы</b>\nВыберите канал, чтобы создать ссылку или управлять им.",
        reply_markup=admin_channels_kb(channels),
    )
    await call.answer()


@admin_router.callback_query(F.data == "adm:ch_add")
async def ch_add_start(call: CallbackQuery, state: FSMContext) -> None:
    if not await _admin_only(call):
        return
    await state.set_state(AddChannel.waiting_forward)
    await call.message.edit_text(
        "📡 <b>Добавить приватный канал</b>\n"
        "─────────────────────\n"
        "1. Добавьте этого бота <b>администратором</b> в приватный канал\n"
        "   (права: приглашать и кикать участников)\n"
        "2. Перешлите мне любое сообщение из этого канала\n"
        "3. Я определю канал и попрошу выбрать категорию\n\n"
        "Жду пересланное сообщение 👇",
        reply_markup=back_to_main_kb(True),
    )
    await call.answer()


@admin_router.message(AddChannel.waiting_forward, F.forward_origin)
async def ch_add_forward(message: Message, state: FSMContext) -> None:
    if not _is_admin(message.from_user.id):
        return

    origin = message.forward_origin
    if not hasattr(origin, "chat") or not origin.chat:
        await message.answer("❌ Не удалось определить канал из пересланного сообщения.")
        return

    chat = origin.chat
    chat_id = chat.id
    title = getattr(chat, "title", "") or str(chat_id)
    username = getattr(chat, "username", "") or ""

    await db.add_pending_channel(chat_id, title, username)
    await state.update_data(chat_id=chat_id, title=title, username=username)
    await state.set_state(AddChannel.waiting_category)

    cats = await db.get_categories()
    if not cats:
        await message.answer(
            f"✅ Канал определён: <b>{title}</b>\n\n"
            "⚠️ Но категорий пока нет. Сначала добавьте категорию.",
            reply_markup=admin_menu_kb(),
        )
        await state.clear()
        return

    buttons = [
        [InlineKeyboardButton(text=f"{c['emoji']} {c['title']}", callback_data=f"adm:ch_cat:{c['id']}")]
        for c in cats
    ]
    await message.answer(
        f"✅ Канал определён: <b>{title}</b>\n\nВыберите категорию:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )


@admin_router.message(AddChannel.waiting_forward)
async def ch_add_forward_invalid(message: Message) -> None:
    if not _is_admin(message.from_user.id):
        return
    await message.answer(
        "❌ Это не пересланное сообщение. Пожалуйста, перешлите сообщение из канала."
    )


@admin_router.callback_query(F.data.startswith("adm:ch_cat:"))
async def ch_add_category(call: CallbackQuery, state: FSMContext) -> None:
    if not await _admin_only(call):
        return
    category_id = int(call.data.split(":")[2])
    data = await state.get_data()
    chat_id = data.get("chat_id")
    title = data.get("title", "")
    username = data.get("username", "")

    if not chat_id:
        await call.answer("Данные потеряны, попробуйте снова", show_alert=True)
        await state.clear()
        return

    await state.update_data(category_id=category_id)
    await state.set_state(AddChannel.waiting_price)
    await call.message.edit_text(
        "Введите цену курса в рублях или /skip, если цена не указана:"
    )
    await call.answer()


@admin_router.message(AddChannel.waiting_price)
async def ch_add_price(message: Message, state: FSMContext) -> None:
    if not _is_admin(message.from_user.id):
        return
    value = (message.text or "").strip()
    if value == "/skip":
        price = 0
    else:
        try:
            price = int(value)
            if price < 0:
                raise ValueError
        except ValueError:
            await message.answer("Введите целое число рублей или /skip:")
            return
    await state.update_data(price=price)
    await state.set_state(AddChannel.waiting_description)
    await message.answer("Отправьте описание курса или /skip:")


@admin_router.message(AddChannel.waiting_description)
async def ch_add_description(message: Message, state: FSMContext) -> None:
    if not _is_admin(message.from_user.id):
        return
    await state.update_data(description="" if message.text == "/skip" else (message.text or ""))
    await state.set_state(AddChannel.waiting_image)
    await message.answer("Отправьте картинку курса или /skip:")


@admin_router.message(AddChannel.waiting_image)
async def ch_add_image(message: Message, state: FSMContext) -> None:
    if not _is_admin(message.from_user.id):
        return
    await state.update_data(image_file_id=message.photo[-1].file_id if message.photo else "")
    await state.set_state(AddChannel.waiting_schedule)
    await message.answer("Отправьте картинку с расписанием или /skip:")


@admin_router.message(AddChannel.waiting_schedule)
async def ch_add_schedule(message: Message, state: FSMContext) -> None:
    if not _is_admin(message.from_user.id):
        return
    data = await state.get_data()
    if message.photo:
        data["schedule_file_id"] = message.photo[-1].file_id
    else:
        data["schedule_file_id"] = ""
    channel_id = await db.add_channel(
        category_id=data["category_id"], chat_id=data["chat_id"], title=data["title"],
        username=data.get("username", ""), price=data.get("price", 0),
        description=data.get("description", ""), image_file_id=data.get("image_file_id", ""),
        schedule_file_id=data["schedule_file_id"],
    )
    await db.delete_pending_channel(data["chat_id"])
    await state.clear()
    await message.answer(
        f"✅ <b>Канал добавлен!</b>\n🎓 {data['title']}\n🆔 ID: <code>{channel_id}</code>",
        reply_markup=admin_channel_card_kb(channel_id),
    )


@admin_router.callback_query(F.data.startswith("adm:ch_info:"))
async def ch_info_start(call: CallbackQuery, state: FSMContext) -> None:
    if not await _admin_only(call):
        return
    channel_id = int(call.data.split(":")[2])
    if not await db.get_channel(channel_id):
        await call.answer("Канал не найден", show_alert=True)
        return
    await state.update_data(channel_id=channel_id)
    await state.set_state(ChannelInfo.price)
    await call.message.edit_text("Введите цену в рублях или /skip:", reply_markup=back_to_main_kb(True))
    await call.answer()


@admin_router.message(ChannelInfo.price)
async def ch_info_price(message: Message, state: FSMContext) -> None:
    try:
        price = 0 if message.text == "/skip" else int(message.text)
        if price < 0: raise ValueError
    except (TypeError, ValueError):
        await message.answer("Введите целое число рублей или /skip:")
        return
    await state.update_data(price=price)
    await state.set_state(ChannelInfo.description)
    await message.answer("Введите описание или /skip:")


@admin_router.message(ChannelInfo.description)
async def ch_info_description(message: Message, state: FSMContext) -> None:
    await state.update_data(description="" if message.text == "/skip" else (message.text or ""))
    await state.set_state(ChannelInfo.image)
    await message.answer("Отправьте картинку курса или /skip:")


@admin_router.message(ChannelInfo.image)
async def ch_info_image(message: Message, state: FSMContext) -> None:
    await state.update_data(image_file_id=message.photo[-1].file_id if message.photo else "")
    await state.set_state(ChannelInfo.schedule)
    await message.answer("Отправьте расписание или /skip:")


@admin_router.message(ChannelInfo.schedule)
async def ch_info_schedule(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    await db.update_channel_info(
        data["channel_id"], data.get("price", 0), data.get("description", ""),
        data.get("image_file_id", ""), message.photo[-1].file_id if message.photo else "",
    )
    await state.clear()
    await message.answer("✅ Данные каталога обновлены.", reply_markup=admin_channel_card_kb(data["channel_id"]))


@admin_router.callback_query(F.data.startswith("adm:ch_open:"))
async def ch_open(call: CallbackQuery) -> None:
    if not await _admin_only(call):
        return
    channel_id = int(call.data.split(":")[2])
    ch = await db.get_channel(channel_id)
    if not ch:
        await call.answer("Канал не найден", show_alert=True)
        return
    await call.message.edit_text(
        f"{ch['emoji']} <b>{ch['title']}</b>\n"
        "───────────────────\n"
        f"🗂 Категория ID: {ch['category_id']}\n"
        f"🔗 Username: @{ch['username'] or '—'}\n"
        f"🆔 Chat ID: <code>{ch['chat_id']}</code>\n\n"
        "Выберите действие:",
        reply_markup=admin_channel_card_kb(channel_id),
    )
    await call.answer()


@admin_router.callback_query(F.data.startswith("adm:ch_del:"))
async def ch_delete(call: CallbackQuery) -> None:
    if not await _admin_only(call):
        return
    channel_id = int(call.data.split(":")[2])
    ch = await db.get_channel(channel_id)
    if not ch:
        await call.answer("Канал не найден", show_alert=True)
        return
    await db.delete_channel(channel_id)
    await invalidate_channel_link(channel_id)
    await call.answer(f"Канал «{ch['title']}» удалён")
    await ch_list(call)


# ────────────────────────────  ссылки  ────────────────────────────


@admin_router.callback_query(F.data == "adm:link_new")
async def link_new(call: CallbackQuery) -> None:
    if not await _admin_only(call):
        return
    channels = await db.get_channels()
    if not channels:
        await call.message.edit_text(
            "❌ Сначала добавьте канал.",
            reply_markup=admin_menu_kb(),
        )
        await call.answer()
        return
    buttons = [
        [InlineKeyboardButton(text=f"{c['emoji']} {c['title']}", callback_data=f"adm:ch_open:{c['id']}")]
        for c in channels
    ]

    await call.message.edit_text(
        "🔗 <b>Создать ссылку</b>\n─────────────────────\nВыберите канал:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )
    await call.answer()


@admin_router.callback_query(F.data.startswith("adm:link_quick:"))
async def link_quick(call: CallbackQuery) -> None:
    if not await _admin_only(call):
        return
    channel_id = int(call.data.split(":")[2])
    ch = await db.get_channel(channel_id)
    if not ch:
        await call.answer("Канал не найден", show_alert=True)
        return
    token = new_token()
    await db.create_ticket(token, channel_id, DEFAULT_DAYS, call.from_user.id)
    link = f"https://t.me/{await _get_bot_username(call.bot)}?start={token}"
    await call.message.edit_text(
        f"🔗 <b>Ссылка создана!</b>\n"
        "─────────────────────\n"
        f"🎓 Канал: {ch['emoji']} {ch['title']}\n"
        f"⏳ Срок: <b>{DEFAULT_DAYS} дн.</b>\n\n"
        f"<code>{link}</code>\n\n"
        "Отправьте эту ссылку пользователю. Она одноразовая.",
        reply_markup=admin_channel_card_kb(channel_id),
    )
    await call.answer()


@admin_router.callback_query(F.data.startswith("adm:link_custom:"))
async def link_custom_start(call: CallbackQuery, state: FSMContext) -> None:
    if not await _admin_only(call):
        return
    channel_id = int(call.data.split(":")[2])
    await state.set_state(CustomDays.days)
    await state.update_data(channel_id=channel_id)
    await call.message.edit_text(
        "✍️ <b>Укажите срок доступа</b>\n"
        "─────────────────────\n"
        f"Введите дни или минуты: <code>2</code>, <code>10м</code>, <code>10 мин</code>\n"
        f"Дни: от 1 до {MAX_DAYS}; минуты: положительное число.",
        reply_markup=back_to_main_kb(True),
    )
    await call.answer()


@admin_router.message(CustomDays.days)
async def link_custom_days(message: Message, state: FSMContext) -> None:
    if not _is_admin(message.from_user.id):
        return
    value = message.text.strip().lower().replace(",", ".")
    minutes_match = re.fullmatch(r"(\d+)\s*(?:м|мин|минута|минуты|минут)", value)
    if minutes_match:
        days = 0
        duration_minutes = int(minutes_match.group(1))
        if duration_minutes < 1:
            await message.answer("❌ Количество минут должно быть больше нуля:")
            return
    else:
        try:
            days = int(value)
        except ValueError:
            await message.answer("❌ Введите срок: например <code>2</code> или <code>10м</code>.")
            return
        if days < 1 or days > MAX_DAYS:
            await message.answer(f"❌ Число дней должно быть от 1 до {MAX_DAYS}:")
            return
        duration_minutes = None
    data = await state.get_data()
    channel_id = data["channel_id"]
    await state.clear()
    ch = await db.get_channel(channel_id)
    if not ch:
        await message.answer("Канал не найден.", reply_markup=admin_menu_kb())
        return
    token = new_token()
    await db.create_ticket(
        token, channel_id, days, message.from_user.id, duration_minutes
    )
    link = f"https://t.me/{await _get_bot_username(message.bot)}?start={token}"
    await message.answer(
        f"🔗 <b>Ссылка создана!</b>\n"
        "─────────────────────\n"
        f"🎓 Канал: {ch['emoji']} {ch['title']}\n"
        f"⏳ Срок: <b>{format_duration(days, duration_minutes)}</b>\n\n"
        f"<code>{link}</code>\n\n"
        "Отправьте эту ссылку пользователю. Она одноразовая.",
        reply_markup=admin_channel_card_kb(channel_id),
    )


@admin_router.callback_query(F.data == "adm:link_list")
async def link_list(call: CallbackQuery) -> None:
    if not await _admin_only(call):
        return
    tickets = await db.get_tickets(limit=30)
    if not tickets:
        await call.message.edit_text(
            "🧾 Ссылок пока не создавали.",
            reply_markup=admin_menu_kb(),
        )
        await call.answer()
        return
    lines = []
    for t in tickets:
        status = "✅ использована" if t["used_by"] else "⏳ ждёт"
        lines.append(
            f"{t['channel_emoji']} {t['channel_title']} · "
            f"{format_duration(t['days'], t['duration_minutes'])} · {status}"
        )
    await call.message.edit_text(
        "🧾 <b>Последние ссылки</b>\n─────────────────────\n" + "\n".join(lines),
        reply_markup=admin_link_list_kb(),
    )
    await call.answer()


@admin_router.callback_query(F.data.startswith("adm:ch_links:"))
async def ch_links(call: CallbackQuery) -> None:
    if not await _admin_only(call):
        return
    channel_id = int(call.data.split(":")[2])
    ch = await db.get_channel(channel_id)
    if not ch:
        await call.answer("Канал не найден", show_alert=True)
        return
    tickets = await db.get_tickets(limit=100)
    tickets = [t for t in tickets if t["channel_id"] == channel_id]
    if not tickets:
        await call.message.edit_text(
            f"🧾 Ссылок для канала <b>{ch['title']}</b> пока не создавали.",
            reply_markup=admin_channel_card_kb(channel_id),
        )
        await call.answer()
        return
    lines = []
    for t in tickets:
        status = "✅" if t["used_by"] else "⏳"
        lines.append(
            f"{status} {format_duration(t['days'], t['duration_minutes'])} · "
            f"{t['created_at'][:10]}"
        )
    await call.message.edit_text(
        f"🧾 <b>Ссылки: {ch['title']}</b>\n─────────────────────\n" + "\n".join(lines),
        reply_markup=admin_channel_card_kb(channel_id),
    )
    await call.answer()


# ────────────────────────────  статистика  ────────────────────────────


@admin_router.callback_query(F.data == "adm:stats")
async def stats(call: CallbackQuery) -> None:
    if not await _admin_only(call):
        return
    s = await db.stats()
    await call.message.edit_text(
        "📊 <b>Статистика</b>\n"
        "─────────────────────\n"
        f"👥 Пользователей: <b>{s['users']}</b>\n"
        f"🗂 Категорий: <b>{s['categories']}</b>\n"
        f"📢 Каналов: <b>{s['channels']}</b>\n"
        f"💎 Активных доступов: <b>{s['access']}</b>\n"
        f"🔗 Создано ссылок: <b>{s['tickets']}</b>",
        reply_markup=admin_menu_kb(),
    )
    await call.answer()


@admin_router.callback_query(F.data == "adm:check")
async def check_now(call: CallbackQuery) -> None:
    if not await _admin_only(call):
        return
    from services import process_expired

    removed, alive = await process_expired(call.bot)
    await call.message.edit_text(
        "🔄 <b>Проверка подписок</b>\n"
        "─────────────────────\n"
        f"🗑 Удалено: <b>{removed}</b>\n"
        f"✅ Активных: <b>{alive}</b>",
        reply_markup=admin_menu_kb(),
    )
    await call.answer()

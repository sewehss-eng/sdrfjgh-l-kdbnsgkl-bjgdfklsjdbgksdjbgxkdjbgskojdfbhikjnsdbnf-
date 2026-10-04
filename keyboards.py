"""Клавиатуры и оформление сообщений."""

from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from config import DEFAULT_DAYS, DEFAULT_WARN_DAYS, MAX_DAYS

# ────────────────────────────────  Тексты  ────────────────────────────────

MAIN_MENU = (
    "🎓 <b>Доступ к курсам</b>\n"
    "─────────────────────\n"
    "Выберите раздел ниже 👇\n\n"
    "💎 <b>Мои доступы</b> — ваши каналы и сроки\n"
    "🗂 <b>Каталог</b> — все курсы по категориям\n"
    "⚙️ <b>Настройки</b> — уведомления о продлении"
)

CATALOG_HEADER = "🗂 <b>Категории курсов</b>\nВыберите категорию, чтобы увидеть каналы 👇"

ADMIN_MENU = (
    "🛠 <b>Панель администратора</b>\n"
    "─────────────────────\n"
    "Управляйте каналами, категориями и ссылками."
)

HELP = (
    "<b>Как это работает</b>\n"
    "─────────────────────\n"
    "1️⃣ Админ создаёт для вас персональную ссылку на канал\n"
    "2️⃣ Вы переходите по ней и вступаете\n"
    "3️⃣ Доступ продлевается на указанное число дней\n"
    "4️⃣ За <b>{warn}</b> дней до конца бот напишет напоминание\n"
    "5️⃣ Когда дни закончатся — бот уберёт вас из канала\n\n"
    "🔗 <b>Свою ссылку создать нельзя</b> — это делает только админ."
)


def fmt_date(iso: str | None) -> str:
    if not iso:
        return "—"
    try:
        from datetime import datetime

        return datetime.fromisoformat(iso).strftime("%d.%m.%Y")
    except ValueError:
        return "—"

# ─────────────────────────────  Пользователь  ─────────────────────────────


def main_menu_kb(is_admin: bool = False) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(text="💎 Мои доступы", callback_data="my:access"),
            InlineKeyboardButton(text="🗂 Каталог", callback_data="cat:list"),
        ],
        [InlineKeyboardButton(text="⚙️ Настройки", callback_data="set:menu")],
    ]
    if is_admin:
        rows.append([InlineKeyboardButton(text="🛠 Админ-панель", callback_data="adm:menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def back_to_main_kb(is_admin: bool = False) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="🏠 Главное меню", callback_data="home")]]
    )


def categories_kb(categories) -> InlineKeyboardMarkup:
    buttons = [
        InlineKeyboardButton(
            text=f"{c['emoji']} {c['title']}", callback_data=f"cat:open:{c['id']}"
        )
        for c in categories
    ]
    grid = [buttons[i : i + 2] for i in range(0, len(buttons), 2)]
    grid.append([InlineKeyboardButton(text="🏠 Главное меню", callback_data="home")])
    return InlineKeyboardMarkup(inline_keyboard=grid)


def channel_category_back_kb(category_id: int, invite_link: str | None = None) -> InlineKeyboardMarkup:
    rows = []
    if invite_link:
        rows.append([InlineKeyboardButton(text="🚀 Открыть канал", url=invite_link)])
    rows.extend([
        [InlineKeyboardButton(text="⬅️ Вернуться в категорию", callback_data=f"cat:open:{category_id}")],
        [InlineKeyboardButton(text="🗂 Все категории", callback_data="cat:list")],
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def channel_links_kb(channels) -> InlineKeyboardMarkup:
    """Меню пользователя: каналы категории. Если есть доступ — кнопка-ссылка."""
    kb = []
    for c in channels:
        kb.append([InlineKeyboardButton(text=f"{c['emoji']} {c['title']}", callback_data=f"ch:view:{c['id']}")])
    kb.append([InlineKeyboardButton(text="⬅️ Вернуться в категорию", callback_data="cat:list")])
    kb.append([InlineKeyboardButton(text="🏠 Главное меню", callback_data="home")])
    return InlineKeyboardMarkup(inline_keyboard=kb)


def access_list_kb(accesses) -> InlineKeyboardMarkup:
    """accesses — список словарей с ключами title, emoji, channel_id, days_left, invite_link.

    Для каждого активного доступа — кнопка входа в канал (постоянная ссылка).
    """
    kb = []
    for a in accesses:
        left = a["days_left"]
        if left <= 0:
            # Срок действительно истёк
            kb.append(
                [
                    InlineKeyboardButton(
                        text=f"{a['emoji']} {a['title']} · ❌ истёк",
                        callback_data=f"acc:view:{a['channel_id']}",
                    )
                ]
            )
        elif a.get("invite_link"):
            # Доступ активен и ссылка есть — вход по кнопке
            kb.append(
                [
                    InlineKeyboardButton(
                        text=f"🚀 {a['emoji']} {a['title']}",
                        url=a["invite_link"],
                    ),
                    InlineKeyboardButton(text=f"ℹ️ {left} дн.", callback_data=f"acc:view:{a['channel_id']}"),
                ]
            )
        else:
            # Доступ активен, но ссылку создать не удалось — даём кнопку повтора
            kb.append(
                [
                    InlineKeyboardButton(
                        text=f"{a['emoji']} {a['title']} · ✅ {left} дн.",
                        callback_data=f"acc:view:{a['channel_id']}",
                    ),
                    InlineKeyboardButton(text="🔗 Войти", callback_data=f"acc:link:{a['channel_id']}"),
                ]
            )
    kb.append([InlineKeyboardButton(text="🔄 Обновить", callback_data="my:access")])
    kb.append([InlineKeyboardButton(text="🏠 Главное меню", callback_data="home")])
    return InlineKeyboardMarkup(inline_keyboard=kb)


def access_join_kb(link: str | None, channel_id: int, is_admin_flag: bool = False) -> InlineKeyboardMarkup:
    """Карточка доступа: кнопка входа + обратно в меню."""
    kb = []
    if link:
        kb.append([InlineKeyboardButton(text="🚀 Войти в канал", url=link)])
    kb.append([InlineKeyboardButton(text="💎 Все мои доступы", callback_data="my:access")])
    kb.append([InlineKeyboardButton(text="🏠 Главное меню", callback_data="home")])
    return InlineKeyboardMarkup(inline_keyboard=kb)


def settings_kb(user_row) -> InlineKeyboardMarkup:
    on = "🟢" if user_row["notify_enabled"] else "🔴"
    daily = "🟢" if user_row["daily_reminder"] else "🔴"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"🔔 Напоминания: {on}", callback_data="set:toggle"
                )
            ],
            [
                InlineKeyboardButton(
                    text=f"📆 Ежедневно за {user_row['warn_days']} дн.: {daily}",
                    callback_data="set:toggle_daily",
                )
            ],
            [
                InlineKeyboardButton(
                    text="⚙️ За сколько дней предупреждать", callback_data="set:warn"
                )
            ],
            [InlineKeyboardButton(text="🏠 Главное меню", callback_data="home")],
        ]
    )


def warn_days_kb(current: int) -> InlineKeyboardMarkup:
    options = [1, 3, 5, 7, 14, 30]
    buttons = [
        InlineKeyboardButton(
            text=f"{d} дн." + (" ✅" if d == current else ""), callback_data=f"set:warn_set:{d}"
        )
        for d in options
    ]
    kb = [buttons[i : i + 3] for i in range(0, len(buttons), 3)]
    kb.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="set:menu")])
    return InlineKeyboardMarkup(inline_keyboard=kb)


# ────────────────────────────────  Админ  ────────────────────────────────


def admin_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🗂 Категории", callback_data="adm:cat_list"),
                InlineKeyboardButton(text="📢 Каналы", callback_data="adm:ch_list"),
            ],
            [
                InlineKeyboardButton(text="📡 Определить канал", callback_data="adm:ch_detect"),
                InlineKeyboardButton(text="➕ Канал по @username", callback_data="adm:ch_add"),
            ],
            [
                InlineKeyboardButton(text="🔗 Создать ссылку", callback_data="adm:link_new"),
                InlineKeyboardButton(text="🧾 История ссылок", callback_data="adm:link_list"),
            ],
            [
                InlineKeyboardButton(text="📊 Статистика", callback_data="adm:stats"),
                InlineKeyboardButton(text="🔄 Проверить подписки", callback_data="adm:check"),
            ],
            [InlineKeyboardButton(text="👥 Пользователи", callback_data="adm:users")],
            [InlineKeyboardButton(text="🏠 Главное меню", callback_data="home")],
        ]
    )


def admin_categories_kb(categories) -> InlineKeyboardMarkup:
    buttons = [
        InlineKeyboardButton(
            text=f"{c['emoji']} {c['title']} · ✖️", callback_data=f"adm:cat_del:{c['id']}"
        )
        for c in categories
    ]
    kb = [buttons[i : i + 2] for i in range(0, len(buttons), 2)]
    kb.append(
        [
            InlineKeyboardButton(text="➕ Добавить категорию", callback_data="adm:cat_add"),
            InlineKeyboardButton(text="🏠 Меню", callback_data="adm:menu"),
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=kb)


def admin_channels_kb(channels) -> InlineKeyboardMarkup:
    buttons = [
        InlineKeyboardButton(
            text=f"{c['emoji']} {c['title']}", callback_data=f"adm:ch_open:{c['id']}"
        )
        for c in channels
    ]
    kb = [buttons[i : i + 2] for i in range(0, len(buttons), 2)]
    kb.append(
        [
            InlineKeyboardButton(text="➕ Добавить канал", callback_data="adm:ch_add"),
            InlineKeyboardButton(text="🏠 Меню", callback_data="adm:menu"),
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=kb)


def admin_channel_card_kb(channel_id: int, has_links: bool = True) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=f"⚡️ {DEFAULT_DAYS} дней", callback_data=f"adm:link_quick:{channel_id}"
            )
        ],
        [
            InlineKeyboardButton(
                text="✍️ Указать дни", callback_data=f"adm:link_custom:{channel_id}"
            )
        ],
        [InlineKeyboardButton(text="📝 Цена, описание и фото", callback_data=f"adm:ch_info:{channel_id}")],
        [InlineKeyboardButton(text="🧾 Созданные ссылки", callback_data=f"adm:ch_links:{channel_id}")],
        [InlineKeyboardButton(text="⬅️ К списку каналов", callback_data="adm:ch_list")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_link_list_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="⬅️ Назад", callback_data="adm:menu")]]
    )


def admin_users_kb(users, page: int, has_prev: bool, has_next: bool) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=f"👤 {u['full_name'][:24]}" + (f" @{u['username']}" if u['username'] else ""),
                callback_data=f"adm:user:{u['user_id']}",
            )
        ]
        for u in users
    ]
    nav = []
    if has_prev:
        nav.append(InlineKeyboardButton(text="⬅️", callback_data=f"adm:users:{page - 1}"))
    if has_next:
        nav.append(InlineKeyboardButton(text="➡️", callback_data=f"adm:users:{page + 1}"))
    if nav:
        rows.append(nav)
    rows.extend(
        [
            [InlineKeyboardButton(text="🔎 Найти", callback_data="adm:user_search")],
            [InlineKeyboardButton(text="🏠 Админ-панель", callback_data="adm:menu")],
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_user_card_kb(user_id: int, channels) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text="➕ Выдать/продлить", callback_data=f"adm:user_grant:{user_id}")]
    ]
    rows.extend(
        [
            [InlineKeyboardButton(text=f"📢 {c['emoji']} {c['title']}", callback_data=f"adm:user_channel:{user_id}:{c['id']}")]
            for c in channels
        ]
    )
    rows.extend(
        [
            [InlineKeyboardButton(text="⬅️ К пользователям", callback_data="adm:users")],
            [InlineKeyboardButton(text="🏠 Админ-панель", callback_data="adm:menu")],
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_user_grant_kb(user_id: int, channels) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=f"📢 {c['emoji']} {c['title']}", callback_data=f"adm:user_channel:{user_id}:{c['id']}")]
        for c in channels
    ]
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data=f"adm:user:{user_id}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def approve_join_kb(channel_id: int, user_id: int, days: int = DEFAULT_DAYS) -> InlineKeyboardMarkup:
    """Кнопки под заявкой в канал (для админа)."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"✅ Одобрить (+{days} дн.)",
                    callback_data=f"join:ok:{channel_id}:{user_id}:{days}",
                ),
                InlineKeyboardButton(text="✖️ Отклонить", callback_data=f"join:no:{channel_id}:{user_id}"),
            ]
        ]
    )
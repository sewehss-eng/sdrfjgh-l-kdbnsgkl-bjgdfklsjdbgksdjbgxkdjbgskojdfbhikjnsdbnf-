"""
Слой данных: SQLite через aiosqlite.

Схема:
  users      — пользователи бота + их настройки уведомлений
  categories — категории каналов
  channels   — каналы (приватные чаты)
  access     — доступы пользователей к каналам (user_id + channel_id)
  tickets    — персональные ссылки-тикеты, которые создаёт админ
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

import aiosqlite

from config import DB_FILE

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    user_id        INTEGER PRIMARY KEY,
    full_name      TEXT NOT NULL DEFAULT '',
    username       TEXT NOT NULL DEFAULT '',
    notify_enabled INTEGER NOT NULL DEFAULT 1,
    daily_reminder INTEGER NOT NULL DEFAULT 1,
    warn_days      INTEGER NOT NULL DEFAULT 7,
    created_at     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS categories (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    title      TEXT NOT NULL,
    emoji      TEXT NOT NULL DEFAULT '📁',
    position   INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS channels (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    category_id  INTEGER NOT NULL REFERENCES categories(id) ON DELETE CASCADE,
    chat_id      INTEGER NOT NULL UNIQUE,
    username     TEXT NOT NULL DEFAULT '',
    title        TEXT NOT NULL,
    emoji        TEXT NOT NULL DEFAULT '📢',
    price        INTEGER NOT NULL DEFAULT 0,
    invite_link  TEXT NOT NULL DEFAULT '',
    created_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS access (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL,
    channel_id INTEGER NOT NULL REFERENCES channels(id) ON DELETE CASCADE,
    expires_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (user_id, channel_id)
);

CREATE TABLE IF NOT EXISTS tickets (
    token       TEXT PRIMARY KEY,
    channel_id  INTEGER NOT NULL REFERENCES channels(id) ON DELETE CASCADE,
    days        INTEGER NOT NULL,
    created_by  INTEGER NOT NULL,
    created_at  TEXT NOT NULL,
    used_by     INTEGER,
    used_at     TEXT,
    invite_link TEXT,
    duration_minutes INTEGER
);

CREATE TABLE IF NOT EXISTS pending_channels (
    chat_id   INTEGER PRIMARY KEY,
    title     TEXT NOT NULL DEFAULT '',
    username  TEXT NOT NULL DEFAULT '',
    added_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_access_user ON access(user_id);
CREATE INDEX IF NOT EXISTS idx_access_channel ON access(channel_id);
CREATE INDEX IF NOT EXISTS idx_access_expires ON access(expires_at);
CREATE INDEX IF NOT EXISTS idx_channels_category ON channels(category_id);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


class Database:
    """Асинхронная обёртка над SQLite."""

    def __init__(self, path: str = DB_FILE) -> None:
        self.path = path
        self._conn: aiosqlite.Connection | None = None

    # ── подключение ────────────────────────────
    async def connect(self) -> None:
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(self.path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA foreign_keys = ON")
        await self._conn.executescript(SCHEMA)
        ticket_columns = await self._q("PRAGMA table_info(tickets)")
        if "duration_minutes" not in {column["name"] for column in ticket_columns}:
            await self._conn.execute("ALTER TABLE tickets ADD COLUMN duration_minutes INTEGER")
        await self._conn.commit()

    async def close(self) -> None:
        if self._conn:
            await self._conn.close()
            self._conn = None

    async def _q(self, sql: str, params: Iterable[Any] = ()) -> list[aiosqlite.Row]:
        assert self._conn is not None, "БД не подключена"
        async with self._conn.execute(sql, params) as cur:
            return await cur.fetchall()

    async def _one(self, sql: str, params: Iterable[Any] = ()) -> aiosqlite.Row | None:
        rows = await self._q(sql, params)
        return rows[0] if rows else None

    async def _exec(self, sql: str, params: Iterable[Any] = ()) -> None:
        assert self._conn is not None, "БД не подключена"
        await self._conn.execute(sql, params)
        await self._conn.commit()

    # ── users ──────────────────────────────────
    async def add_user(self, user_id: int, full_name: str, username: str = "") -> None:
        await self._exec(
            """
            INSERT INTO users (user_id, full_name, username, created_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                full_name = excluded.full_name,
                username  = excluded.username
            """,
            (user_id, full_name, username, _now()),
        )

    async def get_user(self, user_id: int) -> aiosqlite.Row | None:
        return await self._one("SELECT * FROM users WHERE user_id = ?", (user_id,))

    async def get_users(self, limit: int = 10, offset: int = 0) -> list[aiosqlite.Row]:
        return await self._q(
            """
            SELECT u.*, COUNT(a.id) AS access_count
            FROM users u
            LEFT JOIN access a ON a.user_id = u.user_id
            GROUP BY u.user_id
            ORDER BY u.created_at DESC, u.user_id DESC
            LIMIT ? OFFSET ?
            """,
            (limit, offset),
        )

    async def count_users(self) -> int:
        row = await self._one("SELECT COUNT(*) AS count FROM users")
        return int(row["count"] if row else 0)

    async def search_users(self, query: str, limit: int = 30) -> list[aiosqlite.Row]:
        query = query.strip().lstrip("@")
        if query.isdigit():
            return await self._q(
                """
                SELECT u.*, COUNT(a.id) AS access_count
                FROM users u
                LEFT JOIN access a ON a.user_id = u.user_id
                WHERE u.user_id = ?
                GROUP BY u.user_id
                """,
                (int(query),),
            )
        pattern = f"%{query}%"
        return await self._q(
            """
            SELECT u.*, COUNT(a.id) AS access_count
            FROM users u
            LEFT JOIN access a ON a.user_id = u.user_id
            WHERE u.username LIKE ? OR u.full_name LIKE ?
            GROUP BY u.user_id
            ORDER BY u.created_at DESC
            LIMIT ?
            """,
            (pattern, pattern, limit),
        )

    async def set_notify(self, user_id: int, enabled: int) -> None:
        await self._exec("UPDATE users SET notify_enabled = ? WHERE user_id = ?", (enabled, user_id))

    async def set_daily_reminder(self, user_id: int, enabled: int) -> None:
        await self._exec("UPDATE users SET daily_reminder = ? WHERE user_id = ?", (enabled, user_id))

    async def set_warn_days(self, user_id: int, days: int) -> None:
        await self._exec("UPDATE users SET warn_days = ? WHERE user_id = ?", (days, user_id))

    # ── categories ─────────────────────────────
    async def add_category(self, title: str, emoji: str = "📁") -> int:
        cur = await self._conn.execute(
            "INSERT INTO categories (title, emoji, position) VALUES (?, ?, "
            "(SELECT COALESCE(MAX(position), 0) + 1 FROM categories))",
            (title, emoji),
        )
        await self._conn.commit()
        return int(cur.lastrowid)

    async def get_categories(self) -> list[aiosqlite.Row]:
        return await self._q("SELECT * FROM categories ORDER BY position, id")

    async def get_category(self, category_id: int) -> aiosqlite.Row | None:
        return await self._one("SELECT * FROM categories WHERE id = ?", (category_id,))

    async def delete_category(self, category_id: int) -> None:
        await self._exec("DELETE FROM categories WHERE id = ?", (category_id,))

    # ── channels ───────────────────────────────
    async def add_channel(
        self,
        category_id: int,
        chat_id: int,
        title: str,
        username: str = "",
        emoji: str = "📢",
        price: int = 0,
    ) -> int:
        cur = await self._conn.execute(
            """
            INSERT INTO channels (category_id, chat_id, username, title, emoji, price, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (category_id, chat_id, username, title, emoji, price, _now()),
        )
        await self._conn.commit()
        return int(cur.lastrowid)

    async def get_channels(self, category_id: int | None = None) -> list[aiosqlite.Row]:
        if category_id is None:
            return await self._q(
                "SELECT c.*, cat.title AS category_title, cat.emoji AS category_emoji "
                "FROM channels c JOIN categories cat ON cat.id = c.category_id "
                "ORDER BY cat.position, c.id"
            )
        return await self._q(
            "SELECT * FROM channels WHERE category_id = ? ORDER BY id", (category_id,)
        )

    async def get_channel(self, channel_id: int) -> aiosqlite.Row | None:
        return await self._one("SELECT * FROM channels WHERE id = ?", (channel_id,))

    async def get_channel_by_chat(self, chat_id: int) -> aiosqlite.Row | None:
        return await self._one("SELECT * FROM channels WHERE chat_id = ?", (chat_id,))

    async def delete_channel(self, channel_id: int) -> None:
        await self._exec("DELETE FROM channels WHERE id = ?", (channel_id,))

    async def set_invite_link(self, channel_id: int, invite_link: str) -> None:
        """Сохраняет постоянную ссылку канала, чтобы не создавать её заново."""
        await self._exec(
            "UPDATE channels SET invite_link = ? WHERE id = ?", (invite_link, channel_id)
        )

    # ── pending_channels (каналы, ожидающие привязки к категории) ──
    async def add_pending_channel(self, chat_id: int, title: str, username: str = "") -> None:
        await self._exec(
            """
            INSERT INTO pending_channels (chat_id, title, username, added_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(chat_id) DO UPDATE SET
                title = excluded.title,
                username = excluded.username
            """,
            (chat_id, title, username, _now()),
        )

    async def get_pending_channels(self) -> list[aiosqlite.Row]:
        return await self._q("SELECT * FROM pending_channels ORDER BY added_at DESC")

    async def delete_pending_channel(self, chat_id: int) -> None:
        await self._exec("DELETE FROM pending_channels WHERE chat_id = ?", (chat_id,))

    # ── access ─────────────────────────────────
    async def get_access(self, user_id: int, channel_id: int) -> aiosqlite.Row | None:
        return await self._one(
            "SELECT * FROM access WHERE user_id = ? AND channel_id = ?", (user_id, channel_id)
        )

    async def get_user_accesses(self, user_id: int) -> list[aiosqlite.Row]:
        return await self._q(
            """
            SELECT a.*, c.title, c.emoji, c.chat_id, c.username, c.price,
                   cat.title AS category_title, cat.emoji AS category_emoji
            FROM access a
            JOIN channels c ON c.id = a.channel_id
            JOIN categories cat ON cat.id = c.category_id
            WHERE a.user_id = ?
            ORDER BY a.expires_at DESC
            """,
            (user_id,),
        )

    async def get_active_accesses(self) -> list[aiosqlite.Row]:
        return await self._q(
            """
            SELECT a.*, c.title, c.emoji, c.chat_id, c.username,
                   u.full_name, u.username AS user_username, u.notify_enabled,
                   u.daily_reminder, u.warn_days
            FROM access a
            JOIN channels c ON c.id = a.channel_id
            JOIN users u ON u.user_id = a.user_id
            """
        )

    async def grant_access(
        self,
        user_id: int,
        channel_id: int,
        days: int = 0,
        duration_minutes: int | None = None,
    ) -> datetime:
        """Выдаёт/продлевает доступ. Возвращает новую дату окончания."""
        now = datetime.now(timezone.utc)
        duration = (
            timedelta(minutes=duration_minutes)
            if duration_minutes is not None
            else timedelta(days=days)
        )
        existing = await self.get_access(user_id, channel_id)
        if existing:
            old = _dt(existing["expires_at"]) or now
            base = old if old > now else now
            expires = base + duration
            await self._exec(
                "UPDATE access SET expires_at = ? WHERE user_id = ? AND channel_id = ?",
                (expires.isoformat(), user_id, channel_id),
            )
        else:
            expires = now + duration
            await self._exec(
                "INSERT INTO access (user_id, channel_id, expires_at, created_at) VALUES (?, ?, ?, ?)",
                (user_id, channel_id, expires.isoformat(), now.isoformat()),
            )
        return expires

    async def revoke_access(self, user_id: int, channel_id: int) -> None:
        await self._exec(
            "DELETE FROM access WHERE user_id = ? AND channel_id = ?", (user_id, channel_id)
        )

    # ── tickets ────────────────────────────────
    async def create_ticket(
        self,
        token: str,
        channel_id: int,
        days: int,
        created_by: int,
        duration_minutes: int | None = None,
    ) -> None:
        await self._exec(
            "INSERT INTO tickets (token, channel_id, days, created_by, created_at, duration_minutes) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (token, channel_id, days, created_by, _now(), duration_minutes),
        )

    async def get_ticket(self, token: str) -> aiosqlite.Row | None:
        return await self._one("SELECT * FROM tickets WHERE token = ?", (token,))

    async def mark_ticket_used(self, token: str, user_id: int, invite_link: str | None = None) -> None:
        await self._exec(
            "UPDATE tickets SET used_by = ?, used_at = ?, invite_link = ? WHERE token = ?",
            (user_id, _now(), invite_link, token),
        )

    async def get_tickets(self, limit: int = 50) -> list[aiosqlite.Row]:
        return await self._q(
            """
            SELECT t.*, c.title AS channel_title, c.emoji AS channel_emoji,
                   u.full_name AS used_by_name
            FROM tickets t
            JOIN channels c ON c.id = t.channel_id
            LEFT JOIN users u ON u.user_id = t.used_by
            ORDER BY t.created_at DESC LIMIT ?
            """,
            (limit,),
        )

    # ── статистика ─────────────────────────────
    async def stats(self) -> dict[str, int]:
        total_users = (await self._one("SELECT COUNT(*) AS n FROM users"))["n"]
        total_channels = (await self._one("SELECT COUNT(*) AS n FROM channels"))["n"]
        total_categories = (await self._one("SELECT COUNT(*) AS n FROM categories"))["n"]
        active_access = (await self._one("SELECT COUNT(*) AS n FROM access"))["n"]
        tickets = (await self._one("SELECT COUNT(*) AS n FROM tickets"))["n"]
        return {
            "users": total_users,
            "channels": total_channels,
            "categories": total_categories,
            "access": active_access,
            "tickets": tickets,
        }


db = Database()

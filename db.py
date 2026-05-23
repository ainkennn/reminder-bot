"""
db.py – SQLite persistence layer.

Tables
------
team_members : team roster managed by admins
tasks        : created tasks with full metadata
"""

import sqlite3
import contextlib
from config import DB_PATH


# ---------------------------------------------------------------------------
# Connection helper
# ---------------------------------------------------------------------------

@contextlib.contextmanager
def _conn():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    try:
        yield con
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

def init_db() -> None:
    with _conn() as con:
        con.executescript("""
            CREATE TABLE IF NOT EXISTS team_members (
                telegram_id   INTEGER PRIMARY KEY,
                display_name  TEXT    NOT NULL,
                role          TEXT    DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS tasks (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id          INTEGER NOT NULL,
                title            TEXT    NOT NULL DEFAULT '',
                description      TEXT    NOT NULL DEFAULT '',
                deadline         TEXT    NOT NULL,   -- ISO‑8601 string
                responsible_id   INTEGER NOT NULL,
                responsible_name TEXT    NOT NULL,
                task_status      TEXT    NOT NULL DEFAULT '',
                status           TEXT    NOT NULL DEFAULT 'pending',
                summary_msg_id   INTEGER,            -- pinned summary message
                reminder_msg_id  INTEGER             -- live reminder message
            );
        """)


# ---------------------------------------------------------------------------
# Team members
# ---------------------------------------------------------------------------

def upsert_member(telegram_id: int, display_name: str, role: str = "") -> None:
    with _conn() as con:
        con.execute("""
            INSERT INTO team_members (telegram_id, display_name, role)
            VALUES (?, ?, ?)
            ON CONFLICT(telegram_id) DO UPDATE SET
                display_name = excluded.display_name,
                role         = excluded.role
        """, (telegram_id, display_name, role))


def get_all_members() -> list[sqlite3.Row]:
    with _conn() as con:
        return con.execute(
            "SELECT * FROM team_members ORDER BY display_name"
        ).fetchall()


def get_member(telegram_id: int) -> sqlite3.Row | None:
    with _conn() as con:
        return con.execute(
            "SELECT * FROM team_members WHERE telegram_id = ?", (telegram_id,)
        ).fetchone()


def delete_member(telegram_id: int) -> bool:
    with _conn() as con:
        cur = con.execute(
            "DELETE FROM team_members WHERE telegram_id = ?", (telegram_id,)
        )
        return cur.rowcount > 0


# ---------------------------------------------------------------------------
# Tasks
# ---------------------------------------------------------------------------

def create_task(
    chat_id: int,
    description: str,
    deadline: str,
    responsible_id: int,
    responsible_name: str,
    title: str = "",
    task_status: str = "",
) -> int:
    with _conn() as con:
        cur = con.execute("""
            INSERT INTO tasks
                (chat_id, title, description, deadline, responsible_id, responsible_name, task_status)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (chat_id, title, description, deadline, responsible_id, responsible_name, task_status))
        return cur.lastrowid  # type: ignore[return-value]


def get_task(task_id: int) -> sqlite3.Row | None:
    with _conn() as con:
        return con.execute(
            "SELECT * FROM tasks WHERE id = ?", (task_id,)
        ).fetchone()


def set_summary_msg(task_id: int, msg_id: int) -> None:
    with _conn() as con:
        con.execute(
            "UPDATE tasks SET summary_msg_id = ? WHERE id = ?", (msg_id, task_id)
        )


def set_reminder_msg(task_id: int, msg_id: int) -> None:
    with _conn() as con:
        con.execute(
            "UPDATE tasks SET reminder_msg_id = ? WHERE id = ?", (msg_id, task_id)
        )


def mark_task_read(task_id: int) -> None:
    with _conn() as con:
        con.execute(
            "UPDATE tasks SET status = 'read' WHERE id = ?", (task_id,)
        )


def get_pending_tasks() -> list[sqlite3.Row]:
    """Return all tasks that still need a reminder to be scheduled."""
    with _conn() as con:
        return con.execute(
            "SELECT * FROM tasks WHERE status = 'pending'"
        ).fetchall()

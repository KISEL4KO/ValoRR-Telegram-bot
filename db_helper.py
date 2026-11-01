import sqlite3
from pathlib import Path

# Путь к файлу базы данных (создаётся рядом со скриптом)
DB_PATH = Path("valo_bot.db")

# SQL-схема: три таблицы — агенты, их способности и лор.
# IF NOT EXISTS — чтобы повторный запуск не падал, если таблицы уже есть.
SCHEMA = """
CREATE TABLE IF NOT EXISTS agents (
    uuid         TEXT PRIMARY KEY,   -- уникальный id агента из valorant-api
    name         TEXT NOT NULL,      -- имя агента ("Jett")
    role         TEXT,               -- роль ("Дуэлянт")
    description  TEXT,               -- краткое описание
    portrait_url TEXT,               -- ссылка на портрет
    game_version TEXT                -- версия патча, на которой данные обновлялись последний раз
);

CREATE TABLE IF NOT EXISTS abilities (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,   -- id способности (проставляется сам)
    agent_uuid  TEXT NOT NULL REFERENCES agents(uuid) ON DELETE CASCADE,  -- чья способность; удалится вместе с агентом
    slot        TEXT,                -- слот: Ability1, Ability2, Grenade, Ultimate
    name        TEXT NOT NULL,       -- название способности
    description TEXT                 -- описание способности
);

CREATE TABLE IF NOT EXISTS lore (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,   -- id записи лора
    agent_uuid  TEXT NOT NULL REFERENCES agents(uuid) ON DELETE CASCADE,  -- к какому агенту относится
    text        TEXT NOT NULL,       -- сам текст лора
    source_url  TEXT                 -- откуда взят текст (для указания источника)
);
"""


# Проверка: нужно ли создавать БД
def db_needs_init(path: Path) -> bool:
    """True, если файла нет или он пустой."""
    # path.exists() — есть ли файл; st_size == 0 — файл есть, но пустой
    return not path.exists() or path.stat().st_size == 0


# Создание БД и таблиц, если базы ещё нет
def init_db(path: Path) -> None:
    # Если база уже существует и не пустая — ничего не делаем
    if not db_needs_init(path):
        print("БД уже есть, создавать не нужно")
        return

    print("БД не найдена или пустая — создаю")
    conn = sqlite3.connect(path)              # открываем (и создаём) файл базы
    conn.execute("PRAGMA foreign_keys = ON")  # включаем проверку внешних ключей (в SQLite выключена по умолчанию)
    conn.executescript(SCHEMA)                # выполняем все CREATE TABLE разом
    conn.commit()                             # сохраняем изменения
    conn.close()                              # закрываем соединение


# Вставка строки или обновление, если строка с таким ключом уже есть (upsert)
# table  — имя таблицы
# values — словарь {колонка: значение}
# key    — колонка, по которой ищется конфликт (обычно "uuid")
def upsert_db(table: str, values: dict, key: str, path: Path):
    # "uuid, name, role" — список колонок для INSERT
    columns = ", ".join(values.keys())
    # "?, ?, ?" — плейсхолдеры, вместо них SQLite подставит значения безопасно
    placeholders = ", ".join("?" * len(values))
    # "name = excluded.name, role = excluded.role" — что обновлять при конфликте.
    # excluded.<колонка> — значение, которое мы пытались вставить.
    # Ключ не обновляем — он и так совпадает.
    sets = ", ".join(f"{col} = excluded.{col}" for col in values if col != key)

    # with сам делает commit, если ошибок не было
    with sqlite3.connect(path) as conn:
        conn.execute(
            f"INSERT INTO {table} ({columns}) VALUES ({placeholders}) "
            f"ON CONFLICT({key}) DO UPDATE SET {sets}",
            tuple(values.values()),  # значения в том же порядке, что и колонки
        )


# Получение данных агента по uuid
# ver=True  — вернуть только версию патча (строку)
# ver=False — вернуть всю запись агента (словарь)
def get_data(table: str, key: str, ver: bool, path: Path):
    with sqlite3.connect(path) as conn:
        # Строки как словари: можно писать row["name"] вместо row[1]
        conn.row_factory = sqlite3.Row

        if ver:
            # Достаём только версию патча
            row = conn.execute(
                f"SELECT game_version FROM {table} WHERE uuid = ?", (key,)
            ).fetchone()  # первая найденная строка или None
            return row["game_version"] if row else None
        else:
            # Достаём все колонки агента
            row = conn.execute(
                f"SELECT * FROM {table} WHERE uuid = ?", (key,)
            ).fetchone()
            return dict(row) if row else None  # превращаем в обычный словарь

# Поиск агента по имени (без учёта регистра)
def get_agent_by_name(name: str, path: Path):
    with sqlite3.connect(path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM agents WHERE LOWER(name) = LOWER(?)", (name,)
        ).fetchone()
        return dict(row) if row else None


# Все строки из abilities или lore для агента
def get_by_agent(table: str, agent_uuid: str, path: Path) -> list[dict]:
    with sqlite3.connect(path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            f"SELECT * FROM {table} WHERE agent_uuid = ?", (agent_uuid,)
        ).fetchall()
        return [dict(r) for r in rows]


# Заменить все способности агента новыми (при обновлении патча)
def replace_abilities(agent_uuid: str, abilities: list[dict], path: Path):
    with sqlite3.connect(path) as conn:
        conn.execute("DELETE FROM abilities WHERE agent_uuid = ?", (agent_uuid,))
        conn.executemany(
            "INSERT INTO abilities (agent_uuid, slot, name, description) VALUES (?, ?, ?, ?)",
            [(agent_uuid, a["slot"], a["name"], a.get("description")) for a in abilities],
        )


# Добавить запись лора
def add_lore(agent_uuid: str, text: str, source_url: str, path: Path):
    with sqlite3.connect(path) as conn:
        conn.execute(
            "INSERT INTO lore (agent_uuid, text, source_url) VALUES (?, ?, ?)",
            (agent_uuid, text, source_url),
        )

# Все роли (для первого уровня кнопок)
def get_roles(path: Path) -> list[str]:
    with sqlite3.connect(path) as conn:
        rows = conn.execute(
            "SELECT DISTINCT role FROM agents WHERE role IS NOT NULL ORDER BY role"
        ).fetchall()
        return [r[0] for r in rows]


# Агенты одной роли (для второго уровня кнопок)
def get_agents_by_role(role: str, path: Path) -> list[dict]:
    with sqlite3.connect(path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT uuid, name FROM agents WHERE role = ? ORDER BY name", (role,)
        ).fetchall()
        return [dict(r) for r in rows]

# Текущая версия игры, на которой лежат данные (None, если база пустая)
def get_db_version(path: Path) -> str | None:
    with sqlite3.connect(path) as conn:
        row = conn.execute("SELECT MAX(game_version) FROM agents").fetchone()
        return row[0] if row else None

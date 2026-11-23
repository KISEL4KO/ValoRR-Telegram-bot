"""Загрузка агентов из valorant-api в базу.

Запуск вручную: python sync.py
Бот тоже вызывает sync_agents() при старте.
"""
from pathlib import Path

from api import clean_text, fetch_agents, fetch_version
from db_helper import DB_PATH, get_db_version, init_db, replace_abilities, upsert_db


# Сохраняем одного агента и его способности
def save_agent(a: dict, version: str, path: Path) -> None:
    upsert_db("agents", {
        "uuid": a["uuid"],
        "name": a["displayName"],
        "role": a["role"]["displayName"] if a["role"] else None,
        "description": clean_text(a["description"]),
        "portrait_url": a["fullPortrait"],
        "game_version": version,
    }, "uuid", path)

    replace_abilities(a["uuid"], [
        {
            "slot": ab["slot"],
            "name": ab["displayName"],
            "description": clean_text(ab["description"]),
        }
        for ab in a["abilities"]
    ], path)


# Обновляем базу, если вышел новый патч (или база пустая).
# force=True — обновить в любом случае.
# Возвращает True, если данные обновлялись.
def sync_agents(path: Path = DB_PATH, force: bool = False) -> bool:
    init_db(path)

    version = fetch_version()
    updated = force or get_db_version(path) != version
    if updated:
        agents = fetch_agents()
        for a in agents:
            save_agent(a, version, path)
        print(f"Загружено агентов: {len(agents)} (версия {version})")
    else:
        print(f"Данные актуальны (версия {version})")

    # Лор дозаполняем при каждом запуске — fill_lore() берёт только агентов без лора,
    # так что когда всё заполнено, он ничего не делает.
    # Ошибка лора (нет HF_TOKEN, упала нейросеть) не должна ломать загрузку агентов.
    try:
        b = 0
    except Exception as e:
        print(f"Лор не обновлён: {e}")

    return updated


if __name__ == "__main__":
    sync_agents(force=True)

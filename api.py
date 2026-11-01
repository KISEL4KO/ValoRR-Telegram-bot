import html
import json
import re

import requests

BASE_URL = "https://valorant-api.com/v1"


# Список играбельных агентов
def fetch_agents(lang: str = "ru-RU") -> list[dict]:
    resp = requests.get(
        f"{BASE_URL}/agents",
        params={"isPlayableCharacter": "true", "language": lang},
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()["data"]


# Текущая версия игры, например "11.08"
def fetch_version() -> str:
    resp = requests.get(f"{BASE_URL}/version", timeout=10)
    resp.raise_for_status()
    return resp.json()["data"]["version"]


# Чистим текст из игры под HTML-режим Telegram:
# 1) экранируем < > &, чтобы Telegram не принял их за теги
# 2) игровые теги <whitebold>слово</> превращаем в <b>слово</b>
# 3) неразрывные пробелы и \r\n — в обычные
def clean_text(text: str | None) -> str | None:
    if text is None:
        return None
    text = html.escape(text, quote=False)
    text = re.sub(r"&lt;\w+&gt;(.*?)&lt;/&gt;", r"<b>\1</b>", text)
    text = text.replace("\xa0", " ").replace("\r\n", "\n")
    return text.strip()


if __name__ == "__main__":
    # Ручная проверка: python api.py
    agents = fetch_agents()  # один запрос, а не два
    print("Версия игры:", fetch_version())
    print("Агентов:", len(agents))
    print(json.dumps(agents[0], ensure_ascii=False, indent=2))

import asyncio
import logging
import sys
from os import getenv

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart
from aiogram.filters.callback_data import CallbackData
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from dotenv import load_dotenv

from db_helper import (
    DB_PATH,
    get_agents_by_role,
    get_by_agent,
    get_data,
    get_db_version,
    get_roles,
)
from sync import sync_agents

load_dotenv()
TOKEN = getenv("BOT_TOKEN")
if not TOKEN:
    raise RuntimeError("BOT_TOKEN не найден — проверь файл .env")

dp = Dispatcher()

# Порядок способностей в карточке
SLOT_ORDER = {"Ability1": 1, "Ability2": 2, "Grenade": 3, "Ultimate": 4, "Passive": 5}
SLOT_NAMES = {
    "Ability1": "Q", "Ability2": "E", "Grenade": "C",
    "Ultimate": "X (ульта)", "Passive": "Пассивка",
}


# ---------- Данные, которые передают кнопки ----------
# Telegram ограничивает callback_data 64 байтами — uuid (36) влезает

class MenuCb(CallbackData, prefix="menu"):
    action: str          # main / roles / version


class RoleCb(CallbackData, prefix="role"):
    role: str            # "Дуэлянт"


class AgentCb(CallbackData, prefix="agent"):
    uuid: str
    view: str            # card / abilities / lore


# ---------- Клавиатуры ----------

def main_menu_kb() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="🎯 Агенты по ролям", callback_data=MenuCb(action="roles"))
    kb.button(text="🛠 Версия патча", callback_data=MenuCb(action="version"))
    kb.adjust(1)
    return kb.as_markup()


def roles_kb() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for role in get_roles(DB_PATH):
        kb.button(text=role, callback_data=RoleCb(role=role))
    kb.button(text="◀ Назад", callback_data=MenuCb(action="main"))
    kb.adjust(2)
    return kb.as_markup()


def agents_kb(role: str) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for a in get_agents_by_role(role, DB_PATH):
        kb.button(text=a["name"], callback_data=AgentCb(uuid=a["uuid"], view="card"))
    kb.button(text="◀ К ролям", callback_data=MenuCb(action="roles"))
    kb.adjust(3)
    return kb.as_markup()


def agent_kb(agent: dict, current: str) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    uuid = agent["uuid"]
    if current != "card":
        kb.button(text="👤 Карточка", callback_data=AgentCb(uuid=uuid, view="card"))
    if current != "abilities":
        kb.button(text="⚡ Способности", callback_data=AgentCb(uuid=uuid, view="abilities"))
    if current != "lore":
        kb.button(text="📜 Лор", callback_data=AgentCb(uuid=uuid, view="lore"))
    if agent["role"]:
        kb.button(text="◀ К агентам", callback_data=RoleCb(role=agent["role"]))
    kb.adjust(2)
    return kb.as_markup()


# ---------- Тексты ----------

def card_text(agent: dict) -> str:
    return (
        f"<b>{agent['name']}</b> — {agent['role'] or 'без роли'}\n\n"
        f"{agent['description'] or ''}"
    )


def abilities_text(agent: dict) -> str:
    abilities = get_by_agent("abilities", agent["uuid"], DB_PATH)
    abilities.sort(key=lambda a: SLOT_ORDER.get(a["slot"], 99))
    parts = [f"<b>Способности {agent['name']}</b>"]
    for ab in abilities:
        key = SLOT_NAMES.get(ab["slot"], ab["slot"])
        parts.append(f"<b>[{key}] {ab['name']}</b>\n{ab['description'] or ''}")
    return "\n\n".join(parts)


def lore_text(agent: dict) -> str:
    lore = get_by_agent("lore", agent["uuid"], DB_PATH)
    if not lore:
        return f"<b>Лор {agent['name']}</b>\n\nЛор пока не добавлен."
    parts = [f"<b>Лор {agent['name']}</b>"]
    for entry in lore:
        text = entry["text"]
        if entry["source_url"]:
            text += f'\n<a href="{entry["source_url"]}">Источник</a>'
        parts.append(text)
    return "\n\n".join(parts)[:4000]  # лимит сообщения Telegram — 4096


# ---------- Показ экранов ----------
# Текстовые сообщения редактируем, а сообщение с фото (карточку) удаляем и шлём новое:
# у фото нельзя заменить подпись на длинный текст

async def show_text(call: CallbackQuery, text: str, kb: InlineKeyboardMarkup) -> None:
    if call.message.photo:
        await call.message.delete()
        await call.message.answer(text, reply_markup=kb)
    else:
        await call.message.edit_text(text, reply_markup=kb)


async def show_card(call: CallbackQuery, agent: dict) -> None:
    kb = agent_kb(agent, "card")
    caption = card_text(agent)[:1024]  # лимит подписи к фото
    await call.message.delete()
    if agent["portrait_url"]:
        await call.message.answer_photo(agent["portrait_url"], caption=caption, reply_markup=kb)
    else:
        await call.message.answer(caption, reply_markup=kb)


# ---------- Обработчики ----------

@dp.message(CommandStart())
async def cmd_start(message: Message) -> None:
    await message.answer(
        "Привет! Здесь лор и способности агентов VALORANT.\nВыбери раздел:",
        reply_markup=main_menu_kb(),
    )


@dp.callback_query(MenuCb.filter(F.action == "main"))
async def on_main(call: CallbackQuery) -> None:
    await show_text(call, "Выбери раздел:", main_menu_kb())
    await call.answer()


@dp.callback_query(MenuCb.filter(F.action == "roles"))
async def on_roles(call: CallbackQuery) -> None:
    await show_text(call, "Выбери роль:", roles_kb())
    await call.answer()


@dp.callback_query(MenuCb.filter(F.action == "version"))
async def on_version(call: CallbackQuery) -> None:
    version = get_db_version(DB_PATH) or "неизвестна"
    await call.answer(f"Данные для патча {version}", show_alert=True)


@dp.callback_query(RoleCb.filter())
async def on_role(call: CallbackQuery, callback_data: RoleCb) -> None:
    await show_text(call, f"<b>{callback_data.role}</b> — выбери агента:", agents_kb(callback_data.role))
    await call.answer()


@dp.callback_query(AgentCb.filter())
async def on_agent(call: CallbackQuery, callback_data: AgentCb) -> None:
    agent = get_data("agents", callback_data.uuid, False, DB_PATH)
    if agent is None:
        await call.answer("Агент не найден", show_alert=True)
        return

    if callback_data.view == "card":
        await show_card(call, agent)
    elif callback_data.view == "abilities":
        await show_text(call, abilities_text(agent), agent_kb(agent, "abilities"))
    elif callback_data.view == "lore":
        await show_text(call, lore_text(agent), agent_kb(agent, "lore"))
    await call.answer()


# Любой другой текст — подсказка вместо эха
@dp.message()
async def fallback(message: Message) -> None:
    await message.answer("Пользуйся кнопками 👇", reply_markup=main_menu_kb())


async def main() -> None:
    # Обновляем базу при старте. requests блокирующий — запускаем в отдельном потоке.
    try:
        await asyncio.to_thread(sync_agents, DB_PATH)
    except Exception as e:
        logging.warning("Не удалось обновить данные: %s", e)
        if not get_db_version(DB_PATH):
            raise RuntimeError("База пустая, а valorant-api недоступен — бот не сможет работать") from e

    bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    await dp.start_polling(bot)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, stream=sys.stdout)
    asyncio.run(main())

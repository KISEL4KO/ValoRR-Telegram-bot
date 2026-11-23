import asyncio
import html
import logging
import re
import sys
from os import getenv
from pathlib import Path

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart
from aiogram.filters.callback_data import CallbackData
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    FSInputFile,
    InlineKeyboardMarkup,
    InputMediaPhoto,
    InputRichMessage,
    Message,
)
from charts import load_all_stats, render_ability_stats, render_agent_stats
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

import json



load_dotenv()
TOKEN = getenv("BOT_TOKEN")
if not TOKEN:
    raise RuntimeError("BOT_TOKEN не найден — проверь файл .env")

dp = Dispatcher()

# Папка с лором: lore/jett.txt, lore/kayo.txt (для "KAY/O") и т.д.
# Там же видео для лора: lore/jett_present_video.mp4 (без звука — покажется как гифка)
LORE_DIR = Path(__file__).parent / "lore"

anim_cache: dict[str, str] = {}   # имя видео -> file_id в Telegram (повторно не загружаем)
lore_anims: dict[int, int] = {}   # chat_id -> id сообщения с гифкой, которое надо убрать при уходе
ALL_STATS: dict[str, list[dict]] = {}
MAX_MSG = 4000  # лимит сообщения Telegram — 4096, оставляем запас

# Порядок способностей в карточке
SLOT_ORDER = {"Ability1": 1, "Ability2": 2, "Grenade": 3, "Ultimate": 4, "Passive": 5}
SLOT_NAMES = {
    "Ability1": "Q", "Ability2": "E", "Grenade": "C",
    "Ultimate": "X (ульта)", "Passive": "Пассивка",
}

ABILITIES_DIR = Path(__file__).parent / "statistics"   # abilities/hush.json, abilities/kayo.json ...

KEY_ORDER = {"Q": 1, "E": 2, "C": 3, "X": 4}          # порядок кнопок; пассивка идёт последней
TYPE_NAMES = {
    "passive": "пассивка", "basic": "обычная",
    "signature": "сигнатурная", "ultimate": "ультимейт",
}
# (ключ в JSON, подпись, единица) — порядок строк в карточке
STAT_FIELDS = [
    ("cost", "💰 Цена", " кр."),
    ("ult_points", "⭐ Очки ульты", ""),
    ("uses", "🔁 Заряды", ""),
    ("recharge_kills", "☠ Убийств для заряда", ""),
    ("cooldown", "⏱ Кулдаун", " с"),
    ("windup", "⌛ Задержка", " с"),
    ("max_cast_duration", "🎯 Макс. каст", " с"),
    ("duration", "⏳ Длительность", " с"),
    ("duration_PF", "⏳ Длительность (огонь)", " с"),
    ("duration_AF", "⏳ Длительность (альт. огонь)", " с"),
    ("debuff", "💫 Дебафф", " с"),
]

# ---------- Данные, которые передают кнопки ----------
# Telegram ограничивает callback_data 64 байтами — uuid (36) влезает

class MenuCb(CallbackData, prefix="menu"):
    action: str          # main / roles / version


class RoleCb(CallbackData, prefix="role"):
    role: str            # "Дуэлянт"


class AgentCb(CallbackData, prefix="agent"):
    uuid: str
    view: str            # card / abilities / lore

class AbilityCb(CallbackData, prefix="ab"):
    uuid: str
    idx: int             # позиция способности в JSON-списке


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
    if current != "stats":
        kb.button(text="📊 Статистика", callback_data=AgentCb(uuid=uuid, view="stats"))
    kb.adjust(2)
    return kb.as_markup()

def ordered_abilities(stats: list[dict]) -> list[tuple[int, dict]]:
    # idx оставляем исходным (по нему ищем способность в JSON), меняем только порядок кнопок
    def key(item):
        ab = item[1]
        if ab.get("type") == "passive":
            return 5
        return KEY_ORDER.get(str(ab.get("keybind", "")).upper(), 99)
    return sorted(enumerate(stats), key=key)


def ability_label(ab: dict) -> str:
    key = ab.get("keybind") or "★"
    return f"{key} · {str(ab.get('name', '?')).title()}"


def abilities_kb(agent: dict, stats: list[dict]) -> InlineKeyboardMarkup:
    base = agent_kb(agent, "abilities")
    if not stats:
        return base
    kb = InlineKeyboardBuilder()
    for i, ab in ordered_abilities(stats):
        kb.button(text=ability_label(ab), callback_data=AbilityCb(uuid=agent["uuid"], idx=i))
    kb.adjust(3)
    # кнопки способностей сверху, обычная навигация (карточка/лор/назад) снизу
    return InlineKeyboardMarkup(inline_keyboard=kb.as_markup().inline_keyboard + base.inline_keyboard)


def ability_kb(agent: dict, stats: list[dict], current: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for i, ab in ordered_abilities(stats):
        if i != current:   # текущую не показываем: иначе edit_text вернёт "message is not modified"
            kb.button(text=ability_label(ab), callback_data=AbilityCb(uuid=agent["uuid"], idx=i))
    kb.adjust(3)
    back = InlineKeyboardBuilder()
    back.button(text="◀ К способностям", callback_data=AgentCb(uuid=agent["uuid"], view="abilities"))
    return InlineKeyboardMarkup(inline_keyboard=kb.as_markup().inline_keyboard + back.as_markup().inline_keyboard)


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


# Если valorant-api отдаёт имена по-русски — переводим их в имя файла
RU_TO_FILE = {
    "бримстоун": "brimstone", "вайпер": "viper", "омен": "omen",
    "киллджой": "killjoy", "сайфер": "cypher", "сова": "sova",
    "сейдж": "sage", "феникс": "phoenix", "джетт": "jett",
    "рейна": "reyna", "рейз": "raze", "брич": "breach",
    "скай": "skye", "йору": "yoru", "астра": "astra",
    "кайо": "kayo", "чембер": "chamber", "неон": "neon",
    "фейд": "fade", "харбор": "harbor", "гекко": "gekko",
    "дэдлок": "deadlock", "изо": "iso", "клов": "clove",
    "вайс": "vyse", "техо": "tejo", "вэйлэй": "waylay",
    "вето": "veto", "миск": "miks", "микс": "miks", "хаш": "hush",
}


def lore_path(name: str) -> Path:
    # "KAY/O" -> kayo.txt, "Jett" -> jett.txt; убираем всё, кроме букв и цифр
    key = re.sub(r"\W", "", name.lower())
    return LORE_DIR / (RU_TO_FILE.get(key, key) + ".txt")

def load_ability_stats(agent: dict) -> list[dict]:
    path = ABILITIES_DIR / (lore_path(agent["name"]).stem + ".json")
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as e:
        logging.warning("Не прочитал %s: %s", path.name, e)
        return []
    return data if isinstance(data, list) else []


def fmt_num(v) -> str:
    if isinstance(v, (int, float)):
        return f"{v:g}".replace(".", ",")      # 0.75 -> 0,75; 30.0 -> 30
    return str(v)


def ability_text(ab: dict) -> str:
    key = html.escape(str(ab.get("keybind") or "★"))
    name = html.escape(str(ab.get("name", "?")).title())
    head = f"<b>[{key}] {name}</b>"
    kind = TYPE_NAMES.get(ab.get("type"))
    if kind:
        head += f" — {kind}"
    parts = [head]

    if ab.get("description"):
        parts.append(html.escape(ab["description"]))

    lines = []
    for field, label, unit in STAT_FIELDS:
        v = ab.get(field)
        if v is None:
            continue
        if field == "cost" and v == 0:
            lines.append(f"{label}: бесплатно")
        else:
            lines.append(f"{label}: {fmt_num(v)}{unit}")
    if lines:
        parts.append("\n".join(lines))

    if len(parts) == 1:
        parts.append("Данных пока нет.")
    return "\n\n".join(parts)

def log_missing_lore() -> None:
    # При старте пишем в лог агентов без файла лора — сразу видно, где не совпало имя
    missing = [
        a["name"]
        for role in get_roles(DB_PATH)
        for a in get_agents_by_role(role, DB_PATH)
        if not lore_path(a["name"]).exists()
    ]
    if missing:
        logging.warning("Нет файла лора для: %s", ", ".join(missing))


def lore_markdown(agent: dict) -> str | None:
    # Текст для Rich Message: файл лора уже написан в Markdown ("### Заголовок",
    # таблицы "| a | b |", списки "- ..."), поэтому отдаём его как есть.
    # Экранировать ничего не нужно — это не HTML-режим.
    path = lore_path(agent["name"])
    if not path.exists():
        return None
    raw = path.read_text(encoding="utf-8-sig").strip()
    return f"# Лор {agent['name']}\n\n{raw}"


def lore_text(agent: dict) -> str:
    # Запасной вариант (обычное HTML-сообщение), если Rich Message не отправилось
    title = f"<b>Лор {html.escape(agent['name'])}</b>"
    path = lore_path(agent["name"])
    if not path.exists():
        return f"{title}\n\nЛор пока не добавлен."

    # utf-8-sig — чтобы не мешал BOM, который ставит Блокнот Windows
    raw = path.read_text(encoding="utf-8-sig").strip()

    # Строки "### Заголовок" делаем жирными, остальной текст экранируем:
    # бот работает в режиме HTML, и символы < > & в лоре сломали бы сообщение
    lines = []
    for line in raw.splitlines():
        if line.startswith("### "):
            lines.append(f"\n<b>{html.escape(line[4:].strip())}</b>")
        else:
            lines.append(html.escape(line))
    text = f"{title}\n\n" + "\n".join(lines).strip()
    text = re.sub(r"\n{3,}", "\n\n", text)  # не больше одной пустой строки подряд

    # Обрезаем по границе строки, чтобы не разрезать тег <b>
    if len(text) > MAX_MSG:
        text = text[:MAX_MSG].rsplit("\n", 1)[0] + "\n\n…"
    return text


# ---------- Показ экранов ----------
# Текстовые сообщения редактируем, а сообщение с фото (карточку) удаляем и шлём новое:
# у фото нельзя заменить подпись на длинный текст

async def show_photo(
    call: CallbackQuery,
    png: bytes,
    filename: str,
    caption: str,
    kb: InlineKeyboardMarkup,
) -> None:
    await clear_lore_anim(call)
    photo = BufferedInputFile(png, filename=filename)
    caption = caption[:1024]
    if call.message.photo:
        await call.message.edit_media(
            InputMediaPhoto(media=photo, caption=caption),
            reply_markup=kb,
        )
    else:
        await call.message.delete()
        await call.message.answer_photo(photo, caption=caption, reply_markup=kb)

async def clear_lore_anim(call: CallbackQuery) -> None:
    # Гифка лора — отдельное сообщение над текстом; убираем её, когда уходим с экрана лора
    msg_id = lore_anims.pop(call.message.chat.id, None)
    if msg_id:
        try:
            await call.bot.delete_message(call.message.chat.id, msg_id)
        except TelegramBadRequest:
            pass  # уже удалено пользователем или слишком старое


async def show_text(call: CallbackQuery, text: str, kb: InlineKeyboardMarkup) -> None:
    await clear_lore_anim(call)
    if call.message.text is None:   # фото, гифка — у них нет текста, редактировать нельзя
        await call.message.delete()
        await call.message.answer(text, reply_markup=kb)
    else:
        await call.message.edit_text(text, reply_markup=kb)


async def show_card(call: CallbackQuery, agent: dict) -> None:
    await clear_lore_anim(call)
    kb = agent_kb(agent, "card")
    caption = card_text(agent)[:1024]  # лимит подписи к фото
    await call.message.delete()
    if agent["portrait_url"]:
        await call.message.answer_photo(agent["portrait_url"], caption=caption, reply_markup=kb)
    else:
        await call.message.answer(caption, reply_markup=kb)


async def send_lore(message: Message, agent: dict, kb: InlineKeyboardMarkup) -> None:
    # Пробуем Rich Message (таблицы, заголовки); если Telegram его не принял
    # (сломанная разметка, слишком длинный текст, старый Bot API) — шлём обычный HTML
    md = lore_markdown(agent)
    if md is not None:
        try:
            await message.answer_rich(InputRichMessage(markdown=md), reply_markup=kb)
            return
        except TelegramBadRequest as e:
            logging.warning("Rich-лор %s не отправился, шлю обычный текст: %s", agent["name"], e)
    await message.answer(lore_text(agent), reply_markup=kb)


async def show_lore(call: CallbackQuery, agent: dict) -> None:
    kb = agent_kb(agent, "lore")
    # Rich Message нельзя получить через edit_text обычного сообщения,
    # поэтому старое сообщение всегда удаляем и шлём новое
    await clear_lore_anim(call)
    await call.message.delete()

    video = LORE_DIR / f"{lore_path(agent['name']).stem}_present_video.mp4"
    if video.exists():
        # Гифка — отдельным сообщением сверху, лор с кнопками — под ней
        media = anim_cache.get(video.name) or FSInputFile(video)
        anim_msg = await call.message.answer_animation(media)
        # Если в ролике есть звук, Telegram сохранит его как видео, а не гифку — берём то, что пришло
        media_obj = anim_msg.animation or anim_msg.video
        if media_obj:
            anim_cache[video.name] = media_obj.file_id
        lore_anims[call.message.chat.id] = anim_msg.message_id

    await send_lore(call.message, agent, kb)


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
    version = get_db_version(DB_PATH)
    # "13.09.00.5777412" -> "13.09"
    version = ".".join(version.split(".")[:2]) if version else "неизвестна"
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
        stats = load_ability_stats(agent)
        await show_text(call, abilities_text(agent), abilities_kb(agent, stats))
    elif callback_data.view == "stats":
        stats = load_ability_stats(agent)
        if not stats:
            await call.answer("Нет данных для статистики", show_alert=True)
            return
        png = await asyncio.to_thread(render_agent_stats, agent["name"], stats, ALL_STATS)
        await show_photo(
            call, png, f"{agent['uuid']}_stats.png",
            f"<b>📊 Статистика {html.escape(agent['name'])}</b>",
            agent_kb(agent, "stats"),
        )
    elif callback_data.view == "lore":
        await show_lore(call, agent)
    await call.answer()


@dp.callback_query(AbilityCb.filter())
async def on_ability(call: CallbackQuery, callback_data: AbilityCb) -> None:
    agent = get_data("agents", callback_data.uuid, False, DB_PATH)
    if agent is None:
        await call.answer("Агент не найден", show_alert=True)
        return
    stats = load_ability_stats(agent)
    if not 0 <= callback_data.idx < len(stats):
        await call.answer("Данных о способности нет", show_alert=True)
        return

    ab = stats[callback_data.idx]
    kb = ability_kb(agent, stats, callback_data.idx)
    text = ability_text(ab)
    png = await asyncio.to_thread(render_ability_stats, agent["name"], ab, ALL_STATS)
    if png:
        await show_photo(call, png, f"{agent['uuid']}_{callback_data.idx}.png", text, kb)
    else:
        await show_text(call, text, kb)
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

    LORE_DIR.mkdir(exist_ok=True)
    ABILITIES_DIR.mkdir(exist_ok=True)
    log_missing_lore()
    bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    await dp.start_polling(bot)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, stream=sys.stdout)
    asyncio.run(main())
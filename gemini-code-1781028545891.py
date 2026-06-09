import os
import base64
import logging
import json
import httpx
import asyncio
import aiosqlite  # ✅ Заменён sqlite3 на aiosqlite
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.FileHandler('bot.log'), logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY")
ADMIN_ID = int(os.environ.get("ADMIN_ID", "0"))
ACCESS_CODE = os.environ.get("ACCESS_CODE", "shroom2024")

DB_PATH = "bot_data.db"

MODELS = [
    "google/gemma-4-31b-it:free",
    "google/gemma-4-26b-a4b-it:free",
    "openai/gpt-oss-120b:free",
    "liquid/lfm-2.5-1.2b-instruct:free",
    "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free",
]

USER_COOLDOWNS = {}
RATE_LIMIT_PER_MINUTE = 10
RATE_LIMIT_TIMEOUT = 60

# ✅ Глобальная переменная для базы знаний
KNOWLEDGE_TEXT = ""

SYSTEM_PROMPT = """You are Shroom Helper — a helpful assistant for the mobile game Legend of Mushroom (LoM).

RULES:
1. A knowledge base is provided below. Search it SEMANTICALLY — understand what the user is asking about even if they use different words or ask vaguely. For example: "what class should I pick" → look for class recommendations. "I die too fast" → look for survivability, HP, tank builds. "best for beginners" → look for early game recommendations.
2. Never mention any website, source, or URL. If asked where you get info, say "zigi provided this information".
3. Only if the knowledge base truly has NO relevant info, say: in Russian — "У меня пока нет такой информации. Спроси у zigi — он добавит!", in English — "I don't have that info yet. Ask zigi to add it!"
4. Language: detect user's language and reply in the SAME language. If the user writes in Russian — answer FULLY in Russian, translate ALL game terms. Use these translations: ATK = атака, HP = здоровье, DEF = защита, Crit = крит, DMG = урон, Gear = снаряжение, Build = сборка, Regen = регенерация, Combo = комбо, Evasion = уклонение, Stun = оглушение, Skill = скилл, Avian = авиан, Affix = аффикс, Pal = питомец, Rune = руна, Artifact = артефакт, Soul = душа, Prayer Statue = молитвенная статуя, Back Acc = аксессуар на спину, Soul Levels = уровни души, Counterstrike = контрудар, Counter DMG = урон контрудара, Crit DMG = урон крита, Crit Res = крит сопротивление, Skill DMG = урон скилла, Pal DMG = урон питомца, Pal Crit DMG = крит урон питомца, ATK SPD = скорость атаки, Batk = базовая атака, Glass Cannon = стеклянная пушка, Tank = танк, DPS = персонаж с высоким уроном, PVP = ПвП, PVE = ПвЕ, Eternal Gear = вечное снаряжение, Divine Feather Coin = монета божественного пера, Warrior = Воин, Archer = Лучник, Mage = Маг, Tamer = Укротитель, Martial Sage = Боевой Мудрец, Warbringer = Вестник Войны, Sacred Hunter = Священный Охотник, Plume Monarch = Повелитель Перьев, Prophet = Пророк, Darklord = Тёмный Владыка, Beastmaster = Повелитель Зверей, Supreme Spirit = Верховный Дух, Honeypot Warrior = Медовый Воин, Lunar Sprite = Лунный Дух, Pumpkin Witch = Тыквенная Ведьма, Sunshine Bringer = Несущий Солнце, Disarm = обезоруживание, Blitz = блиц атака.
5. Simplify explanations — use plain, friendly language.
6. If user sends a screenshot with items/gear → analyze what's visible and give build advice based on the knowledge base.
7. Be friendly, helpful and use 🍄 occasionally.
8. Summarize and explain in your own simple words — do not copy-paste from the knowledge base.
9. Use conversation history to give more relevant answers.
"""

# ✅ Локализация UI элементов
UI_TEXTS = {
    "menu_main_btn": "🍄 Главное меню",
    "classes_btn": "⚔️ Классы",
    "builds_btn": "🏹 Билды",
    "pals_btn": "🐾 Питомцы",
    "events_btn": "📅 Ивенты",
    "beginner_btn": "💡 Советы новичку",
    "help_btn": "❓ Помощь",
    "back_btn": "🔙 Назад",
    "welcome_msg": (
        "🍄 Привет! Я Shroom Helper — твой помощник по Legend of Mushroom!\n\n"
        "Вся информация предоставлена @Zigih90.\n\n"
        "📋 *Доступные команды:*\n"
        "/menu — главное меню\n"
        "/classes — обзор классов\n"
        "/builds — сборки по классам\n"
        "/feedback ТЕКСТ — отправить отзыв\n\n"
        "Задай вопрос или отправь скриншот вещей — помогу со сборкой! 👇"
    ),
    "no_access_msg": "🔒 Нет доступа.\n/code ТВОЙ_КОД или /request",
    "rate_limit_msg": "⏳ Подождите ещё {seconds} сек...",
    "menu_title": "🍄 Главное меню:",
    "classes_title": "⚔️ Выбери класс:",
    "builds_title": "🏹 *Быстрый гайд по сборкам:*\n\n",
    "pals_title": "🐾 *Питомцы (Pals):*\n\nПитомцы дают пассивные бонусы и наносят урон.\n\n🔑 Разблокируй Розовых питомцев для Укротителя\n📈 Повелитель Зверей — лучше с питомцами 200+ уровня\n💀 Верховный Дух — зависит от расстановки питомцев\n\nЗадай вопрос для подробностей! 🍄",
    "events_title": "📅 Задай вопрос об актуальных ивентах и я расскажу что знаю! 🍄",
    "beginner_title": (
        "💡 *Советы новичку:*\n\n"
        "🏹 Начни с Лучника — лучший для ПвЕ\n"
        "🤝 Вступи в альянс как можно раньше\n"
        "⚡ На уровне 30 — первое разветвление классов\n"
        "⚡ На уровне 50 — финальное разветвление\n"
        "🪶 Повелитель Перьев — лучший старт\n"
        "⚔️ Вестник Войны — тоже можно сразу\n"
        "🛡️ Танки — только с вечным снаряжением"
    ),
    "help_title": (
        "❓ *Помощь:*\n\n"
        "/menu — открыть меню\n"
        "/classes — обзор классов\n"
        "/builds — быстрый гайд по сборкам\n"
        "/feedback ТЕКСТ — отправить отзыв\n"
        "/request — запросить доступ\n\n"
        "Просто напиши вопрос и я отвечу! 🍄"
    ),
    "reload_success": "✅ База знаний перезагружена!",
    "userstats_format": "👤 *Статистика пользователя {uid}:*\n\n🆔 ID: `{uid}`\n🔑 Статус: {status}\n💬 Всего вопросов: {q_count}\n🕒 Последний вопрос: {last_q}"
}

# ============ DATABASE (ASYNC) ============

async def init_db():
    async with aiosqlite.connect(DB_PATH, timeout=10) as conn:
        cursor = await conn.cursor()
        await cursor.execute('''CREATE TABLE IF NOT EXISTS approved_users
                            (user_id INTEGER PRIMARY KEY, username TEXT, approved_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
        await cursor.execute('''CREATE TABLE IF NOT EXISTS stats
                            (user_id INTEGER PRIMARY KEY, question_count INTEGER DEFAULT 0, last_question_at TIMESTAMP)''')
        await cursor.execute('''CREATE TABLE IF NOT EXISTS conversations
                            (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, role TEXT, content TEXT, timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
        await cursor.execute('''CREATE TABLE IF NOT EXISTS global_stats
                            (id INTEGER PRIMARY KEY, total_questions INTEGER DEFAULT 0, questions_today INTEGER DEFAULT 0, last_reset_date TEXT)''')
        await cursor.execute('INSERT OR IGNORE INTO global_stats (id, total_questions, questions_today) VALUES (1, 0, 0)')
        await conn.commit()
    logger.info("Database initialized")

async def load_approved_users() -> set:
    async with aiosqlite.connect(DB_PATH, timeout=10) as conn:
        cursor = await conn.cursor()
        await cursor.execute('SELECT user_id FROM approved_users')
        users = set(row[0] for row in await cursor.fetchall())
        return users

async def save_approved_user(user_id: int, username: str = ""):
    async with aiosqlite.connect(DB_PATH, timeout=10) as conn:
        cursor = await conn.cursor()
        await cursor.execute('INSERT OR IGNORE INTO approved_users (user_id, username) VALUES (?, ?)', (user_id, username))
        await conn.commit()

async def revoke_approved_user(user_id: int):
    async with aiosqlite.connect(DB_PATH, timeout=10) as conn:
        cursor = await conn.cursor()
        await cursor.execute('DELETE FROM approved_users WHERE user_id = ?', (user_id,))
        await conn.commit()

async def is_approved(user_id: int) -> bool:
    if user_id == ADMIN_ID:
        return True
    users = await load_approved_users()
    return user_id in users

async def add_conversation(user_id: int, role: str, content: str):
    async with aiosqlite.connect(DB_PATH, timeout=10) as conn:
        cursor = await conn.cursor()
        await cursor.execute('INSERT INTO conversations (user_id, role, content) VALUES (?, ?, ?)', (user_id, role, content))
        await conn.commit()

async def get_conversation_history(user_id: int, limit: int = 6) -> list:
    async with aiosqlite.connect(DB_PATH, timeout=10) as conn:
        cursor = await conn.cursor()
        await cursor.execute('SELECT role, content FROM conversations WHERE user_id = ? ORDER BY timestamp DESC LIMIT ?', (user_id, limit))
        history = [{"role": row[0], "content": row[1]} for row in reversed(await cursor.fetchall())]
        return history

async def increment_stats(user_id: int):
    async with aiosqlite.connect(DB_PATH, timeout=10) as conn:
        cursor = await conn.cursor()
        await cursor.execute('INSERT OR IGNORE INTO stats (user_id, question_count, last_question_at) VALUES (?, 1, CURRENT_TIMESTAMP)', (user_id,))
        await cursor.execute('UPDATE stats SET question_count = question_count + 1, last_question_at = CURRENT_TIMESTAMP WHERE user_id = ?', (user_id,))
        await cursor.execute('UPDATE global_stats SET total_questions = total_questions + 1, questions_today = questions_today + 1 WHERE id = 1')
        await conn.commit()

async def get_stats() -> dict:
    async with aiosqlite.connect(DB_PATH, timeout=10) as conn:
        cursor = await conn.cursor()
        await cursor.execute('SELECT total_questions, questions_today FROM global_stats WHERE id = 1')
        row = await cursor.fetchone()
        total_q, today_q = row if row else (0, 0)
        approved_count = len(await load_approved_users())
        return {"total_questions": total_q, "questions_today": today_q, "total_users": approved_count}

async def get_user_stats(user_id: int) -> dict:
    async with aiosqlite.connect(DB_PATH, timeout=10) as conn:
        cursor = await conn.cursor()
        await cursor.execute('SELECT question_count, last_question_at FROM stats WHERE user_id = ?', (user_id,))
        row = await cursor.fetchone()
        if row:
            return {"question_count": row[0], "last_question_at": row[1]}
        return {"question_count": 0, "last_question_at": None}

# ============ KNOWLEDGE & RATE LIMIT ============

async def load_knowledge():
    global KNOWLEDGE_TEXT
    try:
        with open("knowledge.txt", "r", encoding="utf-8") as f:
            KNOWLEDGE_TEXT = f.read()
        logger.info("Knowledge base loaded")
    except FileNotFoundError:
        logger.warning("knowledge.txt not found, using empty knowledge base")
        KNOWLEDGE_TEXT = ""
    except Exception as e:
        logger.error(f"Load knowledge error: {e}")
        KNOWLEDGE_TEXT = ""

def check_rate_limit(user_id: int):
    now = datetime.now()
    if user_id not in USER_COOLDOWNS:
        USER_COOLDOWNS[user_id] = []
    USER_COOLDOWNS[user_id] = [t for t in USER_COOLDOWNS[user_id] if (now - t).total_seconds() < RATE_LIMIT_TIMEOUT]
    if len(USER_COOLDOWNS[user_id]) >= RATE_LIMIT_PER_MINUTE:
        oldest = USER_COOLDOWNS[user_id][0]
        remaining = int(RATE_LIMIT_TIMEOUT - (now - oldest).total_seconds())
        return True, max(0, remaining)
    USER_COOLDOWNS[user_id].append(now)
    return False, 0

# ============ AI & WELCOME ============

async def send_welcome(bot, user_id: int):
    try:
        await bot.send_message(
            user_id,
            UI_TEXTS["welcome_msg"],
            parse_mode="Markdown",
            reply_markup=main_menu_keyboard()
        )
    except Exception as e:
        logger.error(f"Welcome send error: {e}")

async def ask_ai(user_message: str, user_id: int, image_data: str = None) -> str:
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    history = await get_conversation_history(user_id, limit=4)
    if history:
        messages.extend(history)

    prompt = f"Game knowledge base:\n{KNOWLEDGE_TEXT}\n\nUser question: {user_message}"

    if image_data:
        msg_content = [
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_data}"}},
            {"type": "text", "text": prompt}
        ]
    else:
        msg_content = [{"type": "text", "text": prompt}]

    messages.append({"role": "user", "content": msg_content})

    async def try_model(model):
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.post(
                    "https://openrouter.ai/api/v1/chat/completions",
                    headers={
                        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                        "Content-Type": "application/json",
                        "HTTP-Referer": "https://t.me/lom_helper_mushroom_bot",
                        "X-Title": "LoM Shroom Helper",
                    },
                    json={"model": model, "messages": messages, "max_tokens": 700}
                )
            data = response.json()
            if "choices" in data and data["choices"]:
                logger.info(f"✓ Model {model} success")
                return data["choices"][0]["message"]["content"]
            return None
        except asyncio.TimeoutError:
            logger.warning(f"Model {model} timeout")
            return None
        except Exception as e:
            logger.warning(f"Model {model} error: {e}")
            return None

    for model in MODELS:
        result = await try_model(model)
        if result:
            return result
    return None

# ============ MENUS ============

def main_menu_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(UI_TEXTS["classes_btn"], callback_data="menu_classes"),
         InlineKeyboardButton(UI_TEXTS["builds_btn"], callback_data="menu_builds")],
        [InlineKeyboardButton(UI_TEXTS["pals_btn"], callback_data="menu_pals"),
         InlineKeyboardButton(UI_TEXTS["events_btn"], callback_data="menu_events")],
        [InlineKeyboardButton(UI_TEXTS["beginner_btn"], callback_data="menu_beginner"),
         InlineKeyboardButton(UI_TEXTS["help_btn"], callback_data="menu_help")],
    ])

def classes_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🗡️ Воин", callback_data="class_warrior"),
         InlineKeyboardButton("🏹 Лучник", callback_data="class_archer")],
        [InlineKeyboardButton("🔮 Маг", callback_data="class_mage"),
         InlineKeyboardButton("🐉 Укротитель", callback_data="class_tamer")],
        [InlineKeyboardButton(UI_TEXTS["back_btn"], callback_data="menu_main")],
    ])

def warrior_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🛡️ Боевой Мудрец", callback_data="class_martial_sage"),
         InlineKeyboardButton("⚔️ Вестник Войны", callback_data="class_warbringer")],
        [InlineKeyboardButton(UI_TEXTS["back_btn"], callback_data="menu_classes")],
    ])

def archer_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🌿 Священный Охотник", callback_data="class_sacred_hunter"),
         InlineKeyboardButton("🪶 Повелитель Перьев", callback_data="class_plume")],
        [InlineKeyboardButton(UI_TEXTS["back_btn"], callback_data="menu_classes")],
    ])

def mage_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✨ Пророк", callback_data="class_prophet"),
         InlineKeyboardButton("🌑 Тёмный Владыка", callback_data="class_darklord")],
        [InlineKeyboardButton(UI_TEXTS["back_btn"], callback_data="menu_classes")],
    ])

def tamer_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🐾 Повелитель Зверей", callback_data="class_beastmaster"),
         InlineKeyboardButton("💀 Верховный Дух", callback_data="class_supreme")],
        [InlineKeyboardButton(UI_TEXTS["back_btn"], callback_data="menu_classes")],
    ])

CLASS_INFO = {
    "class_warrior": ("⚔️ Воин", "Работает на контрударах — наносит урон в ответ на атаки врага.\n\nОтлично против Лучников.\n\nКлючевые статы: Атака, Урон контрудара, Урон крита, Защита\nСнаряжение: Контрудар / Крит шанс\n\nВыбери подкласс:", warrior_keyboard),
    "class_archer": ("🏹 Лучник", "Работает на комбо — дополнительные выстрелы, высокая скорость атаки.\n\nЛучший класс для ПвЕ и начала игры!\n\nКлючевые статы: Атака, Урон комбо, Урон крита, Скорость атаки\nСнаряжение: Комбо / Крит шанс\n\nВыбери подкласс:", archer_keyboard),
    "class_mage": ("🔮 Маг", "Большой взрывной урон через скиллы. Отличен в начале игры.\n\nКлючевые статы: Атака, Урон скилла, Крит урон скилла\nСнаряжение: Крит скилла / Оглушение\n\nВыбери подкласс:", mage_keyboard),
    "class_tamer": ("🐉 Укротитель", "Урон через питомцев. Хорош после разблокировки розовых питомцев.\n\nКлючевые статы: Атака, Урон питомца, Крит урон питомца, Здоровье\nСнаряжение: Комбо питомца / Крит питомца\n\nВыбери подкласс:", tamer_keyboard),
    "class_martial_sage": ("🛡️ Боевой Мудрец", "Роль: Танк с регенерацией\n\n✅ Высокая выживаемость, силён против большинства\n❌ Нужно вечное снаряжение и 25k+ монет\n\nКлючевые статы: Здоровье, Регенерация, Крит сопротивление\nСнаряжение: Контрудар & Регенерация\nМолитвенная статуя: Здоровье x5\nАвиан: Медовый Воин", None),
    "class_warbringer": ("⚔️ Вестник Войны", "Роль: Танковый DPS\n\n✅ Очень силён против Лучников и Повелителей Зверей\n❌ Слабее против Танков и Магов\n\n🟢 Можно играть сразу!\n\nКлючевые статы: Атака, Урон контрудара, Урон крита, Защита\nСнаряжение: Контрудар & Крит шанс\nМолитвенная статуя: Урон контрудара x5\nАвиан: Лунный Дух", None),
    "class_sacred_hunter": ("🌿 Священный Охотник", "Роль: Гибридный Танк\n\n✅ Высокая выживаемость, силён против Магов и Боевых Мудрецов\n❌ Нужно вечное снаряжение и 25k+ монет\n\nКлючевые статы: Здоровье, Регенерация, Крит сопротивление\nСнаряжение: Уклонение & Регенерация\nМолитвенная статуя: Здоровье x5\nАвиан: Медовый Воин", None),
    "class_plume": ("🪶 Повелитель Перьев", "Роль: Стеклянная пушка DPS\n\n✅ Лучший для ПвЕ, силён против Магов и Танков\n❌ Уязвим к Обезоруживанию, слабее против Вестника Войны\n\n🟢 Рекомендуется начинать сразу!\n\nКлючевые статы: Атака, Урон комбо, Урон крита, Скорость атаки\nСнаряжение: Комбо & Крит шанс\nМолитвенная статуя: Урон комбо x5", None),
    "class_prophet": ("✨ Пророк", "Роль: Высокоурон + Танк\n\n✅ Высокая выживаемость, силён против Вестника Войны и Танков\n❌ Ограничен против Священного Охотника\n\nКлючевые статы: Атака, Урон скилла, Здоровье, Регенерация\nСнаряжение: Крит скилла & Регенерация\nМолитвенная статуя: Атака x5\nАвиан: Тыквенная Ведьма", None),
    "class_darklord": ("🌑 Тёмный Владыка", "Роль: Убийца одним ударом\n\n✅ Очень силён в начале-середине игры\n❌ Труднее со временем, слабее против Лучников\n\nКлючевые статы: Атака, Урон скилла, Крит урон скилла, Оглушение\nСнаряжение: Крит скилла & Оглушение\nМолитвенная статуя: Атака x5\nАвиан: Тыквенная Ведьма", None),
    "class_beastmaster": ("🐾 Повелитель Зверей", "Роль: Стеклянная пушка DPS\n\n✅ Силён против Магов и Танков\n❌ Страдает от контрударов Вестника Войны\n\nРекомендуется после питомцев 200+ уровня\n\nКлючевые статы: Атака, Урон питомца, Крит урон питомца\nСнаряжение: Комбо питомца & Крит питомца\nМолитвенная статуя: Атака x5", None),
    "class_supreme": ("💀 Верховный Дух", "Роль: DPS Танк\n\n✅ Быстрее наносит урон чем другие танки, очень силён против Танков\n❌ Меньше танковых пассивов, нужно вечное снаряжение\n\nКлючевые статы: Здоровье, Регенерация, Крит сопротивление\nСнаряжение: Комбо питомца & Регенерация", None)
}

# ============ MAIN (ЗАПУСК БОТА) ============

async def main():
    # Инициализация базы данных и загрузка знаний
    await init_db()
    await load_knowledge()

    # Создание приложения бота
    application = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    # ВАЖНО: Здесь вы должны зарегистрировать свои обработчики команд
    # (если они описаны в другой части проекта, импортируйте их)
    # Например:
    # application.add_handler(CommandHandler("start", start_command))

    logger.info("Бот успешно запущен и работает!")
    
    # Запуск бота
    await application.run_polling()

if __name__ == '__main__':
    # Запускаем асинхронный цикл
    asyncio.run(main())
import os
import base64
import logging
import json
import httpx
import sqlite3
import asyncio
import threading
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
DB_LOCK = threading.Lock()

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

# ============ DATABASE ============

def init_db():
    with DB_LOCK:
        try:
            conn = sqlite3.connect(DB_PATH, timeout=10)
            cursor = conn.cursor()
            cursor.execute('''CREATE TABLE IF NOT EXISTS approved_users
                            (user_id INTEGER PRIMARY KEY, username TEXT, approved_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
            cursor.execute('''CREATE TABLE IF NOT EXISTS stats
                            (user_id INTEGER PRIMARY KEY, question_count INTEGER DEFAULT 0, last_question_at TIMESTAMP)''')
            cursor.execute('''CREATE TABLE IF NOT EXISTS conversations
                            (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, role TEXT, content TEXT, timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
            cursor.execute('''CREATE TABLE IF NOT EXISTS global_stats
                            (id INTEGER PRIMARY KEY, total_questions INTEGER DEFAULT 0, questions_today INTEGER DEFAULT 0, last_reset_date TEXT)''')
            cursor.execute('INSERT OR IGNORE INTO global_stats (id, total_questions, questions_today) VALUES (1, 0, 0)')
            conn.commit()
            conn.close()
            logger.info("Database initialized")
        except Exception as e:
            logger.error(f"DB init error: {e}")

def load_approved_users() -> set:
    with DB_LOCK:
        try:
            conn = sqlite3.connect(DB_PATH, timeout=10)
            cursor = conn.cursor()
            cursor.execute('SELECT user_id FROM approved_users')
            users = set(row[0] for row in cursor.fetchall())
            conn.close()
            return users
        except Exception as e:
            logger.error(f"Load users error: {e}")
            return set()

def save_approved_user(user_id: int, username: str = ""):
    with DB_LOCK:
        try:
            conn = sqlite3.connect(DB_PATH, timeout=10)
            cursor = conn.cursor()
            cursor.execute('INSERT OR IGNORE INTO approved_users (user_id, username) VALUES (?, ?)', (user_id, username))
            conn.commit()
            conn.close()
        except Exception as e:
            logger.error(f"Save user error: {e}")

def revoke_approved_user(user_id: int):
    with DB_LOCK:
        try:
            conn = sqlite3.connect(DB_PATH, timeout=10)
            cursor = conn.cursor()
            cursor.execute('DELETE FROM approved_users WHERE user_id = ?', (user_id,))
            conn.commit()
            conn.close()
        except Exception as e:
            logger.error(f"Revoke user error: {e}")

def is_approved(user_id: int) -> bool:
    if user_id == ADMIN_ID:
        return True
    return user_id in load_approved_users()

def add_conversation(user_id: int, role: str, content: str):
    with DB_LOCK:
        try:
            conn = sqlite3.connect(DB_PATH, timeout=10)
            cursor = conn.cursor()
            cursor.execute('INSERT INTO conversations (user_id, role, content) VALUES (?, ?, ?)', (user_id, role, content))
            conn.commit()
            conn.close()
        except Exception as e:
            logger.error(f"Save conversation error: {e}")

def get_conversation_history(user_id: int, limit: int = 6) -> list:
    with DB_LOCK:
        try:
            conn = sqlite3.connect(DB_PATH, timeout=10)
            cursor = conn.cursor()
            cursor.execute('SELECT role, content FROM conversations WHERE user_id = ? ORDER BY timestamp DESC LIMIT ?', (user_id, limit))
            history = [{"role": row[0], "content": row[1]} for row in reversed(cursor.fetchall())]
            conn.close()
            return history
        except Exception as e:
            logger.error(f"Get history error: {e}")
            return []

def increment_stats(user_id: int):
    with DB_LOCK:
        try:
            conn = sqlite3.connect(DB_PATH, timeout=10)
            cursor = conn.cursor()
            # FIX: добавлен аргумент user_id
            cursor.execute('INSERT OR IGNORE INTO stats (user_id, question_count, last_question_at) VALUES (?, 1, CURRENT_TIMESTAMP)', (user_id,))
            cursor.execute('UPDATE stats SET question_count = question_count + 1, last_question_at = CURRENT_TIMESTAMP WHERE user_id = ?', (user_id,))
            cursor.execute('UPDATE global_stats SET total_questions = total_questions + 1, questions_today = questions_today + 1 WHERE id = 1')
            conn.commit()
            conn.close()
        except Exception as e:
            logger.error(f"Stats error: {e}")

def get_stats() -> dict:
    with DB_LOCK:
        try:
            conn = sqlite3.connect(DB_PATH, timeout=10)
            cursor = conn.cursor()
            cursor.execute('SELECT total_questions, questions_today FROM global_stats WHERE id = 1')
            row = cursor.fetchone()
            total_q, today_q = row if row else (0, 0)
            conn.close()
            approved_count = len(load_approved_users())
            return {"total_questions": total_q, "questions_today": today_q, "total_users": approved_count}
        except Exception as e:
            logger.error(f"Get stats error: {e}")
            return {"total_questions": 0, "questions_today": 0, "total_users": 0}

def load_knowledge() -> str:
    try:
        with open("knowledge.txt", "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        logger.warning("knowledge.txt not found")
        return ""
    except Exception as e:
        logger.error(f"Load knowledge error: {e}")
        return ""

# ============ RATE LIMITING ============

def is_rate_limited(user_id: int) -> bool:
    now = datetime.now()
    if user_id not in USER_COOLDOWNS:
        USER_COOLDOWNS[user_id] = []
    USER_COOLDOWNS[user_id] = [t for t in USER_COOLDOWNS[user_id] if (now - t).total_seconds() < RATE_LIMIT_TIMEOUT]
    if len(USER_COOLDOWNS[user_id]) >= RATE_LIMIT_PER_MINUTE:
        return True
    USER_COOLDOWNS[user_id].append(now)
    return False

# ============ AI ============

async def ask_ai(user_message: str, knowledge: str, user_id: int, image_data: str = None) -> str:
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    history = get_conversation_history(user_id, limit=4)
    if history:
        messages.extend(history)

    prompt = f"Game knowledge base:\n{knowledge}\n\nUser question: {user_message}"

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
        [InlineKeyboardButton("⚔️ Классы", callback_data="menu_classes"),
         InlineKeyboardButton("🏹 Билды", callback_data="menu_builds")],
        [InlineKeyboardButton("🐾 Питомцы", callback_data="menu_pals"),
         InlineKeyboardButton("📅 Ивенты", callback_data="menu_events")],
        [InlineKeyboardButton("💡 Советы новичку", callback_data="menu_beginner"),
         InlineKeyboardButton("❓ Помощь", callback_data="menu_help")],
    ])

def classes_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🗡️ Воин", callback_data="class_warrior"),
         InlineKeyboardButton("🏹 Лучник", callback_data="class_archer")],
        [InlineKeyboardButton("🔮 Маг", callback_data="class_mage"),
         InlineKeyboardButton("🐉 Укротитель", callback_data="class_tamer")],
        [InlineKeyboardButton("🔙 Назад", callback_data="menu_main")],
    ])

def warrior_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🛡️ Боевой Мудрец", callback_data="class_martial_sage"),
         InlineKeyboardButton("⚔️ Вестник Войны", callback_data="class_warbringer")],
        [InlineKeyboardButton("🔙 Назад", callback_data="menu_classes")],
    ])

def archer_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🌿 Священный Охотник", callback_data="class_sacred_hunter"),
         InlineKeyboardButton("🪶 Повелитель Перьев", callback_data="class_plume")],
        [InlineKeyboardButton("🔙 Назад", callback_data="menu_classes")],
    ])

def mage_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✨ Пророк", callback_data="class_prophet"),
         InlineKeyboardButton("🌑 Тёмный Владыка", callback_data="class_darklord")],
        [InlineKeyboardButton("🔙 Назад", callback_data="menu_classes")],
    ])

def tamer_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🐾 Повелитель Зверей", callback_data="class_beastmaster"),
         InlineKeyboardButton("💀 Верховный Дух", callback_data="class_supreme")],
        [InlineKeyboardButton("🔙 Назад", callback_data="menu_classes")],
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
    "class_supreme": ("💀 Верховный Дух", "Роль: DPS Танк\n\n✅ Быстрее наносит урон чем другие танки, очень силён против Танков\n❌ Меньше танковых пассивов, нужно вечное снаряжение\n\nКлючевые статы: Здоровье, Регенерация, Крит сопротивление\nСнаряжение: Комбо питомца & Регенерация\nМолитвенная статуя: Здоровье x5", None),
}

# ============ COMMANDS ============

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user_id = update.effective_user.id
        if is_approved(user_id):
            await update.message.reply_text(
                "🍄 Привет! Я Shroom Helper — твой помощник по Legend of Mushroom!\n\n"
                "Вся информация предоставлена zigi.\n\n"
                "📋 *Доступные команды:*\n"
                "/menu — главное меню\n"
                "/classes — обзор классов\n"
                "/builds — сборки по классам\n"
                "/feedback ТЕКСТ — отправить отзыв\n\n"
                "Задай вопрос или отправь скриншот вещей — помогу со сборкой! 👇",
                parse_mode="Markdown",
                reply_markup=main_menu_keyboard()
            )
        else:
            await update.message.reply_text(
                "🍄 Привет! Для доступа введи код:\n/code ТВОЙ_КОД\n\n"
                "Или запроси доступ у администратора:\n/request\n\n"
                "🍄 Hello! Enter access code:\n/code YOUR_CODE\n\nOr request access:\n/request"
            )
    except Exception as e:
        logger.error(f"Start error: {e}")

async def menu_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if not is_approved(update.effective_user.id):
            await update.message.reply_text("🔒 Нет доступа. Введи /code ТВОЙ_КОД")
            return
        await update.message.reply_text("🍄 Главное меню:", reply_markup=main_menu_keyboard())
    except Exception as e:
        logger.error(f"Menu error: {e}")

async def classes_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if not is_approved(update.effective_user.id):
            await update.message.reply_text("🔒 Нет доступа.")
            return
        await update.message.reply_text("⚔️ Выбери класс:", reply_markup=classes_keyboard())
    except Exception as e:
        logger.error(f"Classes error: {e}")

async def builds_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if not is_approved(update.effective_user.id):
            await update.message.reply_text("🔒 Нет доступа.")
            return
        text = (
            "🏹 *Быстрый гайд по сборкам:*\n\n"
            "🪶 *Повелитель Перьев* — Комбо & Крит шанс\n"
            "⚔️ *Вестник Войны* — Контрудар & Крит шанс\n"
            "🛡️ *Боевой Мудрец* — Контрудар & Регенерация\n"
            "🌿 *Священный Охотник* — Уклонение & Регенерация\n"
            "✨ *Пророк* — Крит скилла & Регенерация\n"
            "🌑 *Тёмный Владыка* — Крит скилла & Оглушение\n"
            "🐾 *Повелитель Зверей* — Комбо питомца & Крит питомца\n"
            "💀 *Верховный Дух* — Комбо питомца & Регенерация\n\n"
            "Задай вопрос для подробностей по любому классу! 🍄"
        )
        await update.message.reply_text(text, parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Builds error: {e}")

async def code_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user_id = update.effective_user.id
        user_name = update.effective_user.full_name
        if is_approved(user_id):
            await update.message.reply_text("✅ У тебя уже есть доступ!")
            return
        if not context.args:
            await update.message.reply_text("Введи код: /code ТВОЙ_КОД")
            return
        if context.args[0] == ACCESS_CODE:
            save_approved_user(user_id, user_name)
            await update.message.reply_text("✅ Код верный! Добро пожаловать 🍄\nНажми /menu!")
            if ADMIN_ID:
                try:
                    await context.bot.send_message(ADMIN_ID, f"✅ Новый пользователь по коду:\n👤 {user_name}\n🆔 {user_id}")
                except:
                    pass
        else:
            await update.message.reply_text("❌ Неверный код.")
            if ADMIN_ID:
                try:
                    keyboard = [[
                        InlineKeyboardButton("✅ Разрешить", callback_data=f"approve_{user_id}"),
                        InlineKeyboardButton("❌ Отклонить", callback_data=f"deny_{user_id}")
                    ]]
                    await context.bot.send_message(ADMIN_ID,
                        f"⚠️ Неверный код:\n👤 {user_name}\n🆔 {user_id}\n\nРазрешить доступ?",
                        reply_markup=InlineKeyboardMarkup(keyboard))
                except:
                    pass
    except Exception as e:
        logger.error(f"Code error: {e}")

async def request_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user_id = update.effective_user.id
        user_name = update.effective_user.full_name
        if is_approved(user_id):
            await update.message.reply_text("✅ У тебя уже есть доступ!")
            return
        if not ADMIN_ID:
            await update.message.reply_text("❌ Используй /code")
            return
        keyboard = [[
            InlineKeyboardButton("✅ Разрешить", callback_data=f"approve_{user_id}"),
            InlineKeyboardButton("❌ Отклонить", callback_data=f"deny_{user_id}")
        ]]
        await context.bot.send_message(ADMIN_ID,
            f"🔔 Запрос доступа:\n👤 {user_name}\n🆔 {user_id}",
            reply_markup=InlineKeyboardMarkup(keyboard))
        await update.message.reply_text("✅ Запрос отправлен! Ожидай одобрения.")
    except Exception as e:
        logger.error(f"Request error: {e}")

async def feedback_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if not is_approved(update.effective_user.id):
            await update.message.reply_text("🔒 Нет доступа.")
            return
        if not context.args:
            await update.message.reply_text("Напиши отзыв: /feedback ТВОЙ_ОТЗЫВ")
            return
        user_name = update.effective_user.full_name
        user_id = update.effective_user.id
        feedback_text = " ".join(context.args)
        if ADMIN_ID:
            await context.bot.send_message(ADMIN_ID, f"💬 Отзыв от {user_name} ({user_id}):\n\n{feedback_text}")
            await update.message.reply_text("✅ Отзыв отправлен! Спасибо 🍄")
    except Exception as e:
        logger.error(f"Feedback error: {e}")

async def approve_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if update.effective_user.id != ADMIN_ID:
            return
        if not context.args:
            await update.message.reply_text("Использование: /approve USER_ID")
            return
        target_id = int(context.args[0])
        save_approved_user(target_id)
        await update.message.reply_text(f"✅ Пользователь {target_id} одобрен!")
        try:
            await context.bot.send_message(target_id, "✅ Доступ одобрен! Добро пожаловать 🍄\nНажми /menu!")
        except:
            pass
    except Exception as e:
        logger.error(f"Approve error: {e}")

async def revoke_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if update.effective_user.id != ADMIN_ID:
            return
        if not context.args:
            await update.message.reply_text("Использование: /revoke USER_ID")
            return
        target_id = int(context.args[0])
        revoke_approved_user(target_id)
        await update.message.reply_text(f"✅ Доступ {target_id} отозван!")
    except Exception as e:
        logger.error(f"Revoke error: {e}")

async def users_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if update.effective_user.id != ADMIN_ID:
            return
        approved = load_approved_users()
        if approved:
            await update.message.reply_text(f"👥 Пользователей: {len(approved)}\n\n" + "\n".join(str(u) for u in sorted(approved)))
        else:
            await update.message.reply_text("Нет пользователей.")
    except Exception as e:
        logger.error(f"Users error: {e}")

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if update.effective_user.id != ADMIN_ID:
            return
        stats = get_stats()
        await update.message.reply_text(
            f"📊 *Статистика бота:*\n\n"
            f"👥 Пользователей: {stats['total_users']}\n"
            f"💬 Всего вопросов: {stats['total_questions']}\n"
            f"📅 Вопросов сегодня: {stats['questions_today']}",
            parse_mode="Markdown"
        )
    except Exception as e:
        logger.error(f"Stats error: {e}")

async def broadcast_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if update.effective_user.id != ADMIN_ID:
            return
        if not context.args:
            await update.message.reply_text("Использование: /broadcast ТЕКСТ")
            return
        text = "📢 " + " ".join(context.args)
        approved = load_approved_users()
        sent = 0
        for user_id in approved:
            try:
                await context.bot.send_message(user_id, text)
                sent += 1
            except:
                pass
        await update.message.reply_text(f"✅ Отправлено {sent}/{len(approved)}!")
    except Exception as e:
        logger.error(f"Broadcast error: {e}")

# ============ CALLBACKS ============

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        query = update.callback_query
        await query.answer()
        data = query.data

        if data == "menu_main":
            await query.edit_message_text("🍄 Главное меню:", reply_markup=main_menu_keyboard())
        elif data == "menu_classes":
            await query.edit_message_text("⚔️ Выбери класс:", reply_markup=classes_keyboard())
        elif data == "menu_builds":
            text = (
                "🏹 *Быстрый гайд по сборкам:*\n\n"
                "🪶 *Повелитель Перьев* — Комбо & Крит шанс\n"
                "⚔️ *Вестник Войны* — Контрудар & Крит шанс\n"
                "🛡️ *Боевой Мудрец* — Контрудар & Регенерация\n"
                "🌿 *Священный Охотник* — Уклонение & Регенерация\n"
                "✨ *Пророк* — Крит скилла & Регенерация\n"
                "🌑 *Тёмный Владыка* — Крит скилла & Оглушение\n"
                "🐾 *Повелитель Зверей* — Комбо питомца & Крит питомца\n"
                "💀 *Верховный Дух* — Комбо питомца & Регенерация"
            )
            await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="menu_main")]]))
        elif data == "menu_pals":
            text = (
                "🐾 *Питомцы (Pals):*\n\n"
                "Питомцы дают пассивные бонусы и наносят урон.\n\n"
                "🔑 Разблокируй Розовых питомцев для Укротителя\n"
                "📈 Повелитель Зверей — лучше с питомцами 200+ уровня\n"
                "💀 Верховный Дух — зависит от расстановки питомцев\n\n"
                "Задай вопрос для подробностей! 🍄"
            )
            await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="menu_main")]]))
        elif data == "menu_events":
            await query.edit_message_text("📅 Задай вопрос об актуальных ивентах и я расскажу что знаю! 🍄", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="menu_main")]]))
        elif data == "menu_beginner":
            text = (
                "💡 *Советы новичку:*\n\n"
                "🏹 Начни с Лучника — лучший для ПвЕ\n"
                "🤝 Вступи в альянс как можно раньше\n"
                "⚡ На уровне 30 — первое разветвление классов\n"
                "⚡ На уровне 50 — финальное разветвление\n"
                "🪶 Повелитель Перьев — лучший старт\n"
                "⚔️ Вестник Войны — тоже можно сразу\n"
                "🛡️ Танки — только с вечным снаряжением"
            )
            await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="menu_main")]]))
        elif data == "menu_help":
            text = (
                "❓ *Помощь:*\n\n"
                "/menu — открыть меню\n"
                "/classes — обзор классов\n"
                "/builds — быстрый гайд по сборкам\n"
                "/feedback ТЕКСТ — отправить отзыв\n"
                "/request — запросить доступ\n\n"
                "Просто напиши вопрос и я отвечу! 🍄"
            )
            await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="menu_main")]]))
        elif data in ("class_warrior", "class_archer", "class_mage", "class_tamer"):
            info = CLASS_INFO[data]
            await query.edit_message_text(f"{info[0]}\n\n{info[1]}", reply_markup=info[2]())
        elif data in CLASS_INFO:
            info = CLASS_INFO[data]
            back_map = {
                "class_martial_sage": "class_warrior", "class_warbringer": "class_warrior",
                "class_sacred_hunter": "class_archer", "class_plume": "class_archer",
                "class_prophet": "class_mage", "class_darklord": "class_mage",
                "class_beastmaster": "class_tamer", "class_supreme": "class_tamer",
            }
            back = back_map.get(data, "menu_classes")
            await query.edit_message_text(f"{info[0]}\n\n{info[1]}", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data=back)]]))
        elif data.startswith("approve_") and update.effective_user.id == ADMIN_ID:
            target_id = int(data.split("_")[1])
            save_approved_user(target_id)
            await query.edit_message_text(f"✅ Пользователь {target_id} одобрен!")
            try:
                await context.bot.send_message(target_id, "✅ Доступ одобрен! Добро пожаловать 🍄\nНажми /menu!")
            except:
                pass
        elif data.startswith("deny_") and update.effective_user.id == ADMIN_ID:
            target_id = int(data.split("_")[1])
            await query.edit_message_text(f"❌ Пользователь {target_id} отклонён.")
            try:
                await context.bot.send_message(target_id, "❌ Администратор отклонил твой запрос.")
            except:
                pass
    except Exception as e:
        logger.error(f"Button error: {e}")

# ============ MESSAGE HANDLER ============

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        message = update.message
        if message is None:
            return

        user_id = update.effective_user.id
        bot_username = context.bot.username
        is_private = message.chat.type == "private"
        is_mention = message.text and f"@{bot_username}" in message.text
        is_reply_to_bot = (
            message.reply_to_message and
            message.reply_to_message.from_user and
            message.reply_to_message.from_user.username == bot_username
        )
        has_photo = message.photo is not None and len(message.photo) > 0

        if not is_private and not is_mention and not is_reply_to_bot:
            return

        if is_rate_limited(user_id):
            await message.reply_text("⏳ Подождите немного...")
            return

        if not is_approved(user_id):
            await message.reply_text("🔒 Нет доступа.\n/code ТВОЙ_КОД или /request")
            return

        user_message = message.text or message.caption or ""
        if is_mention:
            user_message = user_message.replace(f"@{bot_username}", "").strip()

        # FIX: обработка скриншотов восстановлена
        image_data = None
        if has_photo:
            try:
                photo = message.photo[-1]
                file = await context.bot.get_file(photo.file_id)
                async with httpx.AsyncClient() as client:
                    img_resp = await client.get(file.file_path)
                    image_data = base64.b64encode(img_resp.content).decode("utf-8")
                if not user_message:
                    user_message = "Помоги сделать сборку из вещей на скриншоте."
            except Exception as e:
                logger.error(f"Image error: {e}")

        if not user_message and not image_data:
            await message.reply_text("🍄 Задай вопрос или открой /menu")
            return

        await message.chat.send_action("typing")
        knowledge = load_knowledge()
        reply = await ask_ai(user_message, knowledge, user_id, image_data)

        if reply:
            add_conversation(user_id, "user", user_message)
            add_conversation(user_id, "assistant", reply)
            increment_stats(user_id)
            await message.reply_text(reply)
        else:
            await message.reply_text("⚠️ Все модели перегружены. Попробуй через минуту!")
    except Exception as e:
        logger.error(f"Message error: {e}")

# ============ MAIN ============

if __name__ == "__main__":
    init_db()

    if not TELEGRAM_TOKEN:
        logger.error("❌ TELEGRAM_TOKEN not set!")
        exit(1)

    logger.info("🍄 Starting bot...")
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("menu", menu_command))
    app.add_handler(CommandHandler("classes", classes_command))
    app.add_handler(CommandHandler("builds", builds_command))
    app.add_handler(CommandHandler("code", code_command))
    app.add_handler(CommandHandler("request", request_command))
    app.add_handler(CommandHandler("feedback", feedback_command))
    app.add_handler(CommandHandler("approve", approve_command))
    app.add_handler(CommandHandler("revoke", revoke_command))
    app.add_handler(CommandHandler("users", users_command))
    app.add_handler(CommandHandler("stats", stats_command))
    app.add_handler(CommandHandler("broadcast", broadcast_command))
    app.add_handler(CallbackQueryHandler(button_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(MessageHandler(filters.PHOTO, handle_message))

    logger.info("🍄 Bot started!")
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)

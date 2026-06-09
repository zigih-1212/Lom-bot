import os
import base64
import logging
import json
import httpx
import asyncio
import aiosqlite
import discord  # Подключаем Дискорд
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes

# Настройка логов
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.FileHandler('bot.log'), logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

# Переменные окружения (Токены и ID)
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY")
ADMIN_ID = int(os.environ.get("ADMIN_ID", "0"))
ACCESS_CODE = os.environ.get("ACCESS_CODE", "shroom2024")

# Настройки для Дискорд-моста
DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN")
DISCORD_CHANNEL_ID = int(os.environ.get("DISCORD_CHANNEL_ID", "0"))

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

# Глобальные переменные для баз данных
KNOWLEDGE_TEXT = ""
DYNAMIC_EVENTS = "Свежих новостей об ивентах пока не поступало." 

SYSTEM_PROMPT = """You are Shroom Helper — a helpful assistant for the mobile game Legend of Mushroom (LoM).

RULES:
1. A knowledge base is provided below. Search it SEMANTICALLY — understand what the user is asking about even if they use different words or ask vaguely.
2. Never mention any website, source, or URL. If asked where you get info, say "zigi provided this information".
3. Only if the knowledge base truly has NO relevant info, say: in Russian — "У меня пока нет такой информации. Спроси у zigi — он добавит!", in English — "I don't have that info yet. Ask zigi to add it!"
4. Language: detect user's language and reply in the SAME language. Translate ALL game terms to Russian if user writes in Russian.
5. Simplify explanations — use plain, friendly language.
6. If user sends a screenshot with items/gear → analyze what's visible and give build advice based on the knowledge base.
7. Be friendly, helpful and use 🍄 occasionally.
8. Summarize and explain in your own simple words.
9. Use conversation history to give more relevant answers.
"""

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
    "no_access_msg": "🔒 Нет доступа.\nИспользуйте команду `/code ТВОЙ_КОД` или `/request` для запроса доступа.",
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

# База данных функции
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

async def load_approved_users() -> set:
    async with aiosqlite.connect(DB_PATH, timeout=10) as conn:
        cursor = await conn.cursor()
        await cursor.execute('SELECT user_id FROM approved_users')
        return set(row[0] for row in await cursor.fetchall())

async def save_approved_user(user_id: int, username: str = ""):
    async with aiosqlite.connect(DB_PATH, timeout=10) as conn:
        cursor = await conn.cursor()
        await cursor.execute('INSERT OR IGNORE INTO approved_users (user_id, username) VALUES (?, ?)', (user_id, username))
        await conn.commit()

async def is_approved(user_id: int) -> bool:
    if user_id == ADMIN_ID: return True
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
        return [{"role": row[0], "content": row[1]} for row in reversed(await cursor.fetchall())]

async def increment_stats(user_id: int):
    async with aiosqlite.connect(DB_PATH, timeout=10) as conn:
        cursor = await conn.cursor()
        await cursor.execute('INSERT OR IGNORE INTO stats (user_id, question_count, last_question_at) VALUES (?, 1, CURRENT_TIMESTAMP)', (user_id,))
        await cursor.execute('UPDATE stats SET question_count = question_count + 1, last_question_at = CURRENT_TIMESTAMP WHERE user_id = ?', (user_id,))
        await cursor.execute('UPDATE global_stats SET total_questions = total_questions + 1, questions_today = questions_today + 1 WHERE id = 1')
        await conn.commit()

# Загрузка локальных файлов знаний
async def load_knowledge():
    global KNOWLEDGE_TEXT, DYNAMIC_EVENTS
    try:
        with open("knowledge.txt", "r", encoding="utf-8") as f:
            KNOWLEDGE_TEXT = f.read()
        logger.info("Knowledge base loaded")
    except FileNotFoundError:
        KNOWLEDGE_TEXT = ""
    
    try:
        with open("latest_events.txt", "r", encoding="utf-8") as f:
            DYNAMIC_EVENTS = f.read()
        logger.info("Latest events loaded from file")
    except FileNotFoundError:
        DYNAMIC_EVENTS = "Актуальных ивентов в базе пока нет."

def check_rate_limit(user_id: int):
    now = datetime.now()
    if user_id not in USER_COOLDOWNS: USER_COOLDOWNS[user_id] = []
    USER_COOLDOWNS[user_id] = [t for t in USER_COOLDOWNS[user_id] if (now - t).total_seconds() < RATE_LIMIT_TIMEOUT]
    if len(USER_COOLDOWNS[user_id]) >= RATE_LIMIT_PER_MINUTE:
        return True, max(0, int(RATE_LIMIT_TIMEOUT - (now - USER_COOLDOWNS[user_id][0]).total_seconds()))
    USER_COOLDOWNS[user_id].append(now)
    return False, 0

async def send_welcome(bot, user_id: int):
    try:
        await bot.send_message(user_id, UI_TEXTS["welcome_msg"], parse_mode="Markdown", reply_markup=main_menu_keyboard())
    except Exception as e: logger.error(f"Welcome error: {e}")

# Класс моста для чтения Дискорда
class DiscordBridge(discord.Client):
    def __init__(self, *args, **kwargs):
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(intents=intents, *args, **kwargs)

    async def on_ready(self):
        logger.info(f"✓ Мост Discord успешно запущен: {self.user}")

    async def on_message(self, message):
        global DYNAMIC_EVENTS
        if message.author == self.user: return
        if message.channel.id == DISCORD_CHANNEL_ID:
            logger.info("Получено новое обновление ивентов из Discord!")
            DYNAMIC_EVENTS = message.content
            try:
                with open("latest_events.txt", "w", encoding="utf-8") as f:
                    f.write(DYNAMIC_EVENTS)
            except Exception as e: logger.error(f"Save events error: {e}")

# Отправка запроса к нейросети
async def ask_ai(user_message: str, user_id: int, image_data: str = None) -> str:
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    history = await get_conversation_history(user_id, limit=4)
    if history: messages.extend(history)

    prompt = (
        f"Game knowledge base:\n{KNOWLEDGE_TEXT}\n\n"
        f"Latest Live Events from Discord:\n{DYNAMIC_EVENTS}\n\n"
        f"User question: {user_message}"
    )

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
            if "choices" in data and data["choices"]: return data["choices"][0]["message"]["content"]
            return None
        except: return None

    for model in MODELS:
        res = await try_model(model)
        if res: return res
    return None

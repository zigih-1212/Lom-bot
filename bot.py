import os
import base64
import logging
import json
import httpx
import asyncio
import aiosqlite
import discord
from datetime import datetime
from bs4 import BeautifulSoup
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes

# Настройка логов
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.FileHandler('bot.log'), logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

# Переменные окружения
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
ADMIN_ID = int(os.environ.get("ADMIN_ID", "0"))
ACCESS_CODE = os.environ.get("ACCESS_CODE", "shroom2024")

DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN")
DISCORD_CHANNEL_ID = int(os.environ.get("DISCORD_CHANNEL_ID", "0"))
GUIDE_URL = os.environ.get("GUIDE_URL")

DB_PATH = "bot_data.db"

USER_COOLDOWNS = {}
RATE_LIMIT_PER_MINUTE = 10
RATE_LIMIT_TIMEOUT = 60

KNOWLEDGE_TEXT = ""
DYNAMIC_EVENTS = "Свежих новостей об ивентах пока не поступало." 

SYSTEM_PROMPT = """You are Shroom Helper — an expert AI assistant for the mobile game Legend of Mushroom (LoM).

🚨 ИГРОВОЙ СЛОВАРЬ СЛЕНГА:
- "Летучий питомец", "птица", "летун", "птичка", "пет-птица" = Авиан (Avian) / Дух.
- "Стрелок", "лук", "хант" = Лучник (подклассы: Повелитель Перьев, Священный Охотник).
- "Пет", "питомец", "пал", "спутник" = Питомцы (Pals).
- "Танк", "меч", "щит" = Воин (подклассы: Боевой Мудрец, Вестник Войны).
- "Колдун", "прокаст" = Маг (подклассы: Пророк, Тёмный Владыка).
- "Навыки", "скиллы", "кнопки", "активки" = Active-навыки персонажа.

📸 ПРАВИЛА АНАЛИЗА СКРИНШОТОВ (КРИТИЧЕСКИ ВАЖНО):
Ты обязан изучить картинку и дать подробный разбор:
- ШАГ 1: Перечисли, какие навыки/вещи/уровни (Lv.) ты отчетливо видишь на картинке. Если сомневаешься в названии на русском, опиши иконку визуально по цвету (например: фиолетовый череп Lv.19, зеленый кулак Lv.5).
- ШАГ 2: Дай железный совет для указанного подкласса игрока. Напиши, что именно убрать из верхних слотов, а что поставить из инвентаря снизу.

Отвечай строго на русском языке, используй списки и жирный шрифт. 🍄
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
        "Выбери интересующий раздел меню или нажми кнопку анализа скриншотов! 👇"
    ),
    "no_access_msg": "🔒 Нет доступа.",
    "rate_limit_msg": "⏳ Подождите...",
    "menu_title": "🍄 Главное меню:",
    "classes_title": "⚔️ Выбери класс:",
    "builds_title": "🏹 *Быстрый гайд по сборкам:*\n\n",
    "pals_title": "🐾 *Питомцы (Pals):*\n\n",
    "events_title": "📅 Ивенты:",
    "beginner_title": "💡 *Советы новичку...*",
    "help_title": "❓ *Помощь...*",
}

# ============ DATABASE (ASYNC) ============
async def init_db():
    async with aiosqlite.connect(DB_PATH, timeout=10) as conn:
        cursor = await conn.cursor()
        await cursor.execute('''CREATE TABLE IF NOT EXISTS approved_users (user_id INTEGER PRIMARY KEY, username TEXT, approved_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
        await cursor.execute('''CREATE TABLE IF NOT EXISTS stats (user_id INTEGER PRIMARY KEY, question_count INTEGER DEFAULT 0, last_question_at TIMESTAMP)''')
        await cursor.execute('''CREATE TABLE IF NOT EXISTS conversations (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, role TEXT, content TEXT, timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
        await conn.commit()

async def is_approved(user_id: int) -> bool:
    if user_id == ADMIN_ID: return True
    async with aiosqlite.connect(DB_PATH, timeout=10) as conn:
        cursor = await conn.cursor()
        await cursor.execute('SELECT user_id FROM approved_users WHERE user_id = ?', (user_id,))
        return await cursor.fetchone() is not None

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
        await conn.commit()

# ============ ПРЯМОЙ ОФИЦИАЛЬНЫЙ GOOGLE GEMINI API ============
async def ask_ai(user_message: str, user_id: int, image_data: str = None) -> str:
    if not GEMINI_API_KEY: return "⚙️ Ошибка Конфигурации: Не задан GEMINI_API_KEY."

    contents = []
    if image_data:
        contents.append({"role": "user", "parts": [{"text": user_message}, {"inlineData": {"mimeType": "image/jpeg", "data": image_data}}]})
    else:
        history = await get_conversation_history(user_id, limit=4)
        for h in history:
            contents.append({"role": "model" if h["role"] == "assistant" else "user", "parts": [{"text": h["content"]}]})
        contents.append({"role": "user", "parts": [{"text": user_message}]})

    payload = {
        "systemInstruction": {"parts": [{"text": SYSTEM_PROMPT}]},
        "contents": contents,
        "generationConfig": {
            "maxOutputTokens": 3072,
            "temperature": 0.3
        }
    }

    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={GEMINI_API_KEY}"

    try:
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.post(url, headers={"Content-Type": "application/json"}, json=payload)
        
        if response.status_code == 200:
            data = response.json()
            if "candidates" in data and data["candidates"]:
                return data["candidates"][0]["content"]["parts"][0]["text"]
        return f"❌ Ошибка Google API (Статус {response.status_code}): {response.text[:200]}"
    except Exception as e:
        return f"❌ Ошибка сети: {str(e)}"

# ============ КЛАВИАТУРЫ ============
def main_menu_keyboard():
    return InlineKeyboardMarkup([[InlineKeyboardButton("📸 АНАЛИЗ ФОТО НАВЫКОВ 📸", callback_data="photo_flow_start")]])

def photo_flow_classes_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🗡️ Воин", callback_data="p_main_warrior"), InlineKeyboardButton("🏹 Лучник", callback_data="p_main_archer")],
        [InlineKeyboardButton("🔮 Маг", callback_data="p_main_mage"), InlineKeyboardButton("🐉 Укротитель", callback_data="p_main_tamer")]
    ])

def p_warrior_keyboard(): return InlineKeyboardMarkup([[InlineKeyboardButton("🛡️ Боевой Мудрец", callback_data="p_flow_martial_sage"), InlineKeyboardButton("⚔️ Вестник Войны", callback_data="p_flow_warbringer")]])
def p_archer_keyboard(): return InlineKeyboardMarkup([[InlineKeyboardButton("🌿 Священный Охотник", callback_data="p_flow_sacred_hunter"), InlineKeyboardButton("🪶 Повелитель Перьев", callback_data="p_flow_plume")]])
def p_mage_keyboard(): return InlineKeyboardMarkup([[InlineKeyboardButton("✨ Пророк", callback_data="p_flow_prophet"), InlineKeyboardButton("🌑 Тёмный Владыка", callback_data="p_flow_darklord")]])
def p_tamer_keyboard(): return InlineKeyboardMarkup([[InlineKeyboardButton("🐾 Повелитель Зверей", callback_data="p_flow_beastmaster"), InlineKeyboardButton("💀 Верховный Дух", callback_data="p_flow_supreme")]])

CLASS_INFO = {}

# ============ ХЭНДЛЕРЫ ============
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    
    if data == "photo_flow_start":
        await query.edit_message_text("📸 Выбери основной класс:", reply_markup=photo_flow_classes_keyboard())
    elif data.startswith("p_main_"):
        kb_map = {"warrior": p_warrior_keyboard, "archer": p_archer_keyboard, "mage": p_mage_keyboard, "tamer": p_tamer_keyboard}
        await query.edit_message_text("🔮 Выбери подкласс:", reply_markup=kb_map[data.split("_")[2]]())
    elif data.startswith("p_flow_"):
        context.user_data['p_awaiting'] = True
        context.user_data['p_class'] = data.split("_")[2]
        await query.edit_message_text("📥 Отлично! Теперь просто отправь скриншот навыков.")

async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.photo and context.user_data.get('p_awaiting'):
        status_msg = await update.message.reply_text("🍄 Изучаю скриншот...")
        photo_bytes = await (await update.message.photo[-1].get_file()).download_as_bytearray()
        image_data = base64.b64encode(photo_bytes).decode("utf-8")
        
        class_map = {
            "martial_sage": "Боевой Мудрец", "warbringer": "Вестник Войны",
            "sacred_hunter": "Священный Охотник", "plume": "Повелитель Перьев",
            "prophet": "Пророк", "darklord": "Тёмный Владыка",
            "beastmaster": "Повелитель Зверей", "supreme": "Верховный Дух"
        }
        chosen_subclass = class_map.get(context.user_data.get('p_class'), "Повелитель Перьев")
        
        ai_prompt = (
            f"Внимательно изучи прикрепленный скриншот меню навыков Legend of Mushroom.\n"
            f"Игрок играет за точный подкласс: {chosen_subclass}.\n"
            f"Выполни задание: найди все иконки в инвентаре, определи их уровни (Lv.) и "
            f"дай подробные инструкции, что поставить в активные слоты для этого конкретного подкласса."
        )
        
        ai_response = await ask_ai(ai_prompt, update.effective_user.id, image_data)
        context.user_data['p_awaiting'] = False
        await status_msg.edit_text(ai_response, parse_mode="Markdown")
    elif update.message.text:
        await add_conversation(update.effective_user.id, "user", update.message.text)
        resp = await ask_ai(update.message.text, update.effective_user.id)
        await add_conversation(update.effective_user.id, "assistant", resp)
        await update.message.reply_text(resp, parse_mode="Markdown")

# ✅ ПРАВИЛЬНЫЙ АСИНХРОННЫЙ СТАРТ ПОСТ-ИНИЦИАЛИЗАЦИИ БЕЗ ОШИБОК LOOP
async def post_init(application):
    await init_db()
    logger.info("✓ База данных успешно инициализирована внутри рабочего цикла!")

def main():
    if not TELEGRAM_TOKEN: return
    application = ApplicationBuilder().token(TELEGRAM_TOKEN).post_init(post_init).build()
    application.add_handler(CommandHandler("start", lambda u, c: send_welcome(c.bot, u.effective_user.id)))
    application.add_handler(CommandHandler("menu", lambda u, c: u.message.reply_text("Меню:", reply_markup=main_menu_keyboard())))
    application.add_handler(CallbackQueryHandler(button_handler))
    application.add_handler(MessageHandler(filters.TEXT | filters.PHOTO, message_handler))
    application.run_polling()

if __name__ == '__main__':
    main()

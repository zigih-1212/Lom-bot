import os
import base64
import logging
import json
import httpx
import asyncio
import aiosqlite
import discord  # Подключаем Дискорд
from datetime import datetime
from bs4 import BeautifulSoup  # Подключаем очистку сайтов
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

# Настройки для Дискорд-моста и Сайта с гайдами
DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN")
DISCORD_CHANNEL_ID = int(os.environ.get("DISCORD_CHANNEL_ID", "0"))
GUIDE_URL = os.environ.get("GUIDE_URL")

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

# ============ УМНАЯ ЗАГРУЗКА ЗНАНИЙ С САЙТА ИЛИ ФАЙЛА ============
async def load_knowledge():
    global KNOWLEDGE_TEXT, DYNAMIC_EVENTS
    
    # 1. Скачиваем свежие гайды, если в настройках хостинга указан GUIDE_URL
    if GUIDE_URL:
        logger.info(f"Пробую обновить гайды с сайта: {GUIDE_URL}")
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                response = await client.get(GUIDE_URL)
                if response.status_code == 200:
                    if "docs.google.com" in GUIDE_URL and "format=txt" in GUIDE_URL:
                        KNOWLEDGE_TEXT = response.text
                    else:
                        soup = BeautifulSoup(response.text, 'html.parser')
                        for garbage in soup(["script", "style", "nav", "footer", "header"]):
                            garbage.extract()
                        KNOWLEDGE_TEXT = soup.get_text(separator="\n")
                    
                    with open("knowledge.txt", "w", encoding="utf-8") as f:
                        f.write(KNOWLEDGE_TEXT)
                    logger.info("✓ База знаний успешно обновлена напрямую с сайта!")
                else:
                    logger.warning(f"Сайт вернул ошибку {response.status_code}. Использую бэкап.")
        except Exception as e:
            logger.error(f"Не удалось скачать гайды из интернета ({e}). Загружаю локальный файл.")

    # 2. Если сайта нет или он упал — берем из файла локально
    if not KNOWLEDGE_TEXT:
        try:
            with open("knowledge.txt", "r", encoding="utf-8") as f:
                KNOWLEDGE_TEXT = f.read()
            logger.info("База знаний загружена из локального файла")
        except FileNotFoundError:
            KNOWLEDGE_TEXT = "База знаний пуста. Добавьте файлы или настройте GUIDE_URL."
    
    # 3. Загружаем ивенты из Дискорда
    try:
        with open("latest_events.txt", "r", encoding="utf-8") as f:
            DYNAMIC_EVENTS = f.read()
        logger.info("Последние события загружены из файла")
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

# ============ ДИСКОРД МОСТ ============
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

# ============ НЕЙРОСЕТЬ ИИ ============
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

# ============ КЛАВИАТУРЫ И КЛАССЫ ============
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
    "class_warrior": ("⚔️ Воин", "Работает на контрударах.\n\nКлючевые статы: Атака, Урон контрудара, Урон крита, Защита\nСнаряжение: Контрудар / Крит шанс", warrior_keyboard),
    "class_archer": ("🏹 Лучник", "Работает на комбо. Отличен для начала игры!\n\nКлючевые статы: Атака, Урон комбо, Урон крита, Скорость атаки\nСнаряжение: Комбо / Крит шанс", archer_keyboard),
    "class_mage": ("🔮 Маг", "Большой взрывной урон через скиллы.\n\nКлючевые статы: Атака, Урон скилла, Крит урон скилла\nСнаряжение: Крит скилла / Оглушение", mage_keyboard),
    "class_tamer": ("🐉 Укротитель", "Урон через питомцев.\n\nКлючевые статы: Атака, Урон питомца, Крит урон питомца\nСнаряжение: Комбо питомца / Крит питомца", tamer_keyboard),
    "class_martial_sage": ("🛡️ Боевой Мудрец", "Роль: Танк с регенерацией\nСнаряжение: Контрудар & Регенерация", None),
    "class_warbringer": ("⚔️ Вестник Войны", "Роль: Танковый DPS\nСнаряжение: Контрудар & Крит шанс", None),
    "class_sacred_hunter": ("🌿 Священный Охотник", "Роль: Гибридный Танк\nСнаряжение: Уклонение & Регенерация", None),
    "class_plume": ("🪶 Повелитель Перьев", "Роль: Стеклянная пушка DPS\nСнаряжение: Комбо & Крит шанс", None),
    "class_prophet": ("✨ Пророк", "Роль: Высокоурон + Танк\nСнаряжение: Крит скилла & Регенерация", None),
    "class_darklord": ("🌑 Тёмный Владыка", "Роль: Убийца одним ударом\nСнаряжение: Крит скилла & Оглушение", None),
    "class_beastmaster": ("🐾 Повелитель Зверей", "Роль: Стеклянная пушка DPS\nСнаряжение: Комбо питомца & Крит питомца", None),
    "class_supreme": ("💀 Верховный Дух", "Роль: DPS Танк\nСнаряжение: Комбо питомца & Регенерация", None)
}

# ============ ОБРАБОТЧИКИ ТЕЛЕГРАМ КОМАНД ============
async def check_user_access(update: Update) -> bool:
    if await is_approved(update.effective_user.id): return True
    await update.effective_message.reply_text(UI_TEXTS["no_access_msg"])
    return False

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id == ADMIN_ID: await save_approved_user(user_id, update.effective_user.username or "")
    if await is_approved(user_id): await send_welcome(context.bot, user_id)
    else: await update.message.reply_text(UI_TEXTS["no_access_msg"])

async def menu_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_user_access(update): return
    await update.message.reply_text(UI_TEXTS["menu_title"], reply_markup=main_menu_keyboard())

async def classes_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_user_access(update): return
    await update.message.reply_text(UI_TEXTS["classes_title"], reply_markup=classes_keyboard())

async def builds_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_user_access(update): return
    await update.message.reply_text(UI_TEXTS["builds_title"] + "Выберите нужный вам класс через /classes для деталей!")

async def feedback_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_user_access(update): return
    if not context.args:
        await update.message.reply_text("Напишите текст. Пример: `/feedback Привет`", parse_mode="Markdown")
        return
    if ADMIN_ID:
        try: await context.bot.send_message(ADMIN_ID, f"📩 Отзыв от @{update.effective_user.username}:\n\n{' '.join(context.args)}")
        except: pass
    await update.message.reply_text("Спасибо за отзыв! 🍄")

async def code_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if await is_approved(user_id): return
    if context.args and context.args[0] == ACCESS_CODE:
        await save_approved_user(user_id, update.effective_user.username or "")
        await update.message.reply_text("✅ Доступ успешно предоставлен.")
        await send_welcome(context.bot, user_id)
    else: await update.message.reply_text("❌ Неверный код.")

async def request_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if await is_approved(user_id): return
    await update.message.reply_text("⏳ Запрос отправлен админу.")
    if ADMIN_ID:
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("✅ Ок", callback_data=f"adm_ok_{user_id}"), InlineKeyboardButton("❌ Нет", callback_data=f"adm_no_{user_id}")]])
        try: await context.bot.send_message(ADMIN_ID, f"🔔 Запрос доступа от @{update.effective_user.username or user_id}:", reply_markup=kb)
        except: pass

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    user_id = update.effective_user.id

    if data.startswith("adm_ok_") or data.startswith("adm_no_"):
        if user_id != ADMIN_ID: return
        t_id = int(data.split("_")[2])
        if data.startswith("adm_ok_"):
            await save_approved_user(t_id)
            await query.edit_message_text(f"✅ Пользователь {t_id} одобрен.")
            try: await context.bot.send_message(t_id, "🎉 Доступ одобрен! Нажмите /start")
            except: pass
        else: await query.edit_message_text(f"❌ Отклонено.")
        return

    if not await is_approved(user_id): return

    if data == "menu_main": await query.edit_message_text(UI_TEXTS["menu_title"], reply_markup=main_menu_keyboard())
    elif data == "menu_classes": await query.edit_message_text(UI_TEXTS["classes_title"], reply_markup=classes_keyboard())
    elif data == "menu_beginner": await query.edit_message_text(UI_TEXTS["beginner_title"], parse_mode="Markdown", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(UI_TEXTS["back_btn"], callback_data="menu_main")]]))
    elif data == "menu_pals": await query.edit_message_text(UI_TEXTS["pals_title"], reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(UI_TEXTS["back_btn"], callback_data="menu_main")]]))
    elif data == "menu_events": await query.edit_message_text(UI_TEXTS["events_title"], reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(UI_TEXTS["back_btn"], callback_data="menu_main")]]))
    elif data == "menu_help": await query.edit_message_text(UI_TEXTS["help_title"], reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(UI_TEXTS["back_btn"], callback_data="menu_main")]]))
    elif data in CLASS_INFO:
        title, text, kb_f = CLASS_INFO[data]
        kb = kb_f() if kb_f else InlineKeyboardMarkup([[InlineKeyboardButton(UI_TEXTS["back_btn"], callback_data="menu_classes")]])
        await query.edit_message_text(f"*{title}*\n\n{text}", parse_mode="Markdown", reply_markup=kb)

async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not await is_approved(user_id): return
    is_limited, secs = check_rate_limit(user_id)
    if is_limited:
        await update.message.reply_text(UI_TEXTS["rate_limit_msg"].format(seconds=secs))
        return

    user_text = update.message.text or update.message.caption or ""
    image_data = None

    if update.message.photo:
        photo_file = await update.message.photo[-1].get_file()
        photo_bytes = await photo_file.download_as_bytearray()
        image_data = base64.b64encode(photo_bytes).decode("utf-8")
        if not user_text: user_text = "Проанализируй скриншот снаряжения."

    if not user_text: return
    status_msg = await update.message.reply_text("🍄 Думаю...")
    await add_conversation(user_id, "user", user_text)
    
    ai_response = await ask_ai(user_text, user_id, image_data)
    if ai_response:
        await add_conversation(user_id, "assistant", ai_response)
        await increment_stats(user_id)
        await status_msg.edit_text(ai_response)
    else: await status_msg.edit_text("❌ Ошибка ИИ. Попробуйте позже.")

# Фоновая инициализация
async def post_init(application):
    await init_db()
    await load_knowledge()
    
    # Запуск Дискорд-клиента параллельно
    if DISCORD_TOKEN and DISCORD_CHANNEL_ID:
        discord_client = DiscordBridge()
        application.bot_data["discord_client"] = discord_client
        asyncio.create_task(discord_client.start(DISCORD_TOKEN))
        logger.info("✓ Дискорд-мост успешно запущен в фоне!")
    else:
        logger.warning("Дискорд токены не найдены. Мост отключен.")

def main():
    if not TELEGRAM_TOKEN: return
    application = ApplicationBuilder().token(TELEGRAM_TOKEN).post_init(post_init).build()

    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("menu", menu_command))
    application.add_handler(CommandHandler("classes", classes_command))
    application.add_handler(CommandHandler("builds", builds_command))
    application.add_handler(CommandHandler("code", code_command))
    application.add_handler(CommandHandler("request", request_command))
    application.add_handler(CommandHandler("feedback", feedback_command))
    application.add_handler(CallbackQueryHandler(button_handler))
    application.add_handler(MessageHandler(filters.TEXT | filters.PHOTO, message_handler))

    application.run_polling()

if __name__ == '__main__':
    main()

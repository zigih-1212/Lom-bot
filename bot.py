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

# Список рабочих ИИ-моделей
MODELS = [
    "meta-llama/llama-3.2-11b-vision-instruct:free",
    "google/gemini-2.5-flash:free",
    "google/gemma-4-31b-it:free",
    "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free",
]

USER_COOLDOWNS = {}
RATE_LIMIT_PER_MINUTE = 10
RATE_LIMIT_TIMEOUT = 60

# Глобальные переменные для баз данных
KNOWLEDGE_TEXT = ""
DYNAMIC_EVENTS = "Свежих новостей об ивентах пока не поступало." 

SYSTEM_PROMPT = """You are Shroom Helper — an expert AI assistant for the mobile game Legend of Mushroom (LoM).

🚨 ИГРОВОЙ СЛОВАРЬ СЛЕНГА:
- "Летучий питомец", "птица", "летун", "птичка", "пет-птица" = Авиан (Avian) / Дух.
- "Стрелок", "лук", "хант" = Лучник (подклассы: Повелитель Перьев, Священный Охотник).
- "Пет", "питомец", "пал", "спутник" = Питомцы (Pals).
- "Танк", "меч", "щит" = Воин (подклассы: Боевой Мудрец, Вестник Войны).
- "Колдун", "прокаст" = Маг (подклассы: Пророк, Тёмный Владыка).
- "Навыки", "скиллы", "кнопки", "активки" = Активные навыки персонажа.

📸 ПРАВИЛА АНАЛИЗА СКРИНШОТОВ (КРИТИЧЕСКИ ВАЖНО):
Ты обязан изучить картинку и дать четкий совет по алгоритму:
- ШАГ 1: Перечисли, какие навыки/вещи/уровни (Lv.) ты отчетливо видишь на картинке в инвентаре и в экипировке. Описывай их по цветам иконки, если не уверен в названии (например: зеленый кулак Lv.5, фиолетовый череп Lv.19).
- ШАГ 2: Дай четкое руководство к действию для конкретного класса игрока. Что снять из верхнего ряда, а что поставить из нижнего инвентаря.

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
        "Вся информация предоставлена @Zigih90.\n\n"
        "📋 *Доступные команды:*\n"
        "/menu — главное меню\n"
        "/classes — обзор классов\n"
        "/builds — сборки по классам\n"
        "/feedback ТЕКСТ — отправить отзыв\n\n"
        "Выбери интересующий раздел меню или нажми кнопку анализа скриншотов! 👇"
    ),
    "no_access_msg": "🔒 Нет доступа.\nИспользуйте команду `/code ТВОЙ_КОД` или `/request` для запроса доступа.",
    "rate_limit_msg": "⏳ Подождите ещё {seconds} сек...",
    "menu_title": "🍄 Главное меню:",
    "classes_title": "⚔️ Выбери класс:",
    "builds_title": "🏹 *Быстрый гайд по сборкам:*\n\n",
    "pals_title": "🐾 *Питомцы (Pals):*\n\nЗадай вопрос для подробностей! 🍄",
    "events_title": "📅 Задай вопрос об актуальных ивентах и я расскажу что знаю! 🍄",
    "beginner_title": "💡 *Советы новичку...*",
    "help_title": "❓ *Помощь...*",
    "reload_success": "✅ База знаний перезагружена!",
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

async def load_knowledge():
    global KNOWLEDGE_TEXT, DYNAMIC_EVENTS
    if GUIDE_URL:
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                response = await client.get(GUIDE_URL)
                if response.status_code == 200:
                    soup = BeautifulSoup(response.text, 'html.parser')
                    for garbage in soup(["script", "style", "nav", "footer", "header"]): garbage.extract()
                    KNOWLEDGE_TEXT = soup.get_text(separator="\n")
        except Exception as e: logger.error(f"Load web knowledge error: {e}")
    if not KNOWLEDGE_TEXT:
        try:
            with open("knowledge.txt", "r", encoding="utf-8") as f: KNOWLEDGE_TEXT = f.read()
        except FileNotFoundError: KNOWLEDGE_TEXT = "База знаний пуста."

def check_rate_limit(user_id: int):
    now = datetime.now()
    if user_id not in USER_COOLDOWNS: USER_COOLDOWNS[user_id] = []
    USER_COOLDOWNS[user_id] = [t for t in USER_COOLDOWNS[user_id] if (now - t).total_seconds() < RATE_LIMIT_TIMEOUT]
    if len(USER_COOLDOWNS[user_id]) >= RATE_LIMIT_PER_MINUTE:
        return True, max(0, int(RATE_LIMIT_TIMEOUT - (now - USER_COOLDOWNS[user_id][0]).total_seconds()))
    USER_COOLDOWNS[user_id].append(now)
    return False, 0

async def send_welcome(bot, user_id: int):
    try: await bot.send_message(user_id, UI_TEXTS["welcome_msg"], parse_mode="Markdown", reply_markup=main_menu_keyboard())
    except Exception as e: logger.error(f"Welcome error: {e}")

# ============ ДИСКОРД МОСТ ============
class DiscordBridge(discord.Client):
    def __init__(self, *args, **kwargs):
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(intents=intents, *args, **kwargs)
    async def on_ready(self): logger.info(f"✓ Мост Discord запущен: {self.user}")
    async def on_message(self, message):
        global DYNAMIC_EVENTS
        if message.author == self.user: return
        if message.channel.id == DISCORD_CHANNEL_ID:
            DYNAMIC_EVENTS = message.content

# ============ ИИ-НЕЙРОСЕТЬ С АВТОФИЛЬТРОМ БРАКА ============
async def ask_ai(user_message: str, user_id: int, image_data: str = None) -> str:
    class_guides_context = ""
    try:
        text_blocks = []
        for key, val in CLASS_INFO.items(): text_blocks.append(f"Класс: {val[0]}\nГайд:\n{val[1]}")
        class_guides_context = "\n\n".join(text_blocks)
    except: pass

    async def try_model(model, is_vision_mode):
        try:
            if is_vision_mode and image_data:
                v_prompt = (
                    f"Контекст официальных билдов:\n{class_guides_context}\n\n"
                    f"ЗАДАНИЕ: {user_message}"
                )
                model_messages = [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": v_prompt},
                            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_data}"}}
                        ]
                    }
                ]
            else:
                db_context = f"=== ГАЙДЫ ===\n{class_guides_context}\n\n{KNOWLEDGE_TEXT}\n\n=== ИВЕНТЫ ===\n{DYNAMIC_EVENTS}"
                model_messages = [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": db_context},
                    {"role": "assistant", "content": "База знаний принята. Слушаю текстовый вопрос игрока."},
                ]
                history = await get_conversation_history(user_id, limit=4)
                if history: model_messages.extend(history)
                model_messages.append({"role": "user", "content": user_message})

            async with httpx.AsyncClient(timeout=45) as client:
                response = await client.post(
                    "https://openrouter.ai/api/v1/chat/completions",
                    headers={
                        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                        "Content-Type": "application/json",
                        "HTTP-Referer": "https://t.me/lom_helper_mushroom_bot",
                        "X-Title": "LoM Shroom Helper",
                    },
                    json={"model": model, "messages": model_messages, "max_tokens": 1000}
                )
            
            data = response.json()
            if "choices" in data and data["choices"]:
                content = data["choices"][0]["message"]["content"]
                if "user safety" in content.lower() or content.strip() == "":
                    logger.warning(f"⚠️ Заглушка безопасности от {model}. Пропускаем.")
                    return None
                logger.info(f"Успешный ответ от: {model}")
                return content
            return None
        except Exception as e:
            logger.error(f"Ошибка модели {model}: {e}")
            return None

    if image_data:
        vision_models = [
            "meta-llama/llama-3.2-11b-vision-instruct:free",
            "google/gemini-2.5-flash:free"
        ]
        for model in vision_models:
            res = await try_model(model, is_vision_mode=True)
            if res: return res

    text_models = ["google/gemma-4-31b-it:free", "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free"]
    for model in text_models:
        res = await try_model(model, is_vision_mode=False)
        if res: return res
    return None

# ============ КЛАВИАТУРЫ И СТРУКТУРА МЕНЮ ============
def main_menu_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📸 АНАЛИЗ ФОТО НАВЫКОВ 📸", callback_data="photo_flow_start")], # Наша главная супер-кнопка
        [InlineKeyboardButton(UI_TEXTS["classes_btn"], callback_data="menu_classes"),
         InlineKeyboardButton(UI_TEXTS["builds_btn"], callback_data="menu_builds")],
        [InlineKeyboardButton(UI_TEXTS["pals_btn"], callback_data="menu_pals"),
         InlineKeyboardButton(UI_TEXTS["events_btn"], callback_data="menu_events")],
        [InlineKeyboardButton(UI_TEXTS["beginner_btn"], callback_data="menu_beginner"),
         InlineKeyboardButton(UI_TEXTS["help_btn"], callback_data="menu_help")],
    ])

def photo_flow_classes_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🗡️ Воин", callback_data="p_main_warrior"),
         InlineKeyboardButton("🏹 Лучник", callback_data="p_main_archer")],
        [InlineKeyboardButton("🔮 Маг", callback_data="p_main_mage"),
         InlineKeyboardButton("🐉 Укротитель", callback_data="p_main_tamer")],
        [InlineKeyboardButton(UI_TEXTS["back_btn"], callback_data="menu_main")],
    ])

def p_warrior_keyboard():
    return InlineKeyboardMarkup([[InlineKeyboardButton("🛡️ Боевой Мудрец", callback_data="p_flow_martial_sage"), InlineKeyboardButton("⚔️ Вестник Войны", callback_data="p_flow_warbringer")], [InlineKeyboardButton(UI_TEXTS["back_btn"], callback_data="photo_flow_start")]])
def p_archer_keyboard():
    return InlineKeyboardMarkup([[InlineKeyboardButton("🌿 Священный Охотник", callback_data="p_flow_sacred_hunter"), InlineKeyboardButton("🪶 Повелитель Перьев", callback_data="p_flow_plume")], [InlineKeyboardButton(UI_TEXTS["back_btn"], callback_data="photo_flow_start")]])
def p_mage_keyboard():
    return InlineKeyboardMarkup([[InlineKeyboardButton("✨ Пророк", callback_data="p_flow_prophet"), InlineKeyboardButton("🌑 Тёмный Владыка", callback_data="p_flow_darklord")], [InlineKeyboardButton(UI_TEXTS["back_btn"], callback_data="photo_flow_start")]])
def p_tamer_keyboard():
    return InlineKeyboardMarkup([[InlineKeyboardButton("🐾 Повелитель Зверей", callback_data="p_flow_beastmaster"), InlineKeyboardButton("💀 Верховный Дух", callback_data="p_flow_supreme")], [InlineKeyboardButton(UI_TEXTS["back_btn"], callback_data="photo_flow_start")]]),
    ])

def warrior_keyboard():
    return InlineKeyboardMarkup([[InlineKeyboardButton("🛡️ Боевой Мудрец", callback_data="class_martial_sage"), InlineKeyboardButton("⚔️ Вестник Войны", callback_data="class_warbringer")], [InlineKeyboardButton(UI_TEXTS["back_btn"], callback_data="menu_classes")]])
def archer_keyboard():
    return InlineKeyboardMarkup([[InlineKeyboardButton("🌿 Священный Охотник", callback_data="class_sacred_hunter"), InlineKeyboardButton("🪶 Повелитель Перьев", callback_data="class_plume")], [InlineKeyboardButton(UI_TEXTS["back_btn"], callback_data="menu_classes")]])
def mage_keyboard():
    return InlineKeyboardMarkup([[InlineKeyboardButton("✨ Пророк", callback_data="class_prophet"), InlineKeyboardButton("🌑 Тёмный Владыка", callback_data="class_darklord")], [InlineKeyboardButton(UI_TEXTS["back_btn"], callback_data="menu_classes")]])
def tamer_keyboard():
    return InlineKeyboardMarkup([[InlineKeyboardButton("🐾 Повелитель Зверей", callback_data="class_beastmaster"), InlineKeyboardButton("💀 Верховный Дух", callback_data="class_supreme")], [InlineKeyboardButton(UI_TEXTS["back_btn"], callback_data="menu_classes")]])

CLASS_INFO = {
    "class_warrior": ("⚔️ Воин", "Основной класс ближнего боя. Высокая защита и контрудары.", warrior_keyboard),
    "class_archer": ("🏹 Лучник", "Высокий урон комбо и криты. Король ПвЕ контента.", archer_keyboard),
    "class_mage": ("🔮 Маг", "Упор на активные навыки и оглушение противника.", mage_keyboard),
    "class_tamer": ("🐉 Укротитель", "Сила зависит от прокачки и расстановки твоих питомцев.", tamer_keyboard),
    "class_martial_sage": ("🛡️ Боевой Мудрец", "Бессмертный танк с регенерацией. Снаряжение: Контрудар/Реген.", None),
    "class_warbringer": ("⚔️ Вестник Войны", "Танковый DPS. Силен против Лучников. Снаряжение: Контрудар/Крит.", None),
    "class_sacred_hunter": ("🌿 Священный Охотник", "Гибридный анти-маг. Иммунитет к контролю. Снаряжение: Уклонение/Комбо.", None),
    "class_plume": ("🪶 Повелитель Перьев", "Максимальный чистый урон от комбо. Стеклянная пушка. Снаряжение: Комбо/Крит.", None),
    "class_prophet": ("✨ Пророк", "Маг контроля и щитов. Уничтожает Танков. Снаряжение: Крит скилла/Реген.", None),
    "class_darklord": ("🌑 Тёмный Владыка", "Ваншот прокаст. Оглушает и стирает цель. Снаряжение: Крит скилла/Оглушение.", None),
    "class_beastmaster": ("🐾 Повелитель Зверей", "Атакующий призыватель. Снаряжение: Комбо питомца/Крит питомца.", None),
    "class_supreme": ("💀 Верховный Дух", "DPS-Танк через спутников. Снаряжение: Комбо питомца/Реген.", None)
}

# ============ ОБРАБОТЧИКИ КОМАНД И КНОПОК ============
async def check_user_access(update: Update) -> bool:
    if await is_approved(update.effective_user.id): return True
    await update.effective_message.reply_text(UI_TEXTS["no_access_msg"])
    return False

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id == ADMIN_ID: await save_approved_user(user_id, update.effective_user.username or "")
    if await is_approved(user_id): await send_welcome(context.bot, user_id)

async def menu_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_user_access(update): return
    await update.message.reply_text(UI_TEXTS["menu_title"], reply_markup=main_menu_keyboard())

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    user_id = update.effective_user.id

    if not await is_approved(user_id): return

    # ✅ Нажимая кнопку ФОТО, предлагаем выбрать класс
    if data == "photo_flow_start":
        context.user_data['p_awaiting'] = False  # Сбрасываем старые состояния
        await query.edit_message_text(
            "📸 *Режим анализа скриншотов*\n\nВыбери класс своего персонажа, для которого ИИ должен подобрать идеальные навыки со скриншота:", 
            parse_mode="Markdown", 
            reply_markup=photo_flow_classes_keyboard()
        )
        return

    # ✅ После выбора класса просим прислать скриншот
    if data.startswith("p_flow_"):
        chosen_class = data.split("_")[2]
        context.user_data['p_awaiting'] = True
        context.user_data['p_class'] = chosen_class
        
        class_ru = {"warrior": "Воина", "archer": "Лучника / Повелителя Перьев", "mage": "Мага", "tamer": "Укротителя"}
        await query.edit_message_text(
            f"📥 *Отлично! Я готов.*\n\nТеперь просто отправь мне **скриншот** своего инвентаря навыков.\n"
            f"Я проанализирую картинку специально под класс: *{class_ru.get(chosen_class)}*.\n\n"
            f"⚠️ Текст писать не нужно, просто пришли фото!", 
            parse_mode="Markdown"
        )
        return

    # Классическое меню навигации
    if data == "menu_main": await query.edit_message_text(UI_TEXTS["menu_title"], reply_markup=main_menu_keyboard())
    elif data == "menu_classes": await query.edit_message_text(UI_TEXTS["classes_title"], reply_markup=classes_keyboard())
    elif data == "menu_beginner": await query.edit_message_text(UI_TEXTS["beginner_title"], reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(UI_TEXTS["back_btn"], callback_data="menu_main")]]))
    elif data == "menu_pals": await query.edit_message_text(UI_TEXTS["pals_title"], reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(UI_TEXTS["back_btn"], callback_data="menu_main")]]))
    elif data == "menu_events": await query.edit_message_text(UI_TEXTS["events_title"], reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(UI_TEXTS["back_btn"], callback_data="menu_main")]]))
    elif data == "menu_help": await query.edit_message_text(UI_TEXTS["help_title"], reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(UI_TEXTS["back_btn"], callback_data="menu_main")]]))
    elif data in CLASS_INFO:
        title, text, kb_f = CLASS_INFO[data]
        kb = kb_f() if kb_f else InlineKeyboardMarkup([[InlineKeyboardButton(UI_TEXTS["back_btn"], callback_data="menu_classes")]])
        await query.edit_message_text(f"*{title}*\n\n{text}", parse_mode="Markdown", reply_markup=kb)

# ============ ОБРАБОТЧИК СООБЩЕНИЙ С СИСТЕМОЙ ФИЛЬТРАЦИИ ФОТО ============
async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not await is_approved(user_id): return
    
    is_limited, secs = check_rate_limit(user_id)
    if is_limited:
        await update.message.reply_text(UI_TEXTS["rate_limit_msg"].format(seconds=secs))
        return

    # 🚫 ЕСЛИ ПОЛЬЗОВАТЕЛЬ ПРИСЛАЛ ФОТО
    if update.message.photo:
        # Если он НЕ нажал кнопку "📸 Анализ фото" заранее
        if not context.user_data.get('p_awaiting'):
            await update.message.reply_text(
                "⚠️ *Я не могу принять фото напрямую!*\n\n"
                "Чтобы я корректно прочитал скриншот, нажми сначала кнопку **📸 АНАЛИЗ ФОТО НАВЫКОВ 📸** в Главном меню и выбери свой класс персонажа.",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Открыть меню /menu", callback_data="menu_main")]])
            )
            return

        # Если он прошёл по кнопкам, обрабатываем фото!
        status_msg = await update.message.reply_text("🍄 Изучаю твой скриншот навыков... Секунду...")
        
        photo_file = await update.message.photo[-1].get_file()
        photo_bytes = await photo_file.download_as_bytearray()
        image_data = base64.b64encode(photo_bytes).decode("utf-8")
        
        chosen_class = context.user_data.get('p_class', 'archer')
        class_ru = {"warrior": "Воин", "archer": "Лучник / Повелитель Перьев", "mage": "Маг", "tamer": "Укротитель"}
        
        # Сами создаем автоматический пуленепробиваемый промпт для ИИ
        ai_prompt = (
            f"Внимательно изучи прикрепленный скриншот меню навыков Legend of Mushroom.\n"
            f"Игрок играет за класс: {class_ru.get(chosen_class)}.\n"
            f"Выполни задание по нашему алгоритму анализа: найди все иконки в инвентаре, определи уровни и "
            f"дай подробные инструкции, что поставить в активные слоты для этого класса."
        )
        
        # Сбрасываем режим ожидания, чтобы в следующий раз он снова нажимал кнопку
        context.user_data['p_awaiting'] = False
        context.user_data['p_class'] = None
        
        ai_response = await ask_ai(ai_prompt, user_id, image_data)
        if ai_response:
            await increment_stats(user_id)
            await status_msg.edit_text(ai_response, parse_mode="Markdown")
        else:
            await status_msg.edit_text("❌ Все зрячие ИИ-модели перегружены или выдали ошибку. Попробуйте отправить скриншот чуть позже.")
        return

    # ТЕКСТОВЫЙ РЕЖИМ (Обычное общение)
    user_text = update.message.text or ""
    if not user_text: return
    
    status_msg = await update.message.reply_text("🍄 Думаю...")
    await add_conversation(user_id, "user", user_text)
    
    ai_response = await ask_ai(user_text, user_id)
    if ai_response:
        await add_conversation(user_id, "assistant", ai_response)
        await increment_stats(user_id)
        await status_msg.edit_text(ai_response)
    else: 
        await status_msg.edit_text("❌ Ошибка ИИ. Попробуйте позже.")

# Фоновая инициализация
async def post_init(application):
    await init_db()
    await load_knowledge()
    if DISCORD_TOKEN and DISCORD_CHANNEL_ID:
        discord_client = DiscordBridge()
        application.bot_data["discord_client"] = discord_client
        asyncio.create_task(discord_client.start(DISCORD_TOKEN))

def main():
    if not TELEGRAM_TOKEN: return
    application = ApplicationBuilder().token(TELEGRAM_TOKEN).post_init(post_init).build()
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("menu", menu_command))
    application.add_handler(CallbackQueryHandler(button_handler))
    application.add_handler(MessageHandler(filters.TEXT | filters.PHOTO, message_handler))
    application.run_polling()

if __name__ == '__main__':
    main()

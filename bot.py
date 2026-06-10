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

# Список моделей: Графические (Vision) на первом месте
MODELS = [
    "google/gemini-2.5-flash:free",
    "meta-llama/llama-3.2-11b-vision-instruct:free",
    "google/gemma-4-31b-it:free",
    "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free",
]

USER_COOLDOWNS = {}
RATE_LIMIT_PER_MINUTE = 10
RATE_LIMIT_TIMEOUT = 60

# Глобальные переменные для баз данных
KNOWLEDGE_TEXT = ""
DYNAMIC_EVENTS = "Свежих новостей об ивентах пока не поступало." 

# Системные правила для ИИ с жестким алгоритмом чтения скриншотов
SYSTEM_PROMPT = """You are Shroom Helper — an expert AI assistant for the mobile game Legend of Mushroom (LoM).

🚨 ИГРОВОЙ СЛОВАРЬ СЛЕНГА (Обязательно используй для понимания игрока):
- "Летучий питомец", "птица", "летун", "птичка", "пет-птица" = Авиан (Avian) / Дух.
- "Стрелок", "лук", "хант" = Лучник (подклассы: Повелитель Перьев, Священный Охотник).
- "Пет", "питомец", "пал", "спутник" = Питомцы (Pals).
- "Танк", "меч", "щит" = Воин (подклассы: Боевой Мудрец, Вестник Войны).
- "Колдун", "прокаст" = Маг (подклассы: Пророк, Тёмный Владыка).
- "Навыки", "скиллы", "кнопки", "активки" = Активные навыки персонажа.

ПРАВИЛА ОТВЕТОВ:
1. Тебе будет передан контекст: гайды из кнопок, база знаний и ивенты.
2. Никогда не упоминай сайты, ссылки. Если спросят источник, отвечай: "zigi provided this information".
3. Используй свои встроенные экспертные знания о мете игры, если в базе нет точного ответа. НЕ отвечай "Я не знаю" на вопросы по механикам и билдам.

📸 ПРАВИЛА АНАЛИЗА СКРИНШОТОВ (КРИТИЧЕСКИ ВАЖНО):
Если пользователь прислал скриншот инвентаря/навыков/питомцев и просит совет "что поставить из этого", НЕ ВЫДАВАЙ просто идеальный абстрактный гайд. Ты ОБЯЗАН действовать по этому алгоритму:
- ШАГ 1: Внимательно изучи картинку. Прямо напиши пользователю: "Я вижу у тебя экипированы такие-то навыки/вещи, а в инвентаре доступны вот такие: [перечисли названия того, что узнал на картинке]".
- ШАГ 2: Проанализируй этот список. Что из этого больше всего подходит его классу (Комбо/Крит для Лучника, Скилл/Оглушение для Мага и т.д.)?
- ШАГ 3: Дай четкое руководство к действию. Например: "Сними навык X и поставь вместо него навык Y из твоего инвентаря, потому что он дает нужный тебе бафф".

4. Язык общения: Всегда отвечай на русском языке. Используй списки, выделяй ключевые слова жирным шрифтом. 🍄
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

    if not KNOWLEDGE_TEXT:
        try:
            with open("knowledge.txt", "r", encoding="utf-8") as f:
                KNOWLEDGE_TEXT = f.read()
            logger.info("База знаний загружена из локального файла")
        except FileNotFoundError:
            KNOWLEDGE_TEXT = "База знаний пуста. Добавьте файлы или настройте GUIDE_URL."
    
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
    class_guides_context = ""
    try:
        text_blocks = []
        for key, val in CLASS_INFO.items():
            text_blocks.append(f"Класс: {val[0]}\nГайд и Сборка:\n{val[1]}")
        class_guides_context = "\n\n".join(text_blocks)
    except Exception as e:
        logger.error(f"Ошибка при сборке CLASS_INFO для ИИ: {e}")

    db_context = (
        f"=== ИГРОВАЯ БАЗА ЗНАНИЙ И ГАЙДЫ ===\n{class_guides_context}\n\n"
        f"{KNOWLEDGE_TEXT}\n\n"
        f"=== АКТУАЛЬНЫЕ ИВЕНТЫ ===\n{DYNAMIC_EVENTS}"
    )

    async def try_model(model, is_vision_mode):
        try:
            if is_vision_mode and image_data:
                # ✅ МОНОЛИТНЫЙ ПАКЕТ: Соединяем всё в один ход 'user' для Vision-моделей. 
                # Это полностью исключает любые скрытые ошибки 400 Bad Request на OpenRouter!
                full_prompt = (
                    f"{SYSTEM_PROMPT}\n\n"
                    f"{db_context}\n\n"
                    f"ЗАДАНИЕ ДЛЯ ИИ: Внимательно рассмотри прикрепленное изображение и ответь на вопрос игрока по алгоритму.\n"
                    f"Вопрос от игрока: {user_message}"
                )
                model_messages = [
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": full_prompt},
                            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_data}"}}
                        ]
                    }
                ]
            else:
                # Для обычного текста используем классическую многоходовку с историей
                model_messages = [{"role": "system", "content": SYSTEM_PROMPT}]
                model_messages.append({"role": "user", "content": db_context})
                model_messages.append({"role": "assistant", "content": "Принято, база данных в памяти. Слушаю вопрос игрока."})
                
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
                logger.info(f"🦾 Успешный ответ от нейросети: {model}")
                return data["choices"][0]["message"]["content"]
            
            if "error" in data:
                logger.error(f"⚠️ Ошибка OpenRouter для модели {model}: {data['error']}")
            return None
        except Exception as e:
            logger.error(f"❌ Ошибка вызова {model}: {e}")
            return None

    # Если есть картинка — бьем монолитным запросом строго по Vision-моделям
    if image_data:
        vision_models = [
            "google/gemini-2.5-flash:free", 
            "meta-llama/llama-3.2-11b-vision-instruct:free"
        ]
        for model in vision_models:
            logger.info(f"Отправляю монолитный Vision-пакет в {model}...")
            res = await try_model(model, is_vision_mode=True)
            if res: return res
        logger.warning("⚠️ Все зрячие модели выдали ошибку. Переключаюсь на текст...")

    # Текстовый бэкап
    text_models = [
        "google/gemma-4-31b-it:free",
        "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free"
    ]
    for model in text_models:
        res = await try_model(model, is_vision_mode=False)
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
    "class_warrior": (
        "⚔️ Воин (Warrior)", 
        """Основной класс ближнего боя. Базируется на высокой защите и контрударах. Прощает ошибки новичков благодаря выживаемости.

📈 *Ключевые статы:* Атака, Урон контрудара, Урон крита, Защита.
🛡️ *Снаряжение:* Контрудар / Крит шанс.

Выбери подкласс ниже, чтобы увидеть детальный гайд! 👇""", 
        warrior_keyboard
    ),
    "class_archer": (
        "🏹 Лучник (Archer)", 
        """Лучший класс для старта игры и зачистки ПвЕ-контента. Наносит урон за счет частых комбо-атак и высокой скорости.

📈 *Ключевые статы:* Атака, Урон комбо, Урон крита, Скорость атаки.
🏹 *Снаряжение:* Комбо / Крит шанс.

Выбери подкласс ниже, чтобы увидеть детальный гайд! 👇""", 
        archer_keyboard
    ),
    "class_mage": (
        "🔮 Маг (Mage)", 
        """Класс с упором на активные навыки (скиллы) и контроль противника через оглушение. Наносит огромный взрывной урон.

📈 *Ключевые статы:* Атака, Урон скилла, Крит урон скилла, Оглушение.
🔮 *Снаряжение:* Крит скилла / Оглушение.

Выбери подкласс ниже, чтобы увидеть детальный гайд! 👇""", 
        mage_keyboard
    ),
    "class_tamer": (
        "🐉 Укротитель (Tamer)", 
        """Уникальный класс, чья сила напрямую зависит от прокачки и расстановки твоих питомцев (Pals).

📈 *Ключевые статы:* Атака, Урон питомца, Крит урон питомца.
🐾 *Снаряжение:* Комбо питомца / Крит питомца.

Выбери подкласс ниже, чтобы увидеть детальный гайд! 👇""", 
        tamer_keyboard
    ),
    "class_martial_sage": (
        "🛡️ Боевой Мудрец", 
        """*Роль:* Бессмертный танк с бешеной регенерацией.

✅ *Плюсы:* Идеален для долгих боев, высочайшая выживаемость.
❌ *Минусы:* Низкий урон, бои в ПвП могут затягиваться.

📈 *Главные статы:* Здоровье, Защита, Контрудар, Регенерация.
🛡️ *Снаряжение:* Контрудар & Регенерация.
🗿 *Молитвенная статуя:* Защита x3, Здоровье x2.
🦅 *Авиан (Дух):* Синяя Птица или Кактус.""", 
        None
    ),
    "class_warbringer": (
        "⚔️ Вестник Войны", 
        """*Роль:* Танковый DPS (Атакующий боец).

✅ *Плюсы:* Наносит огромный урон, когда его бьют (через контрудары). Силен против Лучников.
❌ *Минусы:* Страдает против Магов с высоким контролем.

📈 *Главные статы:* Атака, Урон контрудара, Крит шанс.
🛡️ *Снаряжение:* Контрудар & Крит шанс.
🗿 *Молитвенная статуя:* Урон контрудара x4, Атака x1.
🦅 *Авиан (Дух):* Огненный Дракон.""", 
        None
    ),
    "class_sacred_hunter": (
        "🌿 Священный Охотник", 
        """*Роль:* Гибридный анти-маг (Контроль + Уклонение).

✅ *Плюсы:* Лучший выбор для ПвП против Магов благодаря иммунитету к контролю. Способен уклоняться от сильных ударов.
❌ *Минусы:* Урон чуть ниже, чем у Повелителя Перьев.

📈 *Главные статы:* Атака, Комбо, Уклонение, Регенерация.
🏹 *Снаряжение:* Уклонение & Регенерация (или Комбо).
🗿 *Молитвенная статуя:* Атака x3, Урон комбо x2.
🦅 *Авиан (Дух):* Охотничий Сокол.""", 
        None
    ),
    "class_plume": (
        "🪶 Повелитель Перьев", 
        """*Роль:* Стеклянная пушка (Максимальный чистый урон).

✅ *Плюсы:* Абсолютный король ПвЕ (Боссы, Подземелья). Невероятный урон от комбо-атак.
❌ *Минусы:* Очень хрупкий. Легко погибает, если враг прорвется сквозь щиты. Уязвим к Обезоруживанию.

📈 *Главные статы:* Атака, Урон комбо, Урон крита, Скорость атаки.
🏹 *Снаряжение:* Комбо & Крит шанс.
🗿 *Молитвенная статуя:* Урон комбо x5 или Атака x5.
🦅 *Авиан (Дух):* Золотой Орел.""", 
        None
    ),
    "class_prophet": (
        "✨ Пророк", 
        """*Роль:* Маг контроля и выживаемости.

✅ *Плюсы:* Очень высокая выживаемость для мага, быстро откатывает щиты. Уничтожает Танков и Вестников Войны.
❌ *Минусы:* Сложно пробить Священного Охотника.

📈 *Главные статы:* Атака, Урон скилла, Здоровье, Регенерация.
🔮 *Снаряжение:* Крит скилла & Регенерация.
🗿 *Молитвенная статуя:* Атака x5.
🦅 *Авиан (Дух):* Тыквенная Ведьма.""", 
        None
    ),
    "class_darklord": (
        "🌑 Тёмный Владыка", 
        """*Роль:* Маг-убийца (Ваншот лейта).

✅ *Плюсы:* Способен стереть врага за один прокаст навыков. Оглушает цель и не дает пошевелиться.
❌ *Минусы:* Если враг пережил первый прокаст — Тёмный Владыка проиграет.

📈 *Главные статы:* Атака, Урон скилла, Крит урон скилла, Оглушение.
🔮 *Снаряжение:* Крит скилла & Оглушение.
🗿 *Молитвенная статуя:* Атака x5 или Крит урон скилла x5.
🦅 *Авиан (Дух):* Тыквенная Ведьма.""", 
        None
    ),
    "class_beastmaster": (
        "🐾 Повелитель Зверей", 
        """*Роль:* Атакующий призыватель.

✅ *Плюсы:* Твои питомцы наносят критические удары огромной силы. Класс раскрывается на максимум, когда питомцы достигают 200+ уровня.
❌ *Минусы:* Зависим от редких Розовых питомцев. Без них урон средний.

📈 *Главные статы:* Атака, Урон питомца, Крит урон питомца.
🐾 *Снаряжение:* Комбо питомца & Крит питомца.
🗿 *Молитвенная статуя:* Урон питомца x5.
🦅 *Авиан (Дух):* Громовой Зверь.""", 
        None
    ),
    "class_supreme": (
        "💀 Верховный Дух", 
        """*Роль:* DPS-Танк через спутников.

✅ *Плюсы:* Отличный баланс между защитой и уроном. Сила зависит от правильной расстановки питомцев в слотах.
❌ *Минусы:* Требует тонкой настройки и понимания механик игры. Требователен к снаряжению.

📈 *Главные статы:* Атака, Здоровье, Урон питомца, Регенерация.
🐾 *Снаряжение:* Комбо питомца & Регенерация.
🗿 *Молитвенная статуя:* Здоровье x3, Урон питомца x2.
🦅 *Авиан (Дух):* Лесной Дух.""", 
        None
    )
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
    if context.args and " ".join(context.args).strip() == ACCESS_CODE.strip():
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
        if not user_text: user_text = "Проанализируй скриншот моих доступных навыков и скажи, что лучше всего экипировать для моего класса."

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

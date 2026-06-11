import os
import base64
import logging
import json
import httpx
import asyncio
import aiosqlite
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes

# Настройка логов
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.FileHandler('bot.log'), stream_handler := logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

# Переменные окружения
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

DB_PATH = "bot_data.db"

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
    "welcome_msg": (
        "🍄 *Привет! Я Shroom Helper — твой верный помощник по Legend of Mushroom!*\n\n"
        "Я умею анализировать скриншоты твоих навыков, подбирать лучшие сборки под любой подкласс и отвечать на любые текстовые вопросы по игре.\n\n"
        "Используй меню кнопок ниже или просто напиши мне свой вопрос! 👇"
    ),
    "menu_title": "🍄 *Главное меню помощника:*",
}

# ============ DATABASE (ASYNC) ============
async def init_db():
    async with aiosqlite.connect(DB_PATH, timeout=10) as conn:
        cursor = await conn.cursor()
        await cursor.execute('''CREATE TABLE IF NOT EXISTS approved_users (user_id INTEGER PRIMARY KEY, username TEXT, approved_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
        await cursor.execute('''CREATE TABLE IF NOT EXISTS stats (user_id INTEGER PRIMARY KEY, question_count INTEGER DEFAULT 0, last_question_at TIMESTAMP)''')
        await cursor.execute('''CREATE TABLE IF NOT EXISTS conversations (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, role TEXT, content TEXT, timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
        await conn.commit()

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

# ============ ПРЯМОЙ ОФИЦИАЛЬНЫЙ GOOGLE GEMINI API ============
async def ask_ai(user_message: str, user_id: int, image_data: str = None) -> str:
    if not GEMINI_API_KEY: return "⚙️ Ошибка Конфигурации: Не задан GEMINI_API_KEY в Railway."

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
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📸 АНАЛИЗ ФОТО НАВЫКОВ 📸", callback_data="photo_flow_start")],
        [InlineKeyboardButton("⚔️ Классы", callback_data="menu_classes"), InlineKeyboardButton("🏹 Билды", callback_data="menu_builds")],
        [InlineKeyboardButton("🐾 Питомцы", callback_data="menu_pals"), InlineKeyboardButton("📅 Ивенты", callback_data="menu_events")],
        [InlineKeyboardButton("💡 Советы новичку", callback_data="menu_beginner"), InlineKeyboardButton("❓ Помощь", callback_data="menu_help")]
    ])

def photo_flow_classes_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🗡️ Воин", callback_data="p_main_warrior"), InlineKeyboardButton("🏹 Лучник", callback_data="p_main_archer")],
        [InlineKeyboardButton("🔮 Маг", callback_data="p_main_mage"), InlineKeyboardButton("🐉 Укротитель", callback_data="p_main_tamer")],
        [InlineKeyboardButton("🔙 Назад в меню", callback_data="menu_main")]
    ])

def p_warrior_keyboard(): return InlineKeyboardMarkup([[InlineKeyboardButton("🛡️ Боевой Мудрец", callback_data="p_flow_martial_sage"), InlineKeyboardButton("⚔️ Вестник Войны", callback_data="p_flow_warbringer")], [InlineKeyboardButton("🔙 Назад", callback_data="photo_flow_start")]])
def p_archer_keyboard(): return InlineKeyboardMarkup([[InlineKeyboardButton("🌿 Священный Охотник", callback_data="p_flow_sacred_hunter"), InlineKeyboardButton("🪶 Повелитель Перьев", callback_data="p_flow_plume")], [InlineKeyboardButton("🔙 Назад", callback_data="photo_flow_start")]])
def p_mage_keyboard(): return InlineKeyboardMarkup([[InlineKeyboardButton("✨ Пророк", callback_data="p_flow_prophet"), InlineKeyboardButton("🌑 Тёмный Владыка", callback_data="p_flow_darklord")], [InlineKeyboardButton("🔙 Назад", callback_data="photo_flow_start")]])
def p_tamer_keyboard(): return InlineKeyboardMarkup([[InlineKeyboardButton("🐾 Повелитель Зверей", callback_data="p_flow_beastmaster"), InlineKeyboardButton("💀 Верховный Дух", callback_data="p_flow_supreme")], [InlineKeyboardButton("🔙 Назад", callback_data="photo_flow_start")]])

def get_subclass_keyboard(main_class):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🛡️ 1 Специализация", callback_data=f"subinfo_{main_class}_1"), InlineKeyboardButton("⚔️ 2 Специализация", callback_data=f"subinfo_{main_class}_2")],
        [InlineKeyboardButton("🔙 Назад к классам", callback_data="menu_classes")]
    ])

CLASS_INFO = {
    "warrior_1": ("🛡️ Боевой Мудрец", "Полноценный бессмертный танк. Высочайшая выживаемость, упор в защиту и постепенное изматывание врага.\n*Рекомендуемое снаряжение:* Контрудар / Регенерация."),
    "warrior_2": ("⚔️ Вестник Войны", "Танковый DPS-боец ближнего боя. Очень силен против Лучников за счет контратак.\n*Рекомендуемое снаряжение:* Контрудар / Крит."),
    "archer_1": ("🌿 Священный Охотник", "Гибридный анти-маг. Обладает встроенным иммунитетом к эффектам контроля противника.\n*Рекомендуемое снаряжение:* Уклонение / Комбо."),
    "archer_2": ("🪶 Повелитель Перьев", "Классический стрелок (Арба). Максимальный и чистый урон от комбо-атак. Стеклянная пушка.\n*Рекомендуемое снаряжение:* Комбо / Крит."),
    "mage_1": ("✨ Пророк", "Маг глубокого контроля, замедления и прочных щитов. Идеально ломает плотных Танков.\n*Рекомендуемое снаряжение:* Крит навыка / Регенерация."),
    "mage_2": ("🌑 Тёмный Владыка", "Маг с колоссальным мгновенным взрывным уроном (ваншот-прокаст). Оглушает и стирает цель.\n*Рекомендуемое снаряжение:* Крит навыка / Оглушение."),
    "tamer_1": ("🐾 Повелитель Зверей", "Атакующий призыватель, чья боевая мощь напрямую завязана на усилении своих спутников.\n*Рекомендуемое снаряжение:* Комбо питомца / Крит питомца."),
    "tamer_2": ("💀 Верховный Дух", "Защитный DPS-танк, распределяющий входящий по себе урон через призванных существ.\n*Рекомендуемое снаряжение:* Комбо питомца / Регенерация.")
}

# ============ ХЭНДЛЕРЫ КОМАНД ============
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(UI_TEXTS["welcome_msg"], parse_mode="Markdown", reply_markup=main_menu_keyboard())

async def menu_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(UI_TEXTS["menu_title"], parse_mode="Markdown", reply_markup=main_menu_keyboard())

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    user_id = update.effective_user.id
    
    if data == "menu_main":
        await query.edit_message_text(UI_TEXTS["menu_title"], parse_mode="Markdown", reply_markup=main_menu_keyboard())
        return
        
    if data == "photo_flow_start":
        await query.edit_message_text("📸 *Режим анализа скриншотов*\n\nВыбери основной класс твоего персонажа:", parse_mode="Markdown", reply_markup=photo_flow_classes_keyboard())
        return
        
    if data.startswith("p_main_"):
        kb_map = {"warrior": p_warrior_keyboard, "archer": p_archer_keyboard, "mage": p_mage_keyboard, "tamer": p_tamer_keyboard}
        await query.edit_message_text("🔮 Теперь выбери точный подкласс персонажа:", reply_markup=kb_map[data.split("_")[2]]())
        return
        
    if data.startswith("p_flow_"):
        context.user_data['p_awaiting'] = True
        context.user_data['p_class'] = data.split("_")[2]
        await query.edit_message_text("📥 *Отлично! Я готов.*\n\nТеперь просто отправь мне **скриншот** своего инвентаря активных навыков.\n\nЯ проведу анализ картинки специально под твой выбранный подкласс! Жду фото.", parse_mode="Markdown")
        return

    if data == "menu_classes":
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🗡️ Воин", callback_data="classinfo_warrior"), InlineKeyboardButton("🏹 Лучник", callback_data="classinfo_archer")],
            [InlineKeyboardButton("🔮 Маг", callback_data="classinfo_mage"), InlineKeyboardButton("🐉 Укротитель", callback_data="classinfo_tamer")],
            [InlineKeyboardButton("🔙 Назад в меню", callback_data="menu_main")]
        ])
        await query.edit_message_text("⚔️ *Выбери интересующий класс для изучения специализаций:*", parse_mode="Markdown", reply_markup=kb)
        return

    if data.startswith("classinfo_"):
        cls = data.split("_")[1]
        class_names = {"warrior": "Воин", "archer": "Лучник", "mage": "Маг", "tamer": "Укротитель"}
        await query.edit_message_text(f"🔮 Выбери ветку развития для класса *{class_names[cls]}*:", parse_mode="Markdown", reply_markup=get_subclass_keyboard(cls))
        return

    if data.startswith("subinfo_"):
        _, cls, num = data.split("_")
        key = f"{cls}_{num}"
        if key in CLASS_INFO:
            title, text = CLASS_INFO[key]
            kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 К подклассам", callback_data=f"classinfo_{cls}")], [InlineKeyboardButton("🔙 Главное меню", callback_data="menu_main")]])
            await query.edit_message_text(f"*{title}*\n\n{text}", parse_mode="Markdown", reply_markup=kb)
        return

    # Интерактивные ИИ-кнопки меню
    ai_prompts = {
        "menu_builds": "Расскажи про актуальные мета-билды навыков и снаряжения для разных классов в мобильной игре Legend of Mushroom.",
        "menu_pals": "Дай развернутый гайд по питомцам (Pals / Спутники) в игре Legend of Mushroom. Какие самые лучшие и для каких задач?",
        "menu_events": "Какие основные регулярные ивенты проходят в игре Legend of Mushroom и как рядовому игроку эффективно копить ресурсы и готовиться к ним?",
        "menu_beginner": "Дай топ-5 важнейших советов, секретов и критических ошибок для новичков в игре Legend of Mushroom."
    }

    if data in ai_prompts:
        await query.edit_message_text("🍄 *Запрашиваю подробную аналитическую информацию у официального ИИ от Google... Минутку...*", parse_mode="Markdown")
        resp = await ask_ai(ai_prompts[data], user_id)
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад в меню", callback_data="menu_main")]])
        await query.edit_message_text(resp, parse_mode="Markdown", reply_markup=kb)
        return

    if data == "menu_help":
        help_text = (
            "❓ *Как правильно использовать Shroom Helper:*\n\n"
            "1. **Анализ скриншотов**: Нажми верхнюю кнопку меню, выбери класс и подкласс, затем пришли картинку инвентаря навыков. ИИ составит идеальную сборку.\n\n"
            "2. **Свободный текстовый чат**: Ты можешь отправить боту любое текстовое сообщение с вопросом по игре (например: *'какие статы ролить на арбалетчика?'*), и ИИ развернуто ответит.\n\n"
            "3. **Интерактивные разделы**: Кнопки Билды, Питомцы и Ивенты автоматически вызывают ИИ для генерации актуальных игровых гайдов."
        )
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад в меню", callback_data="menu_main")]])
        await query.edit_message_text(help_text, parse_mode="Markdown", reply_markup=kb)
        return

# ============ ОБРАБОТЧИК СООБЩЕНИЙ ============
async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.photo and context.user_data.get('p_awaiting'):
        status_msg = await update.message.reply_text("🍄 Официальный ИИ от Google изучает скриншот...")
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

async def post_init(application):
    await init_db()
    logger.info("✓ База данных успешно инициализирована внутри рабочего цикла!")

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

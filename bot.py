import os
import base64
import logging
import json
import httpx
import sqlite3
import asyncio
import threading
from datetime import datetime, timedelta
from pathlib import Path
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes

# ============ LOGGING SETUP ============
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('bot.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# ============ CONFIG ============
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
]

USER_COOLDOWNS = {}
RATE_LIMIT_PER_MINUTE = 10
RATE_LIMIT_TIMEOUT = 60

SYSTEM_PROMPT = """You are Shroom Helper for Legend of Mushroom (LoM).
Answer questions based on knowledge base. Be friendly and helpful.
If user writes in Russian - answer in Russian completely, translate all terms.
If English - answer in English."""

# ============ DATABASE SETUP ============
def init_db():
    """Initialize SQLite database"""
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
                            (id INTEGER PRIMARY KEY AUTOINCREMENT, total_questions INTEGER DEFAULT 0, questions_today INTEGER DEFAULT 0, last_reset_date TEXT, updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
            
            conn.commit()
            conn.close()
            logger.info("Database initialized successfully")
        except Exception as e:
            logger.error(f"Failed to initialize database: {e}")

# ============ DATABASE OPERATIONS ============
def load_approved_users() -> set:
    """Load approved users from database"""
    with DB_LOCK:
        try:
            conn = sqlite3.connect(DB_PATH, timeout=10)
            cursor = conn.cursor()
            cursor.execute('SELECT user_id FROM approved_users')
            users = set(row[0] for row in cursor.fetchall())
            conn.close()
            return users
        except Exception as e:
            logger.error(f"Failed to load approved users: {e}")
            return set()

def save_approved_user(user_id: int, username: str = ""):
    """Add user to approved list"""
    with DB_LOCK:
        try:
            conn = sqlite3.connect(DB_PATH, timeout=10)
            cursor = conn.cursor()
            cursor.execute('INSERT OR IGNORE INTO approved_users (user_id, username) VALUES (?, ?)',
                         (user_id, username))
            conn.commit()
            conn.close()
        except Exception as e:
            logger.error(f"Failed to save approved user {user_id}: {e}")

def revoke_approved_user(user_id: int):
    """Remove user from approved list"""
    with DB_LOCK:
        try:
            conn = sqlite3.connect(DB_PATH, timeout=10)
            cursor = conn.cursor()
            cursor.execute('DELETE FROM approved_users WHERE user_id = ?', (user_id,))
            conn.commit()
            conn.close()
        except Exception as e:
            logger.error(f"Failed to revoke user {user_id}: {e}")

def is_approved(user_id: int) -> bool:
    """Check if user is approved"""
    if user_id == ADMIN_ID:
        return True
    return user_id in load_approved_users()

def add_conversation(user_id: int, role: str, content: str):
    """Save conversation to database"""
    with DB_LOCK:
        try:
            conn = sqlite3.connect(DB_PATH, timeout=10)
            cursor = conn.cursor()
            cursor.execute('INSERT INTO conversations (user_id, role, content) VALUES (?, ?, ?)',
                         (user_id, role, content))
            conn.commit()
            conn.close()
        except Exception as e:
            logger.error(f"Failed to save conversation for user {user_id}: {e}")

def get_conversation_history(user_id: int, limit: int = 6) -> list:
    """Get last N conversations for user"""
    with DB_LOCK:
        try:
            conn = sqlite3.connect(DB_PATH, timeout=10)
            cursor = conn.cursor()
            cursor.execute('SELECT role, content FROM conversations WHERE user_id = ? ORDER BY timestamp DESC LIMIT ?',
                         (user_id, limit))
            history = [{"role": row[0], "content": row[1]} for row in reversed(cursor.fetchall())]
            conn.close()
            return history
        except Exception as e:
            logger.error(f"Failed to get conversation history for user {user_id}: {e}")
            return []

def increment_stats(user_id: int):
    """Increment user question count"""
    with DB_LOCK:
        try:
            conn = sqlite3.connect(DB_PATH, timeout=10)
            cursor = conn.cursor()
            cursor.execute('INSERT OR IGNORE INTO stats (user_id, question_count, last_question_at) VALUES (?, 1, CURRENT_TIMESTAMP)')
            cursor.execute('UPDATE stats SET question_count = question_count + 1, last_question_at = CURRENT_TIMESTAMP WHERE user_id = ?',
                         (user_id,))
            cursor.execute('UPDATE global_stats SET total_questions = total_questions + 1, questions_today = questions_today + 1 WHERE id = 1')
            conn.commit()
            conn.close()
        except Exception as e:
            logger.error(f"Failed to increment stats: {e}")

def get_stats() -> dict:
    """Get global bot statistics"""
    with DB_LOCK:
        try:
            conn = sqlite3.connect(DB_PATH, timeout=10)
            cursor = conn.cursor()
            cursor.execute('SELECT total_questions, questions_today FROM global_stats LIMIT 1')
            row = cursor.fetchone()
            total_q, today_q = row if row else (0, 0)
            approved_count = len(load_approved_users())
            conn.close()
            return {"total_questions": total_q, "questions_today": today_q, "total_users": approved_count}
        except Exception as e:
            logger.error(f"Failed to get stats: {e}")
            return {"total_questions": 0, "questions_today": 0, "total_users": 0}

# ============ STORAGE ============
def load_knowledge() -> str:
    """Load knowledge base from file"""
    try:
        with open("knowledge.txt", "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        logger.warning("knowledge.txt not found")
        return ""
    except Exception as e:
        logger.error(f"Failed to load knowledge.txt: {e}")
        return ""

# ============ RATE LIMITING ============
def is_rate_limited(user_id: int) -> bool:
    """Check if user exceeded rate limit"""
    now = datetime.now()
    if user_id not in USER_COOLDOWNS:
        USER_COOLDOWNS[user_id] = []
    
    USER_COOLDOWNS[user_id] = [t for t in USER_COOLDOWNS[user_id]
                               if (now - t).total_seconds() < RATE_LIMIT_TIMEOUT]
    
    if len(USER_COOLDOWNS[user_id]) >= RATE_LIMIT_PER_MINUTE:
        return True
    
    USER_COOLDOWNS[user_id].append(now)
    return False

# ============ AI ============
async def ask_ai(user_message: str, knowledge: str, user_id: int, image_data: str = None) -> str:
    """Query AI with fallback"""
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    
    history = get_conversation_history(user_id, limit=4)
    if history:
        messages.extend(history)
    
    prompt = f"Knowledge base:\n{knowledge}\n\nQuestion: {user_message}"
    
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
                    json={"model": model, "messages": messages, "max_tokens": 500}
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
    
    tasks = [try_model(model) for model in MODELS]
    try:
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED, timeout=35)
        for task in done:
            result = await task
            if result:
                for t in pending:
                    t.cancel()
                return result
        for t in pending:
            t.cancel()
        return None
    except Exception as e:
        logger.error(f"Error waiting for AI response: {e}")
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

# ============ COMMANDS ============
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start command"""
    try:
        user_id = update.effective_user.id
        user_name = update.effective_user.full_name
        
        if is_approved(user_id):
            await update.message.reply_text(
                "🍄 Привет! Я Shroom Helper\n\n"
                "Задай вопрос, отправь скриншот или открой меню 👇\n\n"
                "/menu - главное меню\n/classes - обзор классов\n/builds - быстрый гайд",
                reply_markup=main_menu_keyboard()
            )
        else:
            await update.message.reply_text(
                "🍄 Введи код:\n/code ТВОЙ_КОД\n\n"
                "Или запроси доступ:\n/request"
            )
    except Exception as e:
        logger.error(f"Start error: {e}")

async def menu_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Menu command"""
    try:
        if not is_approved(update.effective_user.id):
            await update.message.reply_text("🔒 Нет доступа")
            return
        await update.message.reply_text("🍄 Главное меню:", reply_markup=main_menu_keyboard())
    except Exception as e:
        logger.error(f"Menu error: {e}")

async def classes_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Classes command"""
    try:
        if not is_approved(update.effective_user.id):
            await update.message.reply_text("🔒 Нет доступа")
            return
        await update.message.reply_text("⚔️ Выбери класс:", reply_markup=classes_keyboard())
    except Exception as e:
        logger.error(f"Classes error: {e}")

async def builds_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Builds command"""
    try:
        if not is_approved(update.effective_user.id):
            await update.message.reply_text("🔒 Нет доступа")
            return
        text = "🏹 Быстрый гайд по сборкам:\n\n🪶 Плиум - Комбо & Крит\n⚔️ Вестник - Контрудар & Крит\n✨ Пророк - Скилл & Крит\n\nЗадай вопрос для подробностей!"
        await update.message.reply_text(text)
    except Exception as e:
        logger.error(f"Builds error: {e}")

async def code_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Code verification"""
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
                    await context.bot.send_message(ADMIN_ID, f"✅ Новый пользователь:\n👤 {user_name}\n🆔 {user_id}")
                except Exception as e:
                    logger.warning(f"Failed to notify admin: {e}")
        else:
            await update.message.reply_text("❌ Неверный код")
            
            if ADMIN_ID:
                try:
                    keyboard = [[
                        InlineKeyboardButton("✅ Разрешить", callback_data=f"approve_{user_id}"),
                        InlineKeyboardButton("❌ Отклонить", callback_data=f"deny_{user_id}")
                    ]]
                    await context.bot.send_message(ADMIN_ID,
                        f"⚠️ Неверный код:\n👤 {user_name}\n🆔 {user_id}",
                        reply_markup=InlineKeyboardMarkup(keyboard))
                except Exception as e:
                    logger.warning(f"Failed to send request to admin: {e}")
    except Exception as e:
        logger.error(f"Code error: {e}")

async def request_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Request access"""
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
        await update.message.reply_text("✅ Запрос отправлен!")
    except Exception as e:
        logger.error(f"Request error: {e}")

async def feedback_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Feedback command"""
    try:
        if not is_approved(update.effective_user.id):
            await update.message.reply_text("🔒 Нет доступа")
            return
        
        if not context.args:
            await update.message.reply_text("Напиши отзыв: /feedback ТВОЙ_ОТЗЫВ")
            return
        
        user_name = update.effective_user.full_name
        user_id = update.effective_user.id
        feedback_text = " ".join(context.args)
        
        if ADMIN_ID:
            try:
                await context.bot.send_message(ADMIN_ID,
                    f"💬 Отзыв от {user_name} ({user_id}):\n\n{feedback_text}")
                await update.message.reply_text("✅ Отзыв отправлен! Спасибо 🍄")
            except Exception as e:
                logger.error(f"Failed to send feedback: {e}")
    except Exception as e:
        logger.error(f"Feedback error: {e}")

async def approve_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Approve user (admin)"""
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
        except Exception as e:
            logger.warning(f"Failed to notify user: {e}")
    except Exception as e:
        logger.error(f"Approve error: {e}")

async def revoke_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Revoke user (admin)"""
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
    """Users list (admin)"""
    try:
        if update.effective_user.id != ADMIN_ID:
            return
        
        approved = load_approved_users()
        if approved:
            user_list = "\n".join(str(u) for u in sorted(approved))
            await update.message.reply_text(f"👥 Пользователей: {len(approved)}\n\n{user_list}")
        else:
            await update.message.reply_text("Нет пользователей")
    except Exception as e:
        logger.error(f"Users error: {e}")

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Stats (admin)"""
    try:
        if update.effective_user.id != ADMIN_ID:
            return
        
        stats = get_stats()
        await update.message.reply_text(
            f"📊 Статистика:\n\n👥 Пользователей: {stats['total_users']}\n"
            f"💬 Всего вопросов: {stats['total_questions']}\n"
            f"📅 Сегодня: {stats['questions_today']}")
    except Exception as e:
        logger.error(f"Stats error: {e}")

async def broadcast_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Broadcast (admin)"""
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
            except Exception as e:
                logger.warning(f"Failed to send to {user_id}: {e}")
        
        await update.message.reply_text(f"✅ Отправлено {sent}/{len(approved)}!")
    except Exception as e:
        logger.error(f"Broadcast error: {e}")

# ============ CALLBACKS ============
async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle button callbacks"""
    try:
        query = update.callback_query
        await query.answer()
        data = query.data
        
        if data == "menu_main":
            await query.edit_message_text("🍄 Главное меню:", reply_markup=main_menu_keyboard())
        elif data == "menu_classes":
            await query.edit_message_text("⚔️ Выбери класс:", reply_markup=classes_keyboard())
        elif data == "menu_builds":
            await query.edit_message_text("🏹 Быстрый гайд:\n🪶 Плиум - лучший\n⚔️ Вестник - танк\n✨ Пророк - маг")
        elif data == "menu_pals":
            await query.edit_message_text("🐾 Питомцы дают бонусы и урон")
        elif data == "menu_events":
            await query.edit_message_text("📅 Задай вопрос об ивентах!")
        elif data == "menu_beginner":
            await query.edit_message_text("💡 Начни с Лучника, выбери Плиум!")
        elif data == "menu_help":
            await query.edit_message_text("❓ /menu - меню\n/classes - классы\n/code - код доступа")
        elif data.startswith("approve_"):
            target_id = int(data.split("_")[1])
            save_approved_user(target_id)
            await query.edit_message_text(f"✅ {target_id} одобрен!")
        elif data.startswith("deny_"):
            target_id = int(data.split("_")[1])
            await query.edit_message_text(f"❌ {target_id} отклонён")
    except Exception as e:
        logger.error(f"Button error: {e}")

# ============ MESSAGE HANDLER ============
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle messages"""
    try:
        if not update.message or not update.message.text:
            return
        
        user_id = update.effective_user.id
        
        if is_rate_limited(user_id):
            await update.message.reply_text("⏳ Подождите немного...")
            return
        
        if not is_approved(user_id):
            await update.message.reply_text("🔒 /code ТВОЙ_КОД")
            return
        
        text = update.message.text.strip()
        if not text or text.startswith('/'):
            return
        
        await update.message.chat.send_action("typing")
        
        knowledge = load_knowledge()
        reply = await ask_ai(text, knowledge, user_id)
        
        if reply:
            add_conversation(user_id, "user", text)
            add_conversation(user_id, "assistant", reply)
            increment_stats(user_id)
            await update.message.reply_text(reply)
        else:
            await update.message.reply_text("⚠️ Все модели перегружены. Попробуй позже!")
    except Exception as e:
        logger.error(f"Message error: {e}")
        try:
            await update.message.reply_text("❌ Ошибка обработки")
        except:
            pass

# ============ MAIN ============
if __name__ == "__main__":
    init_db()
    
    if not TELEGRAM_TOKEN:
        logger.error("❌ TELEGRAM_TOKEN not set!")
        exit(1)
    
    logger.info("🍄 Starting bot...")
    
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    
    # Commands
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
    
    # Handlers
    app.add_handler(CallbackQueryHandler(button_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    logger.info("🍄 Bot started!")
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)

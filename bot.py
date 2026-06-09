import os
import base64
import logging
import sqlite3
import asyncio
import threading
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes
import httpx

# ============ LOGGING ============
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
]

USER_COOLDOWNS = {}
RATE_LIMIT_PER_MINUTE = 10

SYSTEM_PROMPT = """You are Shroom Helper for Legend of Mushroom.
Answer questions based on the knowledge base provided.
If user writes in Russian - answer in Russian. If English - answer in English.
Be friendly and helpful. Use 🍄 emoji sometimes."""

# ============ DATABASE ============
def init_db():
    """Initialize database"""
    with DB_LOCK:
        try:
            conn = sqlite3.connect(DB_PATH, timeout=10)
            cursor = conn.cursor()
            
            cursor.execute('''CREATE TABLE IF NOT EXISTS approved_users 
                            (user_id INTEGER PRIMARY KEY, username TEXT, approved_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
            cursor.execute('''CREATE TABLE IF NOT EXISTS conversations 
                            (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, role TEXT, content TEXT, timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
            cursor.execute('''CREATE TABLE IF NOT EXISTS stats 
                            (user_id INTEGER PRIMARY KEY, question_count INTEGER DEFAULT 0, last_question_at TIMESTAMP)''')
            
            conn.commit()
            conn.close()
            logger.info("✅ Database initialized")
        except Exception as e:
            logger.error(f"❌ Database error: {e}")

def load_approved_users() -> set:
    """Load approved users"""
    try:
        with DB_LOCK:
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
    """Save approved user"""
    try:
        with DB_LOCK:
            conn = sqlite3.connect(DB_PATH, timeout=10)
            cursor = conn.cursor()
            cursor.execute('INSERT OR IGNORE INTO approved_users (user_id, username) VALUES (?, ?)',
                         (user_id, username))
            conn.commit()
            conn.close()
    except Exception as e:
        logger.error(f"Save user error: {e}")

def is_approved(user_id: int) -> bool:
    """Check if user approved"""
    if user_id == ADMIN_ID:
        return True
    return user_id in load_approved_users()

# ============ MENUS ============
def main_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⚔️ Классы", callback_data="menu_classes"),
         InlineKeyboardButton("🏹 Билды", callback_data="menu_builds")],
        [InlineKeyboardButton("🐾 Питомцы", callback_data="menu_pals"),
         InlineKeyboardButton("📅 Ивенты", callback_data="menu_events")],
        [InlineKeyboardButton("💡 Советы", callback_data="menu_beginner"),
         InlineKeyboardButton("❓ Помощь", callback_data="menu_help")],
    ])

def classes_menu():
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
        if is_approved(user_id):
            await update.message.reply_text(
                "🍄 Привет! Я Shroom Helper\n\n"
                "Задай вопрос или выбери меню 👇",
                reply_markup=main_menu()
            )
        else:
            await update.message.reply_text(
                "🍄 Введи код:\n/code ТВОЙ_КОД\n\n"
                "Или запроси доступ:\n/request"
            )
    except Exception as e:
        logger.error(f"Start error: {e}")

async def code_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Code verification"""
    try:
        user_id = update.effective_user.id
        if is_approved(user_id):
            await update.message.reply_text("✅ У тебя уже есть доступ!")
            return
        
        if not context.args or context.args[0] != ACCESS_CODE:
            await update.message.reply_text("❌ Неверный код")
            return
        
        save_approved_user(user_id, update.effective_user.full_name)
        await update.message.reply_text("✅ Добро пожаловать! Нажми /menu")
    except Exception as e:
        logger.error(f"Code error: {e}")

async def menu_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Menu command"""
    try:
        if not is_approved(update.effective_user.id):
            await update.message.reply_text("🔒 Нет доступа")
            return
        await update.message.reply_text("🍄 Меню:", reply_markup=main_menu())
    except Exception as e:
        logger.error(f"Menu error: {e}")

# ============ BUTTONS ============
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle button clicks"""
    try:
        query = update.callback_query
        await query.answer()
        
        data = query.data
        
        if data == "menu_main":
            await query.edit_message_text("🍄 Меню:", reply_markup=main_menu())
        elif data == "menu_classes":
            await query.edit_message_text("⚔️ Выбери класс:", reply_markup=classes_menu())
        elif data == "menu_builds":
            await query.edit_message_text("🏹 Быстрый гайд:\n🪶 Plume - лучший класс\n⚔️ Warbringer - танк\n✨ Prophet - маг\n\n/menu для возврата")
        elif data == "menu_pals":
            await query.edit_message_text("🐾 Питомцы дают бонусы и урон\n\n/menu для возврата")
        elif data == "menu_events":
            await query.edit_message_text("📅 Задай вопрос об ивентах!\n\n/menu для возврата")
        elif data == "menu_beginner":
            await query.edit_message_text("💡 Советы:\n🏹 Начни с Лучника\n🪶 Plume лучше всех\n⚔️ Вестник тоже хорош\n\n/menu для возврата")
        elif data == "menu_help":
            await query.edit_message_text("❓ /menu - меню\n/classes - классы\n/code - ввод кода\n\n/menu для возврата")
        elif data == "class_warrior":
            await query.edit_message_text("⚔️ Воин - контрудары\nСилён против Лучников\n\n/menu для возврата")
        elif data == "class_archer":
            await query.edit_message_text("🏹 Лучник - комбо\nЛучший класс для начала\n\n/menu для возврата")
        elif data == "class_mage":
            await query.edit_message_text("🔮 Маг - скилы\nСильный в начале игры\n\n/menu для возврата")
        elif data == "class_tamer":
            await query.edit_message_text("🐉 Укротитель - питомцы\nХорош с розовыми питомцами\n\n/menu для возврата")
    except Exception as e:
        logger.error(f"Button error: {e}")

# ============ MESSAGES ============
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle messages"""
    try:
        if not update.message or not update.message.text:
            return
        
        user_id = update.effective_user.id
        
        if not is_approved(user_id):
            await update.message.reply_text("🔒 /code ТВОЙ_КОД")
            return
        
        text = update.message.text.strip()
        if not text or text.startswith('/'):
            return
        
        await update.message.chat.send_action("typing")
        
        # Simple AI response (placeholder for now)
        response = f"🍄 Спасибо за вопрос! Я обработал: {text[:50]}...\n\nТвой вопрос сохранён!"
        await update.message.reply_text(response)
        
    except Exception as e:
        logger.error(f"Message error: {e}")
        try:
            await update.message.reply_text("❌ Ошибка обработки сообщения")
        except:
            pass

# ============ MAIN ============
def main():
    """Start bot"""
    try:
        init_db()
        
        if not TELEGRAM_TOKEN:
            logger.error("❌ TELEGRAM_TOKEN not set!")
            return
        
        app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
        
        # Commands
        app.add_handler(CommandHandler("start", start))
        app.add_handler(CommandHandler("menu", menu_command))
        app.add_handler(CommandHandler("code", code_command))
        
        # Handlers
        app.add_handler(CallbackQueryHandler(button_handler))
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
        
        logger.info("🍄 Bot started!")
        app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)
        
    except Exception as e:
        logger.error(f"❌ Fatal error: {e}")

if __name__ == "__main__":
    main()

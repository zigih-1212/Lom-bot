import os
import logging
import httpx
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes

logging.basicConfig(level=logging.INFO)

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY")

MODELS = [
    "google/gemma-4-31b-it:free",
    "google/gemma-4-26b-a4b-it:free",
    "openai/gpt-oss-120b:free",
    "liquid/lfm-2.5-1.2b-instruct:free",
    "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free",
]

SYSTEM_PROMPT = """You are Shroom Helper — a friendly AI assistant for the mobile game Legend of Mushroom (LoM).

You help players with:
- Classes and builds (Warrior, Mage, Archer, etc.)
- Equipment and upgrades
- Events and quests
- Alliance tips
- Game mechanics (lamp, skills, pets, etc.)
- General game advice

Rules:
- Always detect the language of the user's message and reply in the SAME language (Russian or English)
- Be friendly, helpful and concise
- If you don't know something specific about LoM, say so honestly
- Use game terms correctly (Lamp, Genie, Skills, Alliance, etc.)
- Keep answers short and clear (mobile-friendly)

You only answer questions related to Legend of Mushroom. For unrelated topics, politely redirect to the game.
"""

async def ask_ai(user_message: str) -> str:
    for model in MODELS:
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
                    json={
                        "model": model,
                        "messages": [
                            {"role": "system", "content": SYSTEM_PROMPT},
                            {"role": "user", "content": user_message}
                        ],
                        "max_tokens": 500,
                    }
                )
            data = response.json()
            if "choices" in data:
                logging.info(f"Success with model: {model}")
                return data["choices"][0]["message"]["content"]
            else:
                logging.warning(f"Model {model} failed: {data.get('error', {}).get('message', 'unknown')}")
        except Exception as e:
            logging.error(f"Model {model} error: {e}")
    return None

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🍄 Привет! Я Shroom Helper — твой помощник по Legend of Mushroom!\n\n"
        "Спрашивай что угодно про игру: классы, билды, экипировку, ивенты и многое другое.\n\n"
        "В групповом чате обращайся ко мне через @упоминание или ответь на моё сообщение.\n\n"
        "🍄 Hello! I'm Shroom Helper — your Legend of Mushroom assistant!\n"
        "In group chats, mention me with @ or reply to my message!"
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🍄 *Что я умею:*\n\n"
        "• Отвечать на вопросы о классах и билдах\n"
        "• Помогать с экипировкой и прокачкой\n"
        "• Рассказывать об ивентах\n"
        "• Давать советы по альянсу\n"
        "• Объяснять механики игры\n\n"
        "В группе: напиши @имя\\_бота вопрос или ответь на моё сообщение 👇",
        parse_mode="Markdown"
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    if message is None:
        return

    bot_username = context.bot.username
    is_private = message.chat.type == "private"
    is_mention = message.text and f"@{bot_username}" in message.text
    is_reply_to_bot = (
        message.reply_to_message and
        message.reply_to_message.from_user and
        message.reply_to_message.from_user.username == bot_username
    )

    # В личке отвечаем всегда, в группе — только при упоминании или ответе
    if not is_private and not is_mention and not is_reply_to_bot:
        return

    # Убираем @упоминание из текста
    user_message = message.text or ""
    if is_mention:
        user_message = user_message.replace(f"@{bot_username}", "").strip()

    if not user_message:
        await message.reply_text("🍄 Задай вопрос про Legend of Mushroom!")
        return

    await message.chat.send_action("typing")
    reply = await ask_ai(user_message)

    if reply:
        await message.reply_text(reply)
    else:
        await message.reply_text(
            "⚠️ Все AI модели сейчас перегружены. Попробуй через минуту!\n"
            "⚠️ All AI models are overloaded. Try again in a minute!"
        )

if __name__ == "__main__":
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    print("🍄 Shroom Helper запущен!")
    app.run_polling()

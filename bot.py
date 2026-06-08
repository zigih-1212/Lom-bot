import os
import logging
import httpx
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes

# Настройка логов
logging.basicConfig(level=logging.INFO)

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY")

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

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🍄 Привет! Я Shroom Helper — твой помощник по Legend of Mushroom!\n\n"
        "Спрашивай что угодно про игру: классы, билды, экипировку, ивенты и многое другое.\n\n"
        "🍄 Hello! I'm Shroom Helper — your Legend of Mushroom assistant!\n"
        "Ask me anything about the game!"
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🍄 *Что я умею:*\n\n"
        "• Отвечать на вопросы о классах и билдах\n"
        "• Помогать с экипировкой и прокачкой\n"
        "• Рассказывать об ивентах\n"
        "• Давать советы по альянсу\n"
        "• Объяснять механики игры\n\n"
        "Просто напиши свой вопрос! 👇",
        parse_mode="Markdown"
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_message = update.message.text
    
    await update.message.chat.send_action("typing")
    
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": "meta-llama/llama-3.3-70b-instruct:free",
                    "messages": [
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": user_message}
                    ],
                    "max_tokens": 500,
                }
            )
        
        data = response.json()
        reply = data["choices"][0]["message"]["content"]
        await update.message.reply_text(reply)
        
    except Exception as e:
        logging.error(f"Error: {e}")
        await update.message.reply_text(
            "⚠️ Произошла ошибка. Попробуй ещё раз!\n"
            "⚠️ Something went wrong. Please try again!"
        )

if __name__ == "__main__":
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    print("🍄 Shroom Helper запущен!")
    app.run_polling()

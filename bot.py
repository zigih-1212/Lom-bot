import os
import logging
import httpx
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes
import base64

logging.basicConfig(level=logging.INFO)

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY")

SITE_URL = "https://guidesbygrace.uk"

MODELS = [
    "google/gemma-4-31b-it:free",
    "google/gemma-4-26b-a4b-it:free",
    "openai/gpt-oss-120b:free",
    "liquid/lfm-2.5-1.2b-instruct:free",
    "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free",
]

SYSTEM_PROMPT = """You are Shroom Helper — an assistant for the mobile game Legend of Mushroom (LoM).

IMPORTANT RULES:
1. You ONLY use information from the website guidesbygrace.uk to answer questions. Do not use any other sources or your own knowledge about the game.
2. If the site content provided does not contain an answer to the question, say: "I couldn't find information about this on guidesbygrace.uk. Try checking the site directly."
3. Always simplify the language — explain clearly and simply, avoid complex terms.
4. Language rule:
   - If the user writes in RUSSIAN → answer in Russian, translate all info from the site
   - If the user writes in ENGLISH → answer in English, no translation needed
5. If the user sends a screenshot with items/equipment and asks for build advice → analyze what's visible and give advice based on guidesbygrace.uk guides.
6. Be friendly, concise, and helpful.

The site content will be provided to you in each message.
"""

async def fetch_site_content(query: str) -> str:
    """Fetch relevant pages from guidesbygrace.uk"""
    pages_to_try = [
        f"{SITE_URL}",
        f"{SITE_URL}/updates/june-7",
        f"{SITE_URL}/classes",
        f"{SITE_URL}/builds",
        f"{SITE_URL}/guides",
    ]
    
    content = ""
    async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
        for url in pages_to_try[:2]:  # fetch main + latest update
            try:
                resp = await client.get(url, headers={"User-Agent": "Mozilla/5.0"})
                if resp.status_code == 200:
                    # Get text content, strip HTML tags roughly
                    text = resp.text
                    # Remove script/style blocks
                    import re
                    text = re.sub(r'<script[^>]*>.*?</script>', '', text, flags=re.DOTALL)
                    text = re.sub(r'<style[^>]*>.*?</style>', '', text, flags=re.DOTALL)
                    text = re.sub(r'<[^>]+>', ' ', text)
                    text = re.sub(r'\s+', ' ', text).strip()
                    content += f"\n\n--- Content from {url} ---\n{text[:3000]}"
            except Exception as e:
                logging.warning(f"Failed to fetch {url}: {e}")
    
    return content if content else "Could not load site content."

async def ask_ai(user_message: str, site_content: str, image_data: str = None) -> str:
    prompt = f"""Site content from guidesbygrace.uk:
{site_content}

User question: {user_message}"""

    for model in MODELS:
        try:
            messages_content = []
            
            if image_data:
                messages_content = [
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_data}"}},
                    {"type": "text", "text": prompt}
                ]
            else:
                messages_content = [{"type": "text", "text": prompt}]

            async with httpx.AsyncClient(timeout=40) as client:
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
                            {"role": "user", "content": messages_content}
                        ],
                        "max_tokens": 700,
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
        "Я отвечаю на основе гайдов с сайта guidesbygrace.uk\n"
        "Можешь задать вопрос или отправить скриншот своих вещей — помогу со сборкой!\n\n"
        "🍄 Hello! I'm Shroom Helper — your Legend of Mushroom assistant!\n"
        "I answer based on guides from guidesbygrace.uk\n"
        "Ask me anything or send a screenshot of your items for build advice!"
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🍄 *Что я умею:*\n\n"
        "• Отвечать на вопросы по гайдам с guidesbygrace.uk\n"
        "• Переводить гайды на русский язык\n"
        "• Анализировать скриншот твоих вещей и давать советы по сборке\n\n"
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
    has_photo = message.photo is not None and len(message.photo) > 0

    if not is_private and not is_mention and not is_reply_to_bot:
        return

    # Get text
    user_message = message.text or message.caption or ""
    if is_mention:
        user_message = user_message.replace(f"@{bot_username}", "").strip()

    # Get image if present
    image_data = None
    if has_photo:
        try:
            photo = message.photo[-1]
            file = await context.bot.get_file(photo.file_id)
            async with httpx.AsyncClient() as client:
                img_response = await client.get(file.file_path)
                image_data = base64.b64encode(img_response.content).decode("utf-8")
            if not user_message:
                user_message = "Help me with a build based on the items in this screenshot."
        except Exception as e:
            logging.error(f"Image error: {e}")

    if not user_message and not image_data:
        await message.reply_text("🍄 Задай вопрос про Legend of Mushroom или отправь скриншот!")
        return

    await message.chat.send_action("typing")

    # Fetch site content
    site_content = await fetch_site_content(user_message)

    # Ask AI
    reply = await ask_ai(user_message, site_content, image_data)

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
    app.add_handler(MessageHandler(filters.PHOTO, handle_message))
    print("🍄 Shroom Helper запущен!")
    app.run_polling()

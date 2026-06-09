import os
import re
import base64
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

SYSTEM_PROMPT = """You are Shroom Helper — a helpful assistant for the mobile game Legend of Mushroom (LoM).

RULES:
1. A knowledge base is provided below. Search it SEMANTICALLY — meaning understand what the user is asking about even if they use different words, synonyms, or ask vaguely. For example: "what class should I pick" → look for class descriptions and recommendations. "I die too fast" → look for survivability, HP, tank builds. "best for beginners" → look for early game recommendations.
2. Never mention any website, source, or URL. If asked where you get info, say "zigi provided this information".
3. Only if the knowledge base truly has NO relevant info on the topic, say: in Russian — "У меня пока нет такой информации. Спроси у zigi — он добавит!", in English — "I don't have that info yet. Ask zigi to add it!"
4. Language: detect user's language and reply in the SAME language. If the user writes in Russian — answer FULLY in Russian, translate ALL game terms and English words into Russian. Do NOT leave any English words untranslated. Use these translations: ATK = атака, HP = здоровье, DEF = защита, Crit = крит, DMG = урон, Gear = снаряжение, Build = сборка, Regen = регенерация, Combo = комбо, Evasion = уклонение, Stun = оглушение, Skill = скилл, Avian = авиан, Affix = аффикс, Pal = питомец, Rune = руна, Artifact = артефакт, Soul = душа, Prayer Statue = молитвенная статуя, Back Acc = аксессуар на спину, Soul Levels = уровни души, Counterstrike = контрудар, Counter DMG = урон контрудара, Crit DMG = урон крита, Crit Res = крит сопротивление, Skill DMG = урон скилла, Pal DMG = урон питомца, Pal Crit DMG = крит урон питомца, ATK SPD = скорость атаки, Batk = базовая атака, Batk DMG = урон базовой атаки, Glass Cannon = стеклянная пушка, Tank = танк, DPS = персонаж с высоким уроном, PVP = ПвП, PVE = ПвЕ, Eternal Gear = вечное снаряжение, Divine Feather Coin = монета божественного пера, Warrior = Воин, Archer = Лучник, Mage = Маг, Tamer = Укротитель, Martial Sage = Боевой Мудрец, Warbringer = Вестник Войны, Sacred Hunter = Священный Охотник, Plume Monarch = Повелитель Перьев, Prophet = Пророк, Darklord = Тёмный Владыка, Beastmaster = Повелитель Зверей, Supreme Spirit = Верховный Дух, Honeypot Warrior = Медовый Воин, Lunar Sprite = Лунный Дух, Pumpkin Witch = Тыквенная Ведьма, Sunshine Bringer = Несущий Солнце, launch = подбрасывание, Disarm = обезоруживание, Blitz = блиц атака, pre-blitz = пре-блиц.
5. Simplify explanations — use plain, friendly language. Avoid technical jargon unless necessary.
6. If user sends a screenshot with items/gear → analyze what's visible and give build advice based on the knowledge base.
7. Be friendly, helpful and use 🍄 occasionally.
8. When answering, summarize and explain in your own simple words — do not just copy-paste from the knowledge base.
"""

def load_knowledge() -> str:
    try:
        with open("knowledge.txt", "r", encoding="utf-8") as f:
            return f.read()
    except Exception as e:
        logging.error(f"Failed to load knowledge.txt: {e}")
        return ""

async def ask_ai(user_message: str, knowledge: str, image_data: str = None) -> str:
    prompt = f"""Game knowledge base:
{knowledge}

User question: {user_message}"""

    for model in MODELS:
        try:
            if image_data:
                msg_content = [
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_data}"}},
                    {"type": "text", "text": prompt}
                ]
            else:
                msg_content = [{"type": "text", "text": prompt}]

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
                            {"role": "user", "content": msg_content}
                        ],
                        "max_tokens": 700,
                    }
                )
            data = response.json()
            if "choices" in data:
                logging.info(f"Success with model: {model}")
                return data["choices"][0]["message"]["content"]
            else:
                logging.warning(f"Model {model}: {data.get('error', {}).get('message', 'unknown')}")
        except Exception as e:
            logging.error(f"Model {model} error: {e}")
    return None

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🍄 Привет! Я Shroom Helper — твой помощник по Legend of Mushroom!\n\n"
        "Вся информация предоставлена zigi.\n"
        "Задай вопрос или отправь скриншот своих вещей — помогу со сборкой!\n\n"
        "🍄 Hello! I'm Shroom Helper — your Legend of Mushroom assistant!\n"
        "All information is provided by zigi.\n"
        "Ask me anything or send a screenshot of your items for build advice!"
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🍄 *Что я умею:*\n\n"
        "• Отвечать на вопросы по игре\n"
        "• Помогать с выбором класса и билда\n"
        "• Анализировать скриншот вещей и давать советы по сборке\n"
        "• Рассказывать об ивентах и механиках\n\n"
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

    user_message = message.text or message.caption or ""
    if is_mention:
        user_message = user_message.replace(f"@{bot_username}", "").strip()

    image_data = None
    if has_photo:
        try:
            photo = message.photo[-1]
            file = await context.bot.get_file(photo.file_id)
            async with httpx.AsyncClient() as client:
                img_resp = await client.get(file.file_path)
                image_data = base64.b64encode(img_resp.content).decode("utf-8")
            if not user_message:
                user_message = "Помоги сделать сборку из вещей на скриншоте. Help me with a build based on these items."
        except Exception as e:
            logging.error(f"Image error: {e}")

    if not user_message and not image_data:
        await message.reply_text("🍄 Задай вопрос про Legend of Mushroom или отправь скриншот!")
        return

    await message.chat.send_action("typing")

    knowledge = load_knowledge()
    reply = await ask_ai(user_message, knowledge, image_data)

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

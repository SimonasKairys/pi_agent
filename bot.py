from dotenv import load_dotenv
load_dotenv()
import os
import logging
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from openai import AsyncOpenAI

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

OPENROUTER_API_KEY = os.environ["OPENROUTER_API_KEY"]
TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
# Comma-separated Telegram user IDs allowed to use the bot, e.g. "111111,222222"
ALLOWED_USER_IDS = {
    int(uid.strip()) for uid in os.environ["TELEGRAM_ALLOWED_USER_IDS"].split(",") if uid.strip()
}

client = AsyncOpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=OPENROUTER_API_KEY,
)


def is_authorized(update: Update) -> bool:
    return update.effective_user is not None and update.effective_user.id in ALLOWED_USER_IDS


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        await update.message.reply_text("Sorry, you're not authorized to use this bot.")
        return
    await update.message.reply_text("Hello! I am your AI agent. Ask me anything.")


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        await update.message.reply_text("Sorry, you're not authorized to use this bot.")
        return

    user_text = update.message.text
    await update.message.chat.send_action(action="typing")

    try:
        response = await client.chat.completions.create(
            model="deepseek/deepseek-v4.1-flash",
            messages=[
                {"role": "system", "content": "You are a helpful AI assistant connected to Telegram."},
                {"role": "user", "content": user_text},
            ],
            max_tokens=800,
        )
        reply_text = response.choices[0].message.content
        await update.message.reply_text(reply_text)

    except Exception:
        logger.exception("Failed to get a response from OpenRouter")
        await update.message.reply_text("Sorry, something went wrong processing that. Try again in a moment.")


def main():
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    print("Bot is running... Send a message to it in Telegram!")
    app.run_polling()


if __name__ == "__main__":
    main()

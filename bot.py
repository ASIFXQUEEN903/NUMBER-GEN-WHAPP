import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
import os

# Bot Token (Heroku/GitHub me Config Vars me set karo)
TOKEN = os.environ.get("BOT_TOKEN")

bot = telebot.TeleBot(TOKEN)

@bot.message_handler(commands=['start'])
def start(message):
    # Stylish Welcome Text
    welcome_text = (
        "✨ 𝐖𝐞𝐥𝐜𝐨𝐦𝐞 𝐭𝐨 𝐍𝐮𝐦𝐛𝐞𝐫 𝐆𝐞𝐧 𝐁𝐨𝐭 ✨\n\n"
        "⚡ Created with ❤️ by ARAME9 ⚡"
    )

    # Button with Nobita_903
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("👑 Nobita_903", url="https://t.me/Nobita_903"))

    bot.send_message(message.chat.id, welcome_text, reply_markup=markup)

# Run Bot
bot.infinity_polling()

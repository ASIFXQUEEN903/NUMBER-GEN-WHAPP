import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
import os

TOKEN = os.environ.get("BOT_TOKEN")
bot = telebot.TeleBot(TOKEN)

@bot.message_handler(commands=['start'])
def start(message):
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("📂 File", callback_data="file"))
    markup.add(InlineKeyboardButton("📞 Manual Number", callback_data="manual"))
    markup.add(InlineKeyboardButton("⚙️ App JSON", callback_data="json"))
    bot.send_message(message.chat.id, "Hey! Choose an option below 👇", reply_markup=markup)

@bot.callback_query_handler(func=lambda call: True)
def callback_handler(call):
    if call.data == "file":
        bot.send_message(call.message.chat.id, "📂 You clicked on File.")
    elif call.data == "manual":
        bot.send_message(call.message.chat.id, "📞 You clicked on Manual Number.")
    elif call.data == "json":
        bot.send_message(call.message.chat.id, "⚙️ You clicked on App JSON.")

bot.infinity_polling()

import os
import re
import random
import time
from pymongo import MongoClient
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton

# -----------------------
# CONFIG
# -----------------------
BOT_TOKEN = os.getenv("BOT_TOKEN")
MONGO_URL = os.getenv("MONGO_URL")
DB_NAME = os.getenv("DB_NAME", "nobita903")

if not BOT_TOKEN or not MONGO_URL:
    raise Exception("Please set BOT_TOKEN and MONGO_URL environment variables")

bot = telebot.TeleBot(BOT_TOKEN, parse_mode="HTML")

mc = MongoClient(MONGO_URL)
db = mc[DB_NAME]

lists_coll = db["lists"]
sessions_coll = db["sessions"]

NUM_LINE_REGEX = re.compile(r'[\d\+\s]+')


# -----------------------
# UTILITIES
# -----------------------
def normalize_raw(s: str) -> str:
    if not s:
        return ""
    s = s.strip().replace(" ", "")
    if s.startswith("+"):
        s = s[1:]
    s = re.sub(r'^00', '', s)
    s = re.sub(r'\D', '', s)
    return s


def parse_numbers_from_text(text: str):
    lines = text.splitlines()
    nums = []
    for ln in lines:
        ln = ln.strip()
        if not ln:
            continue
        m = NUM_LINE_REGEX.search(ln)
        if m:
            raw = m.group().strip()
            norm = normalize_raw(raw)
            if norm:
                nums.append(norm)
    return nums


def replace_list(chat_id: int, numbers: list):
    """⚡ Purani list delete karke sirf naye numbers save karo"""
    lists_coll.delete_one({"_id": chat_id})
    new_entries = [{"normalized": n, "used": False, "ts_added": time.time()} for n in numbers]
    lists_coll.insert_one({"_id": chat_id, "numbers": new_entries, "created_at": time.time()})


def get_unused(chat_id: int):
    doc = lists_coll.find_one({"_id": chat_id})
    return [e for e in doc["numbers"] if not e.get("used", False)] if doc else []


def pick_random_unused(chat_id: int):
    unused = get_unused(chat_id)
    return random.choice(unused) if unused else None


def mark_used(chat_id: int, num: str):
    lists_coll.update_one(
        {"_id": chat_id, "numbers.normalized": num},
        {"$set": {"numbers.$.used": True, "numbers.$.ts_used": time.time()}}
    )


# -----------------------
# START
# -----------------------
@bot.message_handler(commands=["start"])
def cmd_start(m):
    welcome = (
        "✨ 𝐖𝐞𝐥𝐜𝐨𝐦𝐞 𝐭𝐨 𝐍𝐮𝐦𝐛𝐞𝐫 𝐆𝐞𝐧 𝐁𝐨𝐭 ✨\n\n"
        "📌 New file ya numbers paste karne par purana sab clear ho jaayega.\n"
        "🎲 Random number milega ek click me.\n\n"
        "Use /gen to upload numbers.\n"
    )
    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton("👑 Nobita_903", url="https://t.me/Nobita_903"))
    bot.send_message(m.chat.id, welcome, reply_markup=kb)


# -----------------------
# GEN
# -----------------------
@bot.message_handler(commands=["gen"])
def cmd_gen(m):
    sessions_coll.update_one(
        {"user_id": m.from_user.id},
        {"$set": {"user_id": m.from_user.id, "chat_id": m.chat.id, "state": "await_numbers"}},
        upsert=True,
    )
    bot.reply_to(m, "📂 Send a .txt file or paste numbers (one per line).")


@bot.message_handler(content_types=['document'])
def handle_document(m):
    s = sessions_coll.find_one({"user_id": m.from_user.id})
    if not s or s.get("state") != "await_numbers":
        return
    try:
        file_info = bot.get_file(m.document.file_id)
        downloaded = bot.download_file(file_info.file_path)
        text = downloaded.decode('utf-8', errors='ignore')
    except:
        bot.reply_to(m, "❌ Error reading file.")
        return
    nums = parse_numbers_from_text(text)
    if not nums:
        bot.reply_to(m, "❌ No valid numbers found.")
        return
    replace_list(m.chat.id, nums)
    # Save state to ask country code
    sessions_coll.update_one(
        {"user_id": m.from_user.id},
        {"$set": {"state": "await_country"}}
    )
    bot.reply_to(m, f"✅ Saved {len(nums)} numbers.\n🌍 Please send country code (example: +91)")


@bot.message_handler(func=lambda msg: True, content_types=['text'])
def handle_text(msg):
    s = sessions_coll.find_one({"user_id": msg.from_user.id})
    if not s:
        bot.reply_to(msg, "Use /gen to upload numbers or /start to see welcome.")
        return

    if s.get("state") == "await_numbers":
        nums = parse_numbers_from_text(msg.text)
        if not nums:
            bot.reply_to(msg, "❌ No valid numbers found.")
            return
        replace_list(msg.chat.id, nums)
        sessions_coll.update_one(
            {"user_id": msg.from_user.id},
            {"$set": {"state": "await_country"}}
        )
        bot.reply_to(msg, f"✅ Saved {len(nums)} numbers.\n🌍 Please send country code (example: +91)")

    elif s.get("state") == "await_country":
        cc = msg.text.strip()
        if not cc.startswith("+") or not cc[1:].isdigit():
            bot.reply_to(msg, "❌ Invalid country code. Example: +91")
            return
        sessions_coll.update_one(
            {"user_id": msg.from_user.id},
            {"$set": {"state": "ready", "country_code": cc}}
        )
        bot.reply_to(msg, f"✅ Country code set to {cc}\nClick below to get numbers.", reply_markup=start_kb())


# -----------------------
# Inline Keyboards
# -----------------------
def start_kb():
    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton("▶️ Get Number", callback_data="get_number"))
    return kb


# -----------------------
# Callbacks
# -----------------------
@bot.callback_query_handler(func=lambda call: True)
def cb(call):
    if call.data == "get_number":
        send_one(call.message.chat.id)


# -----------------------
# Core
# -----------------------
def send_one(chat_id: int):
    s = sessions_coll.find_one({"chat_id": chat_id})
    if not s or s.get("state") != "ready":
        bot.send_message(chat_id, "⚠️ Please /gen to upload file and set country code first.")
        return

    pick = pick_random_unused(chat_id)
    if not pick:
        bot.send_message(chat_id, "🎉 All numbers are finished.")
        return

    num = pick["normalized"]
    cc = s.get("country_code")
    if cc and num.startswith(cc.replace("+", "")):
        num = num[len(cc) - 1 :]  # remove cc digits

    mark_used(chat_id, pick["normalized"])
    bot.send_message(
        chat_id,
        f"<code>{num}</code>",
        reply_markup=start_kb(),
        parse_mode="HTML"
    )


# -----------------------
# Run bot
# -----------------------
if __name__ == "__main__":
    print("Bot started")
    bot.infinity_polling(timeout=60, long_polling_timeout=90)

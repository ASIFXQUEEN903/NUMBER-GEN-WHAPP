import os
import re
import random
import time
from pymongo import MongoClient
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton

# -----------------------
# CONFIG (set these in Heroku Config Vars)
# -----------------------
BOT_TOKEN = os.getenv("BOT_TOKEN")
MONGO_URL = os.getenv("MONGO_URL")
DB_NAME = os.getenv("DB_NAME", "nobita903")  # default db name if not set

if not BOT_TOKEN or not MONGO_URL:
    raise Exception("Please set BOT_TOKEN and MONGO_URL environment variables")

bot = telebot.TeleBot(BOT_TOKEN, parse_mode="HTML")

# Mongo setup
mc = MongoClient(MONGO_URL)
db = mc[DB_NAME]   # ✅ FIXED: direct DB select

# Collections
lists_coll = db["lists"]
sessions_coll = db["sessions"]
used_coll = db["used_global"]

NUM_LINE_REGEX = re.compile(r'[\d\+\s]+')  # crude

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

def display_after_removing_cc(fullnum: str, cc: str) -> str:
    if not fullnum:
        return ""
    if not cc:
        return fullnum[-10:] if len(fullnum) >= 10 else fullnum
    cc = cc.lstrip('+').lstrip('0')
    if fullnum.startswith(cc):
        return fullnum[len(cc):] or fullnum
    return fullnum[-10:] if len(fullnum) >= 10 else fullnum

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

def ensure_list_document(chat_id: int):
    doc = lists_coll.find_one({"_id": chat_id})
    if not doc:
        lists_coll.insert_one({"_id": chat_id, "numbers": [], "created_at": time.time()})
        return lists_coll.find_one({"_id": chat_id})
    return doc

def add_numbers_to_list(chat_id: int, numbers: list):
    doc = ensure_list_document(chat_id)
    existing = [entry["normalized"] for entry in doc["numbers"]]
    new_entries, added = [], 0
    for n in numbers:
        if n not in existing:
            entry = {"raw": n, "normalized": n, "used": False, "ts_added": time.time()}
            new_entries.append(entry)
            existing.append(n)
            added += 1
    if new_entries:
        lists_coll.update_one({"_id": chat_id}, {"$push": {"numbers": {"$each": new_entries}}})
    total = lists_coll.find_one({"_id": chat_id})["numbers"]
    return len(total), added

def get_unused_from_list(chat_id: int):
    doc = lists_coll.find_one({"_id": chat_id})
    return [e for e in doc["numbers"] if not e.get("used", False)] if doc else []

def mark_used_in_list(chat_id: int, normalized_num: str, given_to_user: int = None):
    lists_coll.update_one(
        {"_id": chat_id, "numbers.normalized": normalized_num},
        {"$set": {"numbers.$.used": True, "numbers.$.given_to": given_to_user, "numbers.$.ts_used": time.time()}}
    )

def pick_random_unused(chat_id: int):
    unused = get_unused_from_list(chat_id)
    return random.choice(unused) if unused else None

def set_session(user_id: int, chat_id: int, state: str, payload: dict = None):
    sessions_coll.update_one(
        {"user_id": user_id},
        {"$set": {"user_id": user_id, "chat_id": chat_id, "state": state, "payload": payload or {}, "ts": time.time()}},
        upsert=True
    )

def get_session(user_id: int):
    return sessions_coll.find_one({"user_id": user_id})

def clear_session(user_id: int):
    sessions_coll.delete_many({"user_id": user_id})

# -----------------------
# START
# -----------------------
@bot.message_handler(commands=["start"])
def cmd_start(m):
    welcome = (
        "✨ 𝐖𝐞𝐥𝐜𝐨𝐦𝐞 𝐭𝐨 𝐍𝐮𝐦𝐛𝐞𝐫 𝐆𝐞𝐧 𝐁𝐨𝐭 ✨\n\n"
        "📌 File ya manually diye gaye numbers se random numbers milega.\n"
        "🔒 Ek baar diya number dobara nahi milega.\n\n"
        "Use /gen to upload numbers.\n"
    )
    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton("👑 Nobita_903", url="https://t.me/Nobita_903"))
    bot.send_message(m.chat.id, welcome, reply_markup=kb)

# -----------------------
# GEN flow
# -----------------------
@bot.message_handler(commands=["gen"])
def cmd_gen(m):
    set_session(m.from_user.id, m.chat.id, "await_numbers")
    bot.reply_to(m, "Send a .txt file or paste numbers (one per line).")

@bot.message_handler(content_types=['document'])
def handle_document(m):
    s = get_session(m.from_user.id)
    if not s or s.get("state") != "await_numbers":
        return
    try:
        file_info = bot.get_file(m.document.file_id)
        downloaded = bot.download_file(file_info.file_path)
        text = downloaded.decode('utf-8', errors='ignore')
    except:
        bot.reply_to(m, "❌ Error reading file.")
        clear_session(m.from_user.id)
        return
    nums = parse_numbers_from_text(text)
    if not nums:
        bot.reply_to(m, "❌ No valid numbers found.")
        clear_session(m.from_user.id)
        return
    total, added = add_numbers_to_list(m.chat.id, nums)
    clear_session(m.from_user.id)
    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton("▶️ Get", callback_data="start_get"))
    bot.reply_to(m, f"✅ Numbers processed.\nTotal: {total}\nNewly added: {added}", reply_markup=kb)

@bot.message_handler(func=lambda msg: True, content_types=['text'])
def handle_text(msg):
    s = get_session(msg.from_user.id)
    if s and s.get("state") == "await_numbers":
        nums = parse_numbers_from_text(msg.text)
        if not nums:
            bot.reply_to(msg, "❌ No valid numbers found.")
            clear_session(msg.from_user.id)
            return
        total, added = add_numbers_to_list(msg.chat.id, nums)
        clear_session(msg.from_user.id)
        kb = InlineKeyboardMarkup()
        kb.add(InlineKeyboardButton("▶️ Get", callback_data="start_get"))
        bot.reply_to(msg, f"✅ Numbers processed.\nTotal: {total}\nNewly added: {added}", reply_markup=kb)
        return
    if s and s.get("state") == "await_cc":
        cc = re.sub(r'\D', '', msg.text)
        if not cc:
            bot.reply_to(msg, "❌ Please send a valid country code like 91 or 1.")
            return
        sessions_coll.update_one({"user_id": msg.from_user.id}, {"$set": {"state": "giving", "payload.cc": cc}})
        bot.reply_to(msg, f"Country code set to <b>{cc}</b>. Starting...", parse_mode="HTML")
        send_one_random_number(msg.from_user.id)
        return
    bot.reply_to(msg, "Use /gen to upload numbers or /start to see welcome.")

# -----------------------
# Callback handler
# -----------------------
@bot.callback_query_handler(func=lambda call: True)
def cb(call):
    if call.data == "start_get":
        set_session(call.from_user.id, call.message.chat.id, "await_cc")
        bot.answer_callback_query(call.id)
        bot.send_message(call.message.chat.id, "Send country code (e.g., 91).")
    elif call.data == "next_number":
        bot.answer_callback_query(call.id)
        send_one_random_number(call.from_user.id)
    elif call.data == "stop_giving":
        bot.answer_callback_query(call.id, "Stopped.")
        clear_session(call.from_user.id)
        bot.send_message(call.message.chat.id, "Stopped. Use /gen again.")

# -----------------------
# Core function
# -----------------------
def send_one_random_number(user_id: int):
    sess = get_session(user_id)
    if not sess or sess.get("state") != "giving":
        return
    chat_id, cc = sess["chat_id"], sess["payload"].get("cc", "")
    pick = pick_random_unused(chat_id)
    if not pick:
        bot.send_message(chat_id, "⚠️ No unused numbers left.")
        clear_session(user_id)
        return
    normalized = pick["normalized"]
    outnum = display_after_removing_cc(normalized, cc)
    mark_used_in_list(chat_id, normalized, user_id)
    used_coll.insert_one({"number": normalized, "chat_id": chat_id, "user_id": user_id, "ts": time.time()})
    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton("➡️ Next", callback_data="next_number"),
           InlineKeyboardButton("⛔ Stop", callback_data="stop_giving"))
    bot.send_message(chat_id, f"📲 Number:\n<b>{outnum}</b>\n\nStored: <code>{normalized}</code>", reply_markup=kb, parse_mode="HTML")

# -----------------------
# /count command
# -----------------------
@bot.message_handler(commands=["count"])
def cmd_count(m):
    doc = lists_coll.find_one({"_id": m.chat.id})
    if not doc:
        bot.reply_to(m, "No list found. Use /gen first.")
        return
    all_count = len(doc["numbers"])
    used_count = sum(1 for e in doc["numbers"] if e.get("used"))
    bot.reply_to(m, f"📄 Total: {all_count}\n✅ Unused: {all_count - used_count}\n❌ Used: {used_count}")

# -----------------------
# Run bot
# -----------------------
if __name__ == "__main__":
    print("Bot started")
    bot.infinity_polling(timeout=60, long_polling_timeout=90)

# bot.py
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

if not BOT_TOKEN or not MONGO_URL:
    raise Exception("Please set BOT_TOKEN and MONGO_URL environment variables")

bot = telebot.TeleBot(BOT_TOKEN, parse_mode="HTML")

# Mongo setup
mc = MongoClient(MONGO_URL)
db = mc.get_default_database()

# Collections:
# lists: stores per-chat number lists -> { _id: chat_id, numbers: [ {raw: '9199...', normalized: '9199...', used: False} ], created_at }
# sessions: temporary states for flows -> { user_id: int, chat_id: int, state: 'await_numbers'|'await_cc'|'giving', ts: float }
lists_coll = db["lists"]
sessions_coll = db["sessions"]
used_coll = db["used_global"]  # optional separate store; main used flag in lists

NUM_LINE_REGEX = re.compile(r'[\d\+\s]+')  # crude

# -----------------------
# UTILITIES
# -----------------------
def normalize_raw(s: str) -> str:
    """Normalize incoming raw number string:
       - strip spaces
       - remove leading +
       - remove leading 00
       - remove non-digit
       returns digits only (may include country code at start)
    """
    if not s:
        return ""
    s = s.strip()
    s = s.replace(" ", "")
    if s.startswith("+"):
        s = s[1:]
    # remove leading 00
    s = re.sub(r'^00', '', s)
    # keep digits only
    s = re.sub(r'\D', '', s)
    return s

def display_after_removing_cc(fullnum: str, cc: str) -> str:
    """Remove provided country code cc from start of fullnum if present.
       If not present but len(fullnum) >= 10, fallback to last 10 digits.
    """
    if not fullnum:
        return ""
    if not cc:
        # fallback: last 10 digits if possible
        return fullnum[-10:] if len(fullnum) >= 10 else fullnum
    cc = cc.lstrip('+').lstrip('0')  # normalize cc
    if fullnum.startswith(cc):
        return fullnum[len(cc):] or fullnum
    # sometimes numbers saved had leading 0 after country removal; fallback to last 10
    if len(fullnum) >= 10:
        return fullnum[-10:]
    return fullnum

def parse_numbers_from_text(text: str):
    """Extract lines that look like numbers and normalize them."""
    lines = text.splitlines()
    nums = []
    for ln in lines:
        ln = ln.strip()
        if not ln:
            continue
        # extract digits and pluses
        m = NUM_LINE_REGEX.search(ln)
        if m:
            raw = m.group().strip()
            norm = normalize_raw(raw)
            if norm:
                nums.append(norm)
    return nums

def ensure_list_document(chat_id: int):
    doc = lists_coll.find_one({"_id": chat_id})
    if doc is None:
        lists_coll.insert_one({"_id": chat_id, "numbers": [], "created_at": time.time()})
        return lists_coll.find_one({"_id": chat_id})
    return doc

def add_numbers_to_list(chat_id: int, numbers: list):
    """Add list of normalized numbers to the chat's list, preserving order, removing duplicates."""
    doc = ensure_list_document(chat_id)
    existing = [entry["normalized"] for entry in doc["numbers"]]
    added = 0
    new_entries = []
    for n in numbers:
        if n not in existing:
            entry = {"raw": n, "normalized": n, "used": False, "ts_added": time.time()}
            new_entries.append(entry)
            existing.append(n)
            added += 1
    if new_entries:
        lists_coll.update_one({"_id": chat_id}, {"$push": {"numbers": {"$each": new_entries}}})
    # return total count and how many added now
    total = lists_coll.find_one({"_id": chat_id})["numbers"]
    return len(total), added

def get_unused_from_list(chat_id: int):
    doc = lists_coll.find_one({"_id": chat_id})
    if not doc:
        return []
    return [entry for entry in doc["numbers"] if not entry.get("used", False)]

def mark_used_in_list(chat_id: int, normalized_num: str, given_to_user: int = None):
    lists_coll.update_one({"_id": chat_id, "numbers.normalized": normalized_num},
                          {"$set": {"numbers.$.used": True, "numbers.$.given_to": given_to_user, "numbers.$.ts_used": time.time()}})

def pick_random_unused(chat_id: int):
    unused = get_unused_from_list(chat_id)
    if not unused:
        return None
    return random.choice(unused)

def set_session(user_id: int, chat_id: int, state: str, payload: dict = None):
    payload = payload or {}
    sessions_coll.update_one({"user_id": user_id}, {"$set": {"user_id": user_id, "chat_id": chat_id, "state": state, "payload": payload, "ts": time.time()}}, upsert=True)

def get_session(user_id: int):
    return sessions_coll.find_one({"user_id": user_id})

def clear_session(user_id: int):
    sessions_coll.delete_many({"user_id": user_id})

# -----------------------
# START command (stylish + owner button Nobita_903)
# -----------------------
@bot.message_handler(commands=["start"])
def cmd_start(m):
    welcome = (
        "✨ 𝐖𝐞𝐥𝐜𝐨𝐦𝐞 𝐭𝐨 𝐍𝐮𝐦𝐛𝐞𝐫 𝐆𝐞𝐧 𝐁𝐨𝐭 ✨\n\n"
        "📌 Yeh bot file se ya manually diye gaye numbers me se random numbers dega.\n"
        "🔒 Jo number ek baar de diya jayega vo dobara nahi diya jayega.\n\n"
        "Use /gen to upload numbers or paste them.\n"
    )
    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton("👑 Nobita_903", url="https://t.me/Nobita_903"))
    bot.send_message(m.chat.id, welcome, reply_markup=kb)

# -----------------------
# GEN flow: ask user to send file or paste numbers
# -----------------------
@bot.message_handler(commands=["gen"])
def cmd_gen(m):
    chat_id = m.chat.id
    set_session(m.from_user.id, chat_id, "await_numbers")
    bot.reply_to(m, "Send a .txt file containing numbers OR paste numbers (one per line).\n\nExamples accepted:\n`918093256780`\n`+919876543210`\n`00919876543210`\n`8093256780`\n\n(After upload I'll show total count and a Get button.)")

# Handler for document (file) upload
@bot.message_handler(content_types=['document'])
def handle_document(m):
    s = get_session(m.from_user.id)
    if not s or s.get("state") != "await_numbers":
        # ignore or inform
        return
    # download file
    try:
        file_info = bot.get_file(m.document.file_id)
        downloaded = bot.download_file(file_info.file_path)
        text = downloaded.decode('utf-8', errors='ignore')
    except Exception as e:
        bot.reply_to(m, "Error reading file. Make sure it's a text file.")
        clear_session(m.from_user.id)
        return

    nums = parse_numbers_from_text(text)
    if not nums:
        bot.reply_to(m, "No valid numbers found in the file.")
        clear_session(m.from_user.id)
        return

    total, added = add_numbers_to_list(m.chat.id, nums)
    clear_session(m.from_user.id)

    # show result with Get button
    resp = f"✅ Numbers processed.\nTotal in list: {total}\nNewly added: {added}"
    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton("▶️ Get", callback_data="start_get"))
    bot.reply_to(m, resp, reply_markup=kb)

# Handler for pasted text numbers
@bot.message_handler(func=lambda msg: True, content_types=['text'])
def handle_text(msg):
    s = get_session(msg.from_user.id)
    text = msg.text.strip()
    # If waiting for numbers
    if s and s.get("state") == "await_numbers":
        nums = parse_numbers_from_text(text)
        if not nums:
            bot.reply_to(msg, "No valid numbers found in your message. Send one-per-line numbers or upload a .txt file.")
            clear_session(msg.from_user.id)
            return
        total, added = add_numbers_to_list(msg.chat.id, nums)
        clear_session(msg.from_user.id)
        resp = f"✅ Numbers processed.\nTotal in list: {total}\nNewly added: {added}"
        kb = InlineKeyboardMarkup()
        kb.add(InlineKeyboardButton("▶️ Get", callback_data="start_get"))
        bot.reply_to(msg, resp, reply_markup=kb)
        return

    # If waiting for country code for number-giving session
    if s and s.get("state") == "await_cc":
        cc = re.sub(r'\D', '', text)  # digits only
        if not cc:
            bot.reply_to(msg, "Please send a valid country code like `91` or `1` (digits only).")
            return
        # store chosen cc in session and switch to giving
        sessions_coll.update_one({"user_id": msg.from_user.id}, {"$set": {"state": "giving", "payload.cc": cc, "ts": time.time()}})
        bot.reply_to(msg, f"Country code set to <b>{cc}</b>. I'll now send random numbers from the list (country code removed).", parse_mode="HTML")
        # Immediately send first number
        send_one_random_number(msg.from_user.id)
        return

    # default fallback
    # You can expand fallback as you like
    bot.reply_to(msg, "Use /gen to upload numbers or /start to see welcome message.")

# -----------------------
# Callback query handler (buttons)
# -----------------------
@bot.callback_query_handler(func=lambda call: True)
def cb(call):
    data = call.data or ""
    user_id = call.from_user.id
    chat_id = call.message.chat.id

    if data == "start_get":
        # start the flow: ask country code
        # set session awaiting country code
        set_session(user_id, chat_id, "await_cc")
        bot.answer_callback_query(call.id)
        bot.send_message(chat_id, "Send the country code to remove from numbers (e.g., `91`).\nIf you want last-10-digits fallback, send `0` or leave as `0`.", parse_mode="HTML")
        return

    if data == "next_number":
        bot.answer_callback_query(call.id)
        send_one_random_number(user_id)
        return

    if data == "stop_giving":
        bot.answer_callback_query(call.id, "Stopped.")
        clear_session(user_id)
        try:
            bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=None)
        except:
            pass
        bot.send_message(call.message.chat.id, "Stopped giving numbers. Use /gen again when needed.")
        return

    # unknown callback
    bot.answer_callback_query(call.id, "Unknown action.")

# -----------------------
# Core: send one random unused number (for the session user)
# -----------------------
def send_one_random_number(user_id: int):
    sess = get_session(user_id)
    if not sess or sess.get("state") != "giving":
        return
    chat_id = sess.get("chat_id")
    cc = sess.get("payload", {}).get("cc", "")
    # pick from that chat's list
    pick = pick_random_unused(chat_id)
    if not pick:
        bot.send_message(chat_id, "⚠️ No unused numbers left in the list.")
        clear_session(user_id)
        return
    normalized = pick["normalized"]
    # display number with country code removed per user request
    outnum = display_after_removing_cc(normalized, cc)
    # Mark used
    mark_used_in_list(chat_id, normalized, user_id)
    # optional: record global used
    used_coll.insert_one({"number": normalized, "chat_id": chat_id, "user_id": user_id, "ts": time.time()})

    # Build inline buttons: Next / Stop
    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton("➡️ Next", callback_data="next_number"),
           InlineKeyboardButton("⛔ Stop", callback_data="stop_giving"))
    text = f"📲 Here is a number:\n<b>{outnum}</b>\n\n(Original stored: <code>{normalized}</code>)"
    bot.send_message(chat_id, text, reply_markup=kb, parse_mode="HTML")

# -----------------------
# Simple /count command to check totals
# -----------------------
@bot.message_handler(commands=["count"])
def cmd_count(m):
    doc = lists_coll.find_one({"_id": m.chat.id})
    if not doc:
        bot.reply_to(m, "No list exists for this chat. Use /gen to add numbers.")
        return
    all_count = len(doc["numbers"])
    used_count = len([e for e in doc["numbers"] if e.get("used")])
    unused = all_count - used_count
    bot.reply_to(m, f"📄 Total: {all_count}\n✅ Unused: {unused}\n❌ Used: {used_count}")

# -----------------------
# Run bot
# -----------------------
if __name__ == "__main__":
    print("Bot started")
    bot.infinity_polling(timeout=60, long_polling_timeout=90)

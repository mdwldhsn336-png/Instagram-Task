import os
import json
import time
import random
import string
import io
import threading
import requests
import re
from datetime import datetime, timezone
from flask import Flask

import telebot
from telebot.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
from faker import Faker
import pyotp
import firebase_admin
from firebase_admin import credentials, firestore

# ==========================================
# 1. CONFIGURATION & SETUP
# ==========================================
BOT_TOKEN = os.environ.get("BOT_TOKEN", "8782856209:AAFyDqj1owGHut0ivuobBJxyg9j2PXpNrW4")
ADMIN_ID = int(os.environ.get("ADMIN_ID", "6670461311"))
TARGET_GROUP_ID = -1003963518943  # আপনার দেওয়া টেলিগ্রাম গ্রুপ আইডি

bot = telebot.TeleBot(BOT_TOKEN, parse_mode='HTML')
try:
    BOT_USERNAME = bot.get_me().username
except:
    BOT_USERNAME = "YourBotUsername"

fake = Faker()
user_sessions = {}

# --- Firebase Initialization ---
firebase_cred_json = os.environ.get("FIREBASE_CRED")
try:
    if firebase_cred_json:
        cred_dict = json.loads(firebase_cred_json)
        cred = credentials.Certificate(cred_dict)
    else:
        cred = credentials.Certificate("firebase_credentials.json")
        
    if not firebase_admin._apps:
        firebase_admin.initialize_app(cred)
    db = firestore.client()
    print("✅ Firebase connected successfully!")
except Exception as e:
    print(f"❌ Firebase Error: {e}")

# ==========================================
# 2. DATABASE & SETTINGS
# ==========================================
def init_settings():
    settings_ref = db.collection('settings').document('app_settings')
    if not settings_ref.get().exists:
        settings_ref.set({'task_rate': 5.00, 'ref_commission': 1.00, 'check_delay_minutes': 5, 'check_mode': 'auto'})

init_settings()

def get_settings():
    return db.collection('settings').document('app_settings').get().to_dict() or {}

def init_user(chat_id, referrer_id=None):
    user_ref = db.collection('users').document(str(chat_id))
    if not user_ref.get().exists:
        user_ref.set({
            'balance': 0.0, 'total_earned': 0.0, 'withdrawn': 0.0,
            'submitted': 0, 'approved': 0, 'rejected': 0,
            'referred_users': 0, 'referral_earnings': 0.0,
            'invited_by': str(referrer_id) if referrer_id else None,
            'banned': False, 'lang': 'bn'
        })
        if referrer_id and str(referrer_id) != str(chat_id):
            ref_ref = db.collection('users').document(str(referrer_id))
            if ref_ref.get().exists:
                ref_com = get_settings().get('ref_commission', 1.00)
                ref_ref.update({'referred_users': firestore.Increment(1), 'balance': firestore.Increment(ref_com), 'referral_earnings': firestore.Increment(ref_com)})

def check_ban(chat_id):
    user_doc = db.collection('users').document(str(chat_id)).get()
    return user_doc.exists and user_doc.to_dict().get('banned', False)

# ==========================================
# 3. SMART CHECKER LOGIC (API + WEB FALLBACK)
# ==========================================
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_2 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (Linux; Android 13; SM-S918B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36"
]

def check_ig_alive(username):
    # Method 1: IG Web API (সবচেয়ে নিখুঁত ভাবে 404 ফেক একাউন্ট ধরবে)
    api_url = f"https://www.instagram.com/api/v1/users/web_profile_info/?username={username}"
    headers_api = {
        "User-Agent": random.choice(USER_AGENTS),
        "X-IG-App-ID": "936619743392459",  # Instagram Official App ID
        "Accept": "*/*"
    }
    try:
        res = requests.get(api_url, headers=headers_api, timeout=10)
        if res.status_code == 404: return False  # ফেক একাউন্ট সাথে সাথে রিজেক্ট
        if res.status_code == 200: return True   # আসল একাউন্ট
    except:
        pass

    # Method 2: Web Scraping Fallback
    url = f"https://www.instagram.com/{username}/"
    headers_web = {
        "User-Agent": random.choice(USER_AGENTS), 
        "Accept-Language": "en-US,en;q=0.9",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
    }
    try:
        response = requests.get(url, headers=headers_web, timeout=15, allow_redirects=True)
        if response.status_code == 404: 
            return False
            
        if response.status_code == 200:
            html = response.text.lower()
            if "page not found" in html or "profile isn't available" in html or "sorry, this page isn't available" in html:
                return False 
            if 'property="og:description"' in html and ('followers' in html or 'following' in html):
                return True
            if "accounts/login" in response.url: 
                return None # আইপি ব্লক হলে ম্যানুয়াল
            return None
        return None
    except: 
        return None

# ==========================================
# 4. GROUP FORWARD FUNCTION
# ==========================================
def forward_to_group(data, doc_id):
    try:
        msg = f"✅ <b>Approved Account</b>\n\n" \
              f"🆔 Username: <code>{data.get('username')}</code>\n" \
              f"🔑 Password: <code>{data.get('password')}</code>"
              
        m = InlineKeyboardMarkup()
        m.add(InlineKeyboardButton("🔐 Get 2FA OTP", callback_data=f"grp:otp:{doc_id}"))
        
        bot.send_message(TARGET_GROUP_ID, msg, reply_markup=m)
    except Exception as e:
        print(f"Group Forward Error: {e}")

# ==========================================
# 5. KEYBOARDS & MENUS
# ==========================================
def main_menu(is_admin=False):
    markup = ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    markup.add(KeyboardButton("🚀 Start Task"))
    markup.add(KeyboardButton("👤 Profile"), KeyboardButton("👥 Referral"))
    markup.add(KeyboardButton("🏆 Top 10"), KeyboardButton("🌐 Language"))
    if is_admin: 
        markup.add(KeyboardButton("⚙️ Admin Panel"))
    return markup

# ==========================================
# 6. BACKGROUND AUTO-CHECKER THREAD
# ==========================================
def notify_admin_manual(data, doc_id):
    user_id = data.get('created_by')
    msg = f"⚠️ <b>ম্যানুয়াল রিভিউ প্রয়োজন!</b>\n\n" \
          f"👤 User ID: <code>{user_id}</code>\n" \
          f"🆔 Username: <code>{data.get('username')}</code>\n" \
          f"🔑 Pass: <code>{data.get('password')}</code>\n" \
          f"🔐 2FA: <code>{data.get('2fa_secret')}</code>"
    m = InlineKeyboardMarkup(row_width=2)
    m.add(InlineKeyboardButton("🔑 Get OTP", callback_data=f"man:otp:{doc_id}"))
    m.add(
        InlineKeyboardButton("✅ Approve", callback_data=f"man:app:{doc_id}:{user_id}"),
        InlineKeyboardButton("❌ Reject", callback_data=f"man:rej:{doc_id}:{user_id}")
    )
    try: bot.send_message(ADMIN_ID, msg, reply_markup=m)
    except: pass

def auto_checker_thread():
    while True:
        try:
            settings = get_settings()
            check_mode = settings.get('check_mode', 'auto')
            
            if check_mode == 'manual':
                time.sleep(60)
                continue

            delay = settings.get('check_delay_minutes', 5)
            now = datetime.now(timezone.utc)
            accounts = db.collection('instagram_accounts').where('status', '==', 'unchecked').stream()
            
            for acc in accounts:
                data = acc.to_dict()
                created_at = data.get('timestamp')
                if not created_at: continue
                
                if (now - created_at).total_seconds() / 60.0 >= delay:
                    status = check_ig_alive(data['username'])
                    user_id = data['created_by']
                    
                    if status is None:
                        db.collection('instagram_accounts').document(acc.id).update({'status': 'pending_manual'})
                        notify_admin_manual(data, acc.id)
                        try: bot.send_message(user_id, f"⏳ <code>{data['username']}</code> অটো-চেক সম্ভব হয়নি, ম্যানুয়াল রিভিউতে আছে।")
                        except: pass
                    elif status:
                        rate = settings.get('task_rate', 5.00)
                        db.collection('instagram_accounts').document(acc.id).update({'status': 'approved'})
                        db.collection('users').document(user_id).update({'balance': firestore.Increment(rate), 'total_earned': firestore.Increment(rate), 'approved': firestore.Increment(1)})
                        
                        forward_to_group(data, acc.id) # গ্রুপে সেন্ড করা
                        
                        try: bot.send_message(user_id, f"✅ <b>Report approved! +{rate} BDT</b>\n✉ Comment: Account <code>{data['username']}</code> is live.")
                        except: pass
                    else:
                        db.collection('instagram_accounts').document(acc.id).update({'status': 'rejected'})
                        db.collection('users').document(user_id).update({'rejected': firestore.Increment(1)})
                        try: bot.send_message(user_id, f"❌ <b>Report rejected.</b>\n✉ Comment: Account <code>{data['username']}</code> not found/suspended.")
                        except: pass
        except Exception as e: print(f"Checker Error: {e}")
        time.sleep(60)

# ==========================================
# 7. USER COMMANDS & TASK WORKFLOW
# ==========================================
@bot.message_handler(commands=['start'])
def welcome(message):
    uid = str(message.chat.id)
    if check_ban(uid): return bot.send_message(uid, "⛔ আপনার একাউন্ট ব্যান করা হয়েছে।")
    ref = message.text.split()[1] if len(message.text.split()) > 1 else None
    init_user(uid, ref)
    bot.send_message(uid, "স্বাগতম!", reply_markup=main_menu(message.chat.id == ADMIN_ID))

@bot.message_handler(func=lambda m: True)
def handle_all(message):
    uid, text = str(message.chat.id), message.text
    if check_ban(uid): return
    settings = get_settings()

    if text == "🚀 Start Task":
        m = ReplyKeyboardMarkup(resize_keyboard=True, row_width=1)
        m.add(KeyboardButton("🔐 Instagram 2FA"))
        m.add(KeyboardButton("❌ Cancel"))
        bot.send_message(uid, "পরবর্তী ধাপে যেতে ক্লিক করুন:", reply_markup=m)
    
    elif text == "👤 Profile":
        u = db.collection('users').document(uid).get().to_dict()
        msg = f"👤 <b>প্রোফাইল</b>\n\n📥 জমা দিয়েছেন: {u.get('submitted',0)}\n✅ অনুমোদিত: {u.get('approved',0)}\n❌ বাতিল: {u.get('rejected',0)}\n\n💵 প্রতি কাজ: {settings.get('task_rate', 0)} BDT\n💰 মোট আয়: {u.get('total_earned',0):.2f} BDT\n\n📤 উত্তোলন: {u.get('withdrawn',0):.2f} BDT\n\n💰 <b>ব্যালেন্স: {u.get('balance',0):.2f} BDT</b>"
        bot.send_message(uid, msg)

    elif text == "🏆 Top 10":
        users = db.collection('users').order_by('approved', direction=firestore.Query.DESCENDING).limit(10).stream()
        msg = "🏆 <b>টপ ১০ ইউজার</b>\n\n"
        for idx, u in enumerate(users, 1): msg += f"{idx}. ID: <code>{u.id}</code> - ✅ {u.to_dict().get('approved', 0)} কাজ\n"
        bot.send_message(uid, msg)

    elif text == "👥 Referral":
        u = db.collection('users').document(uid).get().to_dict()
        ref_link = f"https://t.me/{BOT_USERNAME}?start={uid}"
        msg = f"👥 <b>রেফারেল প্রোগ্রাম</b>\n\nপ্রতি রেফারে: {settings.get('ref_commission',0)} BDT\nমোট রেফার: {u.get('referred_users',0)} জন\nরেফার আয়: {u.get('referral_earnings',0):.2f} BDT\n\n🔗 <b>লিংক:</b>\n<code>{ref_link}</code>"
        bot.send_message(uid, msg)

    elif text == "🌐 Language":
        bot.send_message(uid, "🌐 ভাষা পরিবর্তনের কাজ চলছে।")

    elif text == "⚙️ Admin Panel" and message.chat.id == ADMIN_ID:
        m = InlineKeyboardMarkup(row_width=2)
        m.add(InlineKeyboardButton("🎛️ Checker Mode", callback_data="adm_mode"), InlineKeyboardButton("📝 Manual Review", callback_data="adm_review"))
        m.add(InlineKeyboardButton("📄 Users", callback_data="adm_users"), InlineKeyboardButton("🔍 Search", callback_data="adm_search"))
        m.add(InlineKeyboardButton("📊 Stats", callback_data="adm_stats"), InlineKeyboardButton("💰 Rates", callback_data="adm_rates"))
        m.add(InlineKeyboardButton("⏳ Timer", callback_data="adm_timer"), InlineKeyboardButton("📢 Notice", callback_data="adm_notice"))
        m.add(InlineKeyboardButton("📥 Download Report", callback_data="adm_ig"))
        bot.send_message(uid, "🛠️ <b>অ্যাডমিন ড্যাশবোর্ড</b>", reply_markup=m)

    elif text == "🔐 Instagram 2FA":
        m = ReplyKeyboardMarkup(resize_keyboard=True, row_width=1)
        m.add(KeyboardButton("▶️ Start"))
        m.add(KeyboardButton("❌ Cancel"))
        bot.send_message(uid, "📌 <b>রুলস:</b> ডিটেইলস কপি করে একাউন্ট খুলুন এবং 2FA সেট করুন।", reply_markup=m)

    elif text == "▶️ Start":
        first, last = fake.first_name(), fake.last_name()
        un = f"{first.lower()}_{last.lower()}{random.randint(1000,99999)}"[:18]
        pw = ''.join(random.choices(string.ascii_letters + string.digits + "@#$", k=12))
        user_sessions[message.chat.id] = {'name': f"{first} {last}", 'username': un, 'password': pw}
        
        msg_text = f"✅ <b>আপনার ডিটেইলস</b>:\n\n👤 Name: <code>{first} {last}</code>\n🆔 Username: <code>{un}</code>\n🔑 Password: <code>{pw}</code>\n\n👇 <b>একাউন্ট খোলার পর নিচে 2FA সিক্রেট কোডটি দিন:</b>"
        
        m = ReplyKeyboardMarkup(resize_keyboard=True, row_width=1)
        m.add(KeyboardButton("❌ Cancel"))
        bot_msg = bot.send_message(uid, msg_text, reply_markup=m)
        bot.register_next_step_handler(bot_msg, process_direct_2fa)

    elif text == "✅ Account Registered":
        data = user_sessions.get(message.chat.id)
        if data and '2fa_secret' in data:
            db.collection('instagram_accounts').document(data['username']).set({
                'created_by': uid, 'name': data['name'], 'username': data['username'],
                'password': data['password'], '2fa_secret': data['2fa_secret'], 'status': 'unchecked',
                'timestamp': datetime.now(timezone.utc)
            })
            db.collection('users').document(uid).update({'submitted': firestore.Increment(1)})
            mode = settings.get('check_mode', 'auto')
            delay_msg = f"{settings.get('check_delay_minutes')} মিনিট পর অটো রিপোর্ট আসবে।" if mode == 'auto' else "অ্যাডমিন ম্যানুয়ালি রিভিউ করে রিপোর্ট দেবেন।"
            bot.send_message(uid, f"🎉 একাউন্ট সেভ হয়েছে! {delay_msg}", reply_markup=main_menu(message.chat.id == ADMIN_ID))
            del user_sessions[message.chat.id]
        else:
            bot.send_message(uid, "⚠️ সেশন পাওয়া যায়নি।", reply_markup=main_menu(message.chat.id == ADMIN_ID))

    elif text == "❌ Cancel":
        if message.chat.id in user_sessions: del user_sessions[message.chat.id]
        bot.send_message(uid, "ক্যানসেল করা হয়েছে।", reply_markup=main_menu(message.chat.id == ADMIN_ID))

def process_direct_2fa(message):
    uid = message.chat.id
    text = message.text
    
    if text == "❌ Cancel":
        if uid in user_sessions: del user_sessions[uid]
        bot.send_message(uid, "ক্যানসেল করা হয়েছে।", reply_markup=main_menu(uid == ADMIN_ID))
        return

    sec = text.replace(" ", "")
    try:
        otp = pyotp.TOTP(sec).now()
        user_sessions.setdefault(uid, {})['2fa_secret'] = sec
        
        m = ReplyKeyboardMarkup(resize_keyboard=True, row_width=1)
        m.add(KeyboardButton("✅ Account Registered"))
        m.add(KeyboardButton("❌ Cancel"))
        
        bot.send_message(uid, f"✅ <b>OTP জেনারেট হয়েছে:</b>\n\n<code>{otp}</code>", reply_markup=m)
    except:
        m_kbd = ReplyKeyboardMarkup(resize_keyboard=True, row_width=1)
        m_kbd.add(KeyboardButton("❌ Cancel"))
        m_msg = bot.send_message(uid, "❌ Secret Code ভুল। আবার দিন:", reply_markup=m_kbd)
        bot.register_next_step_handler(m_msg, process_direct_2fa)

# ==========================================
# 8. ADMIN & GROUP CALLBACKS
# ==========================================
def get_next_manual_review():
    accounts = db.collection('instagram_accounts').where('status', 'in', ['unchecked', 'pending_manual']).limit(1).stream()
    for doc in accounts:
        data = doc.to_dict()
        msg = f"📝 <b>ম্যানুয়াল রিভিউ</b>\n\n" \
              f"🧾 Report ID: <code>{doc.id}</code>\n" \
              f"👤 User ID: <code>{data.get('created_by')}</code>\n" \
              f"🆔 Username: <code>{data.get('username')}</code>\n" \
              f"🔑 Password: <code>{data.get('password')}</code>"
        
        m = InlineKeyboardMarkup(row_width=2)
        m.add(InlineKeyboardButton("🔑 Get OTP", callback_data=f"rev:otp:{doc.id}"))
        m.add(
            InlineKeyboardButton("✅ Approve", callback_data=f"rev:app:{doc.id}:{data.get('created_by')}"),
            InlineKeyboardButton("❌ Reject", callback_data=f"rev:rej:{doc.id}:{data.get('created_by')}")
        )
        try: bot.send_message(ADMIN_ID, msg, reply_markup=m)
        except: pass
        return
    bot.send_message(ADMIN_ID, "🎉 বর্তমানে চেক করার মতো নতুন কোনো রিপোর্ট নেই!")

@bot.callback_query_handler(func=lambda call: call.data.startswith(("adm_", "usr:", "mode:", "rev:", "man:", "grp:")))
def all_callbacks(call):
    data = call.data

    # --- GROUP OTP BUTTON LOGIC ---
    if data.startswith("grp:otp:"):
        doc_id = data.split(':')[2]
        doc = db.collection('instagram_accounts').document(doc_id).get()
        if doc.exists:
            d = doc.to_dict()
            try:
                otp = pyotp.TOTP(d.get('2fa_secret')).now()
                # মেসেজটি এডিট করে OTP যোগ করে দেওয়া হলো
                new_msg = f"✅ <b>Approved Account</b>\n\n" \
                          f"🆔 Username: <code>{d.get('username')}</code>\n" \
                          f"🔑 Password: <code>{d.get('password')}</code>\n\n" \
                          f"🔐 <b>Latest OTP:</b> <code>{otp}</code>"
                m = InlineKeyboardMarkup()
                m.add(InlineKeyboardButton("🔄 Refresh OTP", callback_data=f"grp:otp:{doc_id}"))
                bot.edit_message_text(new_msg, chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=m, parse_mode="HTML")
            except:
                bot.answer_callback_query(call.id, "Error generating OTP", show_alert=True)
        return

    # From here, only admin is allowed
    if call.message.chat.id != ADMIN_ID: return

    if data == "adm_mode":
        m = InlineKeyboardMarkup(row_width=2)
        m.add(InlineKeyboardButton("🤖 Auto Check", callback_data="mode:auto"), InlineKeyboardButton("✋ Manual Check", callback_data="mode:manual"))
        bot.send_message(ADMIN_ID, "⚙️ <b>চেকার মোড সিলেক্ট করুন:</b>", reply_markup=m)
        return

    elif data.startswith("mode:"):
        mode = data.split(':')[1]
        db.collection('settings').document('app_settings').update({'check_mode': mode})
        bot.edit_message_text(f"✅ চেকার মোড <b>{mode.upper()}</b> এ সেট করা হয়েছে।", call.message.chat.id, call.message.message_id)
        return

    elif data == "adm_review":
        get_next_manual_review()
        return

    elif data.startswith("rev:") or data.startswith("man:"):
        parts = data.split(':')
        prefix = parts[0]
        action = parts[1]
        doc_id = parts[2]
        
        if action == "otp":
            doc = db.collection('instagram_accounts').document(doc_id).get()
            if doc.exists:
                d = doc.to_dict()
                try:
                    otp = pyotp.TOTP(d.get('2fa_secret')).now()
                    if prefix == "rev":
                        msg = f"📝 <b>ম্যানুয়াল রিভিউ</b>\n\n🧾 Report ID: <code>{doc.id}</code>\n👤 User ID: <code>{d.get('created_by')}</code>\n🆔 Username: <code>{d.get('username')}</code>\n🔑 Password: <code>{d.get('password')}</code>\n\n🔐 <b>OTP Code:</b> <code>{otp}</code>"
                    else:
                        msg = f"⚠️ <b>ম্যানুয়াল রিভিউ প্রয়োজন!</b>\n\n👤 User ID: <code>{d.get('created_by')}</code>\n🆔 Username: <code>{d.get('username')}</code>\n🔑 Pass: <code>{d.get('password')}</code>\n🔐 2FA: <code>{d.get('2fa_secret')}</code>\n\n🔐 <b>OTP Code:</b> <code>{otp}</code>"
                              
                    m = InlineKeyboardMarkup(row_width=2)
                    m.add(InlineKeyboardButton(f"⏳ {otp}", callback_data=f"{prefix}:otp:{doc_id}"))
                    user_id = d.get('created_by')
                    m.add(
                        InlineKeyboardButton("✅ Approve", callback_data=f"{prefix}:app:{doc_id}:{user_id}"),
                        InlineKeyboardButton("❌ Reject", callback_data=f"{prefix}:rej:{doc_id}:{user_id}")
                    )
                    bot.edit_message_text(msg, call.message.chat.id, call.message.message_id, reply_markup=m, parse_mode="HTML")
                except Exception as e:
                    bot.answer_callback_query(call.id, "Invalid 2FA", show_alert=True)
            return

        user_id = parts[3]
        rate = get_settings().get('task_rate', 5.00)

        if action == "app":
            doc = db.collection('instagram_accounts').document(doc_id).get()
            if doc.exists:
                d = doc.to_dict()
                db.collection('instagram_accounts').document(doc_id).update({'status': 'approved'})
                db.collection('users').document(user_id).update({'balance': firestore.Increment(rate), 'total_earned': firestore.Increment(rate), 'approved': firestore.Increment(1)})
                
                forward_to_group(d, doc_id) # ম্যানুয়ালি অ্যাপ্রুভ করলেও গ্রুপে যাবে
                
                try: bot.send_message(user_id, f"✅ <b>Report approved (Manual), +{rate} BDT</b>\n✉ Comment: Account <code>{doc_id}</code> is live.", parse_mode="HTML")
                except: pass
                bot.edit_message_text(f"✅ Approved: {doc_id}", call.message.chat.id, call.message.message_id)
                if prefix == "rev": get_next_manual_review() 

        elif action == "rej":
            db.collection('instagram_accounts').document(doc_id).update({'status': 'rejected'})
            db.collection('users').document(user_id).update({'rejected': firestore.Increment(1)})
            try: bot.send_message(user_id, f"❌ <b>Report rejected (Manual)</b>\n✉ Comment: Account <code>{doc_id}</code> suspended.", parse_mode="HTML")
            except: pass
            bot.edit_message_text(f"❌ Rejected: {doc_id}", call.message.chat.id, call.message.message_id)
            if prefix == "rev": get_next_manual_review() 
        return

    # --- Other Admin Panel Settings ---
    if data == "adm_users":
        content = "ID | Balance | Banned\n" + "-"*20 + "\n"
        for u in db.collection('users').stream(): content += f"{u.id} | {u.to_dict().get('balance',0)} | {u.to_dict().get('banned',False)}\n"
        bio = io.BytesIO(content.encode('utf-8')); bio.name = "users.txt"
        bot.send_document(ADMIN_ID, bio)

    elif data == "adm_ig":
        content = "Username,Password,2FA,Status,UserID\n"
        for a in db.collection('instagram_accounts').stream():
            d = a.to_dict()
            content += f"{d.get('username')},{d.get('password')},{d.get('2fa_secret')},{d.get('status')},{d.get('created_by')}\n"
        bio = io.BytesIO(content.encode('utf-8')); bio.name = "reports.csv"
        bot.send_document(ADMIN_ID, bio)

    elif data == "adm_stats":
        u_count = len(list(db.collection('users').stream()))
        ig_count = len(list(db.collection('instagram_accounts').stream()))
        bot.send_message(ADMIN_ID, f"📊 <b>পরিসংখ্যান</b>\n\nমোট ইউজার: {u_count}\nমোট কাজ: {ig_count}")

    elif data == "adm_rates":
        m = bot.send_message(ADMIN_ID, "Task Rate এবং Referral Commission স্পেস দিয়ে লিখুন (উদাঃ 4.5 1.5):")
        bot.register_next_step_handler(m, lambda msg: admin_updates(msg, 'rates'))

    elif data == "adm_timer":
        m = bot.send_message(ADMIN_ID, "অটো-চেকার ডিলে টাইম মিনিটে লিখুন (উদাঃ 5):")
        bot.register_next_step_handler(m, lambda msg: admin_updates(msg, 'timer'))

    elif data == "adm_notice":
        m = bot.send_message(ADMIN_ID, "নোটিশের টেক্সট লিখুন:")
        bot.register_next_step_handler(m, lambda msg: admin_updates(msg, 'notice'))

    elif data == "adm_search":
        m = bot.send_message(ADMIN_ID, "ইউজারের Telegram ID দিন:")
        bot.register_next_step_handler(m, search_user)

    elif data.startswith("usr:"):
        parts = data.split(':')
        action = parts[1]
        uid = parts[2]
        db.collection('users').document(uid).update({'banned': action == "ban"})
        bot.edit_message_text(f"✅ User {uid} is {action}ned.", call.message.chat.id, call.message.message_id)

def admin_updates(message, update_type):
    if update_type == 'rates':
        try:
            task, ref = map(float, message.text.split())
            db.collection('settings').document('app_settings').update({'task_rate': task, 'ref_commission': ref})
            bot.send_message(ADMIN_ID, f"✅ রেট আপডেট হয়েছে! Task: {task}, Ref: {ref}")
        except: bot.send_message(ADMIN_ID, "❌ ইনপুট ফরমেট ভুল।")
    elif update_type == 'timer':
        try:
            db.collection('settings').document('app_settings').update({'check_delay_minutes': int(message.text)})
            bot.send_message(ADMIN_ID, f"⏳ টাইমার {message.text} মিনিটে সেট করা হয়েছে।")
        except: bot.send_message(ADMIN_ID, "❌ শুধু সংখ্যা দিন।")
    elif update_type == 'notice':
        bot.send_message(ADMIN_ID, "⏳ নোটিশ পাঠানো হচ্ছে...")
        count = 0
        for u in db.collection('users').stream():
            try: bot.send_message(u.id, f"📢 <b>অ্যাডমিন নোটিশ:</b>\n\n{message.text}"); count += 1
            except: pass
        bot.send_message(ADMIN_ID, f"✅ {count} জনকে পাঠানো হয়েছে।")

def search_user(message):
    uid = message.text
    doc = db.collection('users').document(uid).get()
    if doc.exists:
        d = doc.to_dict()
        markup = InlineKeyboardMarkup()
        action = "unban" if d.get('banned') else "ban"
        markup.add(InlineKeyboardButton(f"🚫 {action.title()} User", callback_data=f"usr:{action}:{uid}"))
        bot.send_message(ADMIN_ID, f"👤 <b>Info ({uid})</b>\n\nBalance: {d.get('balance')}\nTasks: {d.get('submitted')}\nBanned: {d.get('banned')}", reply_markup=markup)
    else: bot.send_message(ADMIN_ID, "❌ ইউজার পাওয়া যায়নি।")

# ==========================================
# 9. FLASK SERVER & EXECUTION
# ==========================================
app = Flask(__name__)
@app.route('/')
def home(): return "🚀 Instagram Micro-Job Bot is Live!"

if __name__ == "__main__":
    threading.Thread(target=auto_checker_thread, daemon=True).start()
    threading.Thread(target=lambda: bot.infinity_polling(timeout=60, long_polling_timeout=60), daemon=True).start()
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 10000)))

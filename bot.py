#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import json
import time
import logging
import asyncio
from datetime import datetime
import telebot
from telebot import types
from telethon import TelegramClient
from threading import Thread

# ─── الإعدادات ──────────────────────────────────────────────────────────
TOKEN = "8734069991:AAHgDiwyeSzuGCMcEZ6UO6vcDK2SSraSDfA"  # ضع توكن البوت هنا
API_ID = 0          # ضع api_id هنا من my.telegram.org
API_HASH = ""       # ضع api_hash هنا من my.telegram.org
ALLOWED_USERS = []  # ضع معرف المستخدم المسموح له (رقم)

CONFIG_FILE = "config.json"
TARGETS_FILE = "targets.json"
DOWNLOADS_DIR = "downloads"

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# ─── إنشاء البوت ─────────────────────────────────────────────────────────
bot = telebot.TeleBot(TOKEN)

# ─── المتغيرات العالمية ──────────────────────────────────────────────────
user_client = None
targets = []
monitoring = False
known_stories = set()

# ─── تحميل وحفظ البيانات ────────────────────────────────────────────────
def load_config():
    global API_ID, API_HASH, ALLOWED_USERS
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "r") as f:
            cfg = json.load(f)
            API_ID = cfg.get("api_id", API_ID)
            API_HASH = cfg.get("api_hash", API_HASH)
            ALLOWED_USERS = cfg.get("allowed_users", ALLOWED_USERS)

def load_targets():
    global targets
    if os.path.exists(TARGETS_FILE):
        with open(TARGETS_FILE, "r") as f:
            targets = json.load(f)
    else:
        targets = []

def save_targets():
    with open(TARGETS_FILE, "w") as f:
        json.dump(targets, f, indent=4)

# ─── دوال التحقق من الصلاحية ─────────────────────────────────────────────
def is_allowed(message):
    return message.from_user.id in ALLOWED_USERS

def require_auth(func):
    def wrapper(message):
        if is_allowed(message):
            return func(message)
        else:
            bot.reply_to(message, "⛔ غير مصرح لك باستخدام هذا البوت.")
    return wrapper

# ─── الأوامر ──────────────────────────────────────────────────────────────
@bot.message_handler(commands=["start"])
@require_auth
def cmd_start(message):
    bot.reply_to(message, 
        "🤖 **بوت مشاهدة الستوريات**\n\n"
        "الأوامر:\n"
        "/targets - عرض الأهداف\n"
        "/add @username - إضافة هدف\n"
        "/remove @username - حذف هدف\n"
        "/monitor - بدء المراقبة\n"
        "/stop - إيقاف المراقبة\n"
        "/status - حالة البوت",
        parse_mode="Markdown"
    )

@bot.message_handler(commands=["targets"])
@require_auth
def cmd_targets(message):
    if not targets:
        bot.reply_to(message, "📭 لا توجد أهداف حالياً.")
        return
    msg = "🎯 **الأهداف:**\n"
    for i, t in enumerate(targets, 1):
        msg += f"{i}. {t}\n"
    bot.reply_to(message, msg, parse_mode="Markdown")

@bot.message_handler(commands=["add"])
@require_auth
def cmd_add(message):
    username = message.text.replace("/add", "").strip()
    if not username:
        bot.reply_to(message, "❗ استخدم: /add @username")
        return
    if not username.startswith("@"):
        username = f"@{username}"
    
    if username not in targets:
        targets.append(username)
        save_targets()
        bot.reply_to(message, f"✅ تم إضافة {username}")
    else:
        bot.reply_to(message, f"⚠️ {username} موجود بالفعل.")

@bot.message_handler(commands=["remove"])
@require_auth
def cmd_remove(message):
    username = message.text.replace("/remove", "").strip()
    if not username:
        bot.reply_to(message, "❗ استخدم: /remove @username")
        return
    if not username.startswith("@"):
        username = f"@{username}"
    
    if username in targets:
        targets.remove(username)
        save_targets()
        bot.reply_to(message, f"✅ تم حذف {username}")
    else:
        bot.reply_to(message, f"⚠️ {username} غير موجود.")

@bot.message_handler(commands=["monitor"])
@require_auth
def cmd_monitor(message):
    global monitoring
    if monitoring:
        bot.reply_to(message, "⚠️ المراقبة تعمل بالفعل.")
        return
    
    if not targets:
        bot.reply_to(message, "❗ لا توجد أهداف. أضف أهدافاً أولاً.")
        return
    
    monitoring = True
    bot.reply_to(message, "✅ **بدأت المراقبة!** سأرسل لك الستوريات هنا.")
    
    # تشغيل المراقبة في خيط منفصل
    thread = Thread(target=monitor_loop, args=(message.chat.id,), daemon=True)
    thread.start()

@bot.message_handler(commands=["stop"])
@require_auth
def cmd_stop(message):
    global monitoring
    monitoring = False
    bot.reply_to(message, "⏹️ **تم إيقاف المراقبة.**")

@bot.message_handler(commands=["status"])
@require_auth
def cmd_status(message):
    user_status = "🟢 متصل" if user_client and user_client.is_connected() else "🔴 غير متصل"
    bot.reply_to(message,
        f"**حالة البوت:**\n"
        f"• حساب المستخدم: {user_status}\n"
        f"• المراقبة: {'🟢 نشطة' if monitoring else '🔴 متوقفة'}\n"
        f"• الأهداف: {len(targets)}",
        parse_mode="Markdown"
    )

# ─── حلقة المراقبة (تعمل في خلفية منفصلة) ────────────────────────────────
def monitor_loop(chat_id):
    global monitoring, known_stories
    
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    
    while monitoring:
        for target in targets:
            try:
                # الحصول على الكيان
                entity = loop.run_until_complete(user_client.get_entity(target))
                
                # جلب الستوريات
                stories = loop.run_until_complete(user_client.get_stories(entity))
                
                if stories and stories.stories:
                    for story in stories.stories:
                        story_key = f"{entity.id}_{story.id}"
                        
                        if story_key in known_stories:
                            continue
                        
                        known_stories.add(story_key)
                        logger.info(f"📸 ستوري جديد من {target}")

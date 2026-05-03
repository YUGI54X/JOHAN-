"""
بوت تيليجرام احترافي لتحميل الفيديوهات من Facebook, TikTok, YouTube
- اختيار الجودة قبل التحميل
- اشتراك إجباري في القنوات
- 15 محاولة مجانية ثم 50 نجمة لـ 50 محاولة إضافية
- المالك معفي ويمكنه إعفاء أي مستخدم
"""

import os
import re
import json
import logging
import asyncio
import shutil
import tempfile
from pathlib import Path
from typing import Optional

import yt_dlp
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    LabeledPrice,
)
from telegram.constants import ChatAction, ParseMode
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    PreCheckoutQueryHandler,
    ContextTypes,
    filters,
)
from telegram.error import BadRequest, Forbidden

# ============================================================
# الإعدادات الأساسية
# ============================================================
BOT_TOKEN = os.environ.get("5432954282:AAFefAG3CE19kiA1po7Ha5muTudZ1oOrnQA")
OWNER_ID = 5868893864
DATA_FILE = Path("bot_data.json")
DOWNLOADS_DIR = Path("downloads")
DOWNLOADS_DIR.mkdir(exist_ok=True)

FREE_DOWNLOADS = 15
PAID_DOWNLOADS = 50
STARS_PRICE = 50

TELEGRAM_FILE_LIMIT = 50 * 1024 * 1024  # 50 ميجابايت

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ============================================================
# تخزين بسيط بصيغة JSON
# ============================================================
DEFAULT_CHANNELS = [
    {
        "title": "📢 قناة 1",
        "url": "https://t.me/rsll61",
        "verify_id": "@rsll61",
    },
    {
        "title": "🤖 بوت Hack696",
        "url": "https://t.me/Hack696bot",
        "verify_id": None,
    },
    {
        "title": "🎵 TikTok",
        "url": "https://www.tiktok.com/@sou.r31",
        "verify_id": None,
    },
]


def load_data() -> dict:
    if DATA_FILE.exists():
        try:
            data = json.loads(DATA_FILE.read_text(encoding="utf-8"))
            data.setdefault("users", {})
            data.setdefault("channels", [])
            data.setdefault("exempt", [])
            data.setdefault("verified", [])
            return data
        except Exception:
            pass
    return {
        "users": {},
        "channels": list(DEFAULT_CHANNELS),
        "exempt": [],
        "verified": [],
    }


def save_data(data: dict) -> None:
    DATA_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def get_user(data: dict, user_id: int) -> dict:
    uid = str(user_id)
    if uid not in data["users"]:
        data["users"][uid] = {"used": 0, "remaining": FREE_DOWNLOADS, "paid": False}
    return data["users"][uid]


def is_owner(user_id: int) -> bool:
    return user_id == OWNER_ID


def is_exempt(data: dict, user_id: int) -> bool:
    return is_owner(user_id) or user_id in data.get("exempt", [])


# ============================================================
# التحقق من الاشتراك الإجباري
# ============================================================
def _normalize_channel(ch) -> dict:
    """يدعم الصيغة القديمة (نص) والصيغة الجديدة (dict)."""
    if isinstance(ch, str):
        username = ch.lstrip("@")
        return {
            "title": f"📢 {ch}",
            "url": f"https://t.me/{username}",
            "verify_id": ch if ch.startswith("@") else f"@{username}",
        }
    return ch


async def check_subscription(
    context: ContextTypes.DEFAULT_TYPE, user_id: int, channels: list
) -> list:
    """يرجع قائمة بالقنوات التي لم يشترك فيها المستخدم
    (فقط القنوات التي يمكن التحقق منها تيليجرامياً)."""
    not_joined = []
    for raw in channels:
        ch = _normalize_channel(raw)
        vid = ch.get("verify_id")
        if not vid:
            # قناة خارجية (TikTok مثلاً) أو بوت - لا يمكن التحقق منها
            continue
        try:
            member = await context.bot.get_chat_member(vid, user_id)
            if member.status in ("left", "kicked"):
                not_joined.append(ch)
        except (BadRequest, Forbidden) as e:
            logger.warning(f"تعذر التحقق من القناة {vid}: {e}")
            continue
        except Exception as e:
            logger.warning(f"خطأ غير متوقع في {vid}: {e}")
            continue
    return not_joined


def _all_channel_buttons(channels: list) -> list:
    """يرجع كل القنوات (للعرض الكامل في رسالة الاشتراك الإجباري)."""
    rows = []
    for raw in channels:
        ch = _normalize_channel(raw)
        rows.append([InlineKeyboardButton(ch["title"], url=ch["url"])])
    return rows


def subscription_keyboard(channels: list) -> InlineKeyboardMarkup:
    buttons = _all_channel_buttons(channels)
    buttons.append(
        [InlineKeyboardButton("✅ تحقق من الاشتراك", callback_data="check_sub")]
    )
    return InlineKeyboardMarkup(buttons)


async def enforce_subscription(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> bool:
    """يتحقق من الاشتراك ويرسل رسالة إذا لم يكن المستخدم مشتركاً.
    يرجع True إذا كان مشتركاً (أو لا قنوات)، وإلا False."""
    data = context.application.bot_data["data"]
    user_id = update.effective_user.id

    if is_exempt(data, user_id):
        return True

    channels = data.get("channels", [])
    if not channels:
        return True

    not_joined = await check_subscription(context, user_id, channels)
    if not not_joined:
        return True

    text = (
        "🔒 <b>الاشتراك إجباري</b>\n\n"
        "للاستفادة من البوت، يرجى الاشتراك في القنوات التالية ثم الضغط على "
        "<b>«تحقق من الاشتراك»</b>:"
    )
    kb = subscription_keyboard(not_joined)
    target = update.effective_message
    if update.callback_query:
        try:
            await update.callback_query.edit_message_text(
                text, reply_markup=kb, parse_mode=ParseMode.HTML
            )
        except BadRequest:
            await target.reply_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)
    else:
        await target.reply_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)
    return False


# ============================================================
# واجهة المنصات
# ============================================================
def platforms_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("📘 Facebook", callback_data="plat:facebook"),
                InlineKeyboardButton("🎵 TikTok", callback_data="plat:tiktok"),
            ],
            [
                InlineKeyboardButton("▶️ YouTube", callback_data="plat:youtube"),
                InlineKeyboardButton("📷 Instagram", callback_data="plat:instagram"),
            ],
            [InlineKeyboardButton("👤 حسابي", callback_data="account")],
            [
                InlineKeyboardButton(
                    "✉️ تواصل مع المالك",
                    url=f"tg://user?id={OWNER_ID}",
                )
            ],
        ]
    )


WELCOME_TEXT = (
    "👋 <b>مرحباً بك!</b>\n\n"
    "أنا بوت مخصص لتحميل الفيديوهات من مواقع التواصل الاجتماعي.\n\n"
    "📥 <b>طريقة الاستخدام:</b>\n"
    "• أرسل رابط الفيديو مباشرة وسأقوم بتحميله.\n"
    "• أو اختر منصة من الأزرار بالأسفل واتبع التعليمات.\n\n"
    "🎁 لديك <b>{free}</b> محاولة تحميل مجانية."
)


# ============================================================
# الأوامر
# ============================================================
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await enforce_subscription(update, context):
        return
    await update.effective_message.reply_text(
        WELCOME_TEXT.format(free=FREE_DOWNLOADS),
        parse_mode=ParseMode.HTML,
        reply_markup=platforms_keyboard(),
    )


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = (
        "ℹ️ <b>المساعدة</b>\n\n"
        "• أرسل رابط الفيديو مباشرة (Facebook, TikTok, YouTube, Instagram).\n"
        "• اختر الجودة المطلوبة من القائمة.\n"
        "• <b>/account</b> لعرض رصيدك.\n"
        "• <b>/buy</b> لشراء 50 محاولة تحميل بـ 50 نجمة.\n"
    )
    if is_owner(update.effective_user.id):
        text += (
            "\n👑 <b>أوامر المالك:</b>\n"
            "• <code>/addchannel @channel</code> - إضافة قناة اشتراك إجباري\n"
            "• <code>/delchannel @channel</code> - حذف قناة\n"
            "• <code>/channels</code> - عرض القنوات\n"
            "• <code>/exempt &lt;user_id&gt;</code> - إعفاء مستخدم\n"
            "• <code>/unexempt &lt;user_id&gt;</code> - إلغاء إعفاء\n"
            "• <code>/exempts</code> - عرض المعفيين\n"
            "• <code>/grant &lt;user_id&gt; &lt;count&gt;</code> - منح محاولات\n"
            "• <code>/stats</code> - إحصائيات\n"
        )
    await update.effective_message.reply_text(text, parse_mode=ParseMode.HTML)


async def account_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await enforce_subscription(update, context):
        return
    data = context.application.bot_data["data"]
    user = get_user(data, update.effective_user.id)
    save_data(data)

    if is_exempt(data, update.effective_user.id):
        text = "👑 <b>حسابك:</b>\n\n♾️ تحميل غير محدود (معفي)"
    else:
        text = (
            f"👤 <b>حسابك:</b>\n\n"
            f"📥 المتبقي: <b>{user['remaining']}</b> محاولة\n"
            f"📊 المستخدم: <b>{user['used']}</b>\n\n"
            f"عند نفاد المحاولات يمكنك شراء <b>{PAID_DOWNLOADS}</b> "
            f"محاولة بـ <b>{STARS_PRICE} ⭐</b> عبر /buy"
        )
    await update.effective_message.reply_text(text, parse_mode=ParseMode.HTML)


async def buy_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await enforce_subscription(update, context):
        return
    if is_exempt(context.application.bot_data["data"], update.effective_user.id):
        await update.effective_message.reply_text("✨ أنت معفي ولا تحتاج للشراء.")
        return

    await context.bot.send_invoice(
        chat_id=update.effective_chat.id,
        title=f"{PAID_DOWNLOADS} محاولة تحميل",
        description=f"احصل على {PAID_DOWNLOADS} محاولة تحميل إضافية للفيديوهات.",
        payload=f"buy_{PAID_DOWNLOADS}_downloads",
        provider_token="",  # فارغ لأن النجوم XTR
        currency="XTR",
        prices=[LabeledPrice(label=f"{PAID_DOWNLOADS} تحميل", amount=STARS_PRICE)],
    )


# ============================================================
# أوامر المالك
# ============================================================
def owner_only(func):
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not is_owner(update.effective_user.id):
            await update.effective_message.reply_text("❌ هذا الأمر للمالك فقط.")
            return
        return await func(update, context)
    return wrapper


@owner_only
async def addchannel_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.effective_message.reply_text("الاستخدام: /addchannel @channel")
        return
    ch = context.args[0]
    if not ch.startswith("@"):
        ch = "@" + ch
    data = context.application.bot_data["data"]
    if ch not in data["channels"]:
        data["channels"].append(ch)
        save_data(data)
    await update.effective_message.reply_text(f"✅ تمت إضافة {ch}")


@owner_only
async def delchannel_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.effective_message.reply_text("الاستخدام: /delchannel @channel")
        return
    ch = context.args[0]
    if not ch.startswith("@"):
        ch = "@" + ch
    data = context.application.bot_data["data"]
    if ch in data["channels"]:
        data["channels"].remove(ch)
        save_data(data)
        await update.effective_message.reply_text(f"🗑️ تم حذف {ch}")
    else:
        await update.effective_message.reply_text("القناة غير موجودة.")


@owner_only
async def channels_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    data = context.application.bot_data["data"]
    if not data["channels"]:
        await update.effective_message.reply_text("لا توجد قنوات اشتراك إجباري.")
        return
    text = "📢 <b>قنوات الاشتراك:</b>\n" + "\n".join(
        f"• {c}" for c in data["channels"]
    )
    await update.effective_message.reply_text(text, parse_mode=ParseMode.HTML)


@owner_only
async def exempt_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args or not context.args[0].lstrip("-").isdigit():
        await update.effective_message.reply_text("الاستخدام: /exempt <user_id>")
        return
    uid = int(context.args[0])
    data = context.application.bot_data["data"]
    if uid not in data["exempt"]:
        data["exempt"].append(uid)
        save_data(data)
    await update.effective_message.reply_text(f"✨ تم إعفاء المستخدم {uid}")


@owner_only
async def unexempt_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args or not context.args[0].lstrip("-").isdigit():
        await update.effective_message.reply_text("الاستخدام: /unexempt <user_id>")
        return
    uid = int(context.args[0])
    data = context.application.bot_data["data"]
    if uid in data["exempt"]:
        data["exempt"].remove(uid)
        save_data(data)
        await update.effective_message.reply_text(f"🔓 تم إلغاء إعفاء {uid}")
    else:
        await update.effective_message.reply_text("المستخدم غير معفي.")


@owner_only
async def exempts_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    data = context.application.bot_data["data"]
    if not data["exempt"]:
        await update.effective_message.reply_text("لا يوجد مستخدمون معفيون.")
        return
    text = "✨ <b>المعفيون:</b>\n" + "\n".join(
        f"• <code>{u}</code>" for u in data["exempt"]
    )
    await update.effective_message.reply_text(text, parse_mode=ParseMode.HTML)


@owner_only
async def grant_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if (
        len(context.args) != 2
        or not context.args[0].lstrip("-").isdigit()
        or not context.args[1].isdigit()
    ):
        await update.effective_message.reply_text("الاستخدام: /grant <user_id> <count>")
        return
    uid = int(context.args[0])
    count = int(context.args[1])
    data = context.application.bot_data["data"]
    user = get_user(data, uid)
    user["remaining"] += count
    save_data(data)
    await update.effective_message.reply_text(
        f"✅ تم منح {count} محاولة للمستخدم {uid}. الرصيد الآن: {user['remaining']}"
    )


@owner_only
async def stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    data = context.application.bot_data["data"]
    users = data["users"]
    total_users = len(users)
    total_downloads = sum(u.get("used", 0) for u in users.values())
    paid_users = sum(1 for u in users.values() if u.get("paid"))
    text = (
        f"📊 <b>إحصائيات البوت</b>\n\n"
        f"👥 المستخدمون: <b>{total_users}</b>\n"
        f"📥 إجمالي التحميلات: <b>{total_downloads}</b>\n"
        f"💎 المشتركون المدفوعون: <b>{paid_users}</b>\n"
        f"📢 قنوات الاشتراك: <b>{len(data['channels'])}</b>\n"
        f"✨ المعفيون: <b>{len(data['exempt'])}</b>\n"
    )
    await update.effective_message.reply_text(text, parse_mode=ParseMode.HTML)


# ============================================================
# معالجة الأزرار
# ============================================================
PLATFORM_INFO = {
    "facebook": ("📘 Facebook", "facebook.com / fb.watch"),
    "tiktok": ("🎵 TikTok", "tiktok.com / vm.tiktok.com"),
    "youtube": ("▶️ YouTube", "youtube.com / youtu.be"),
    "instagram": ("📷 Instagram", "instagram.com"),
}


async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    data_cb = query.data

    if data_cb == "check_sub":
        data = context.application.bot_data["data"]
        not_joined = await check_subscription(
            context, query.from_user.id, data.get("channels", [])
        )
        if not_joined:
            await query.answer("لم تشترك في كل القنوات بعد!", show_alert=True)
            try:
                await query.edit_message_reply_markup(
                    reply_markup=subscription_keyboard(not_joined)
                )
            except BadRequest:
                pass
            return
        await query.edit_message_text(
            WELCOME_TEXT.format(free=FREE_DOWNLOADS),
            parse_mode=ParseMode.HTML,
            reply_markup=platforms_keyboard(),
        )
        return

    if not await enforce_subscription(update, context):
        return

    if data_cb.startswith("plat:"):
        platform = data_cb.split(":", 1)[1]
        name, domains = PLATFORM_INFO.get(platform, (platform, ""))
        await query.edit_message_text(
            f"{name}\n\n📩 أرسل رابط الفيديو من <code>{domains}</code> "
            "وسأقوم بتحميله لك مع خيارات الجودة.",
            parse_mode=ParseMode.HTML,
        )
        return

    if data_cb == "account":
        await account_cmd(update, context)
        return

    if data_cb.startswith("dl:"):
        # dl:<token>:<format_id>
        _, token, fmt = data_cb.split(":", 2)
        await handle_download(update, context, token, fmt)
        return


# ============================================================
# الكشف عن الروابط واستخراج الجودات
# ============================================================
URL_REGEX = re.compile(
    r"https?://(?:www\.|m\.|vm\.|vt\.)?"
    r"(facebook\.com|fb\.watch|fb\.com|tiktok\.com|youtube\.com|youtu\.be|instagram\.com)"
    r"/[^\s]+",
    re.IGNORECASE,
)


def extract_url(text: str) -> Optional[str]:
    m = URL_REGEX.search(text or "")
    return m.group(0) if m else None


def extract_formats(info: dict) -> list:
    """يرجع قائمة بالجودات المتاحة (تحتوي فيديو + صوت)."""
    formats = info.get("formats") or []
    seen = {}
    for f in formats:
        if f.get("vcodec") == "none":
            continue
        height = f.get("height")
        ext = f.get("ext", "mp4")
        fid = f.get("format_id")
        if not fid or not height:
            continue
        # نفضّل الصيغ التي تحتوي صوت + فيديو، وإلا نستخدم bv*+ba للدمج
        has_audio = f.get("acodec") and f.get("acodec") != "none"
        size = f.get("filesize") or f.get("filesize_approx") or 0
        key = height
        if key not in seen or has_audio:
            seen[key] = {
                "format_id": fid,
                "height": height,
                "ext": ext,
                "has_audio": has_audio,
                "size": size,
            }
    items = sorted(seen.values(), key=lambda x: x["height"], reverse=True)
    return items[:6]


def human_size(n: int) -> str:
    if not n:
        return ""
    for unit in ["B", "KB", "MB", "GB"]:
        if n < 1024:
            return f"{n:.1f}{unit}"
        n /= 1024
    return f"{n:.1f}TB"


async def url_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await enforce_subscription(update, context):
        return

    text = update.effective_message.text or ""
    url = extract_url(text)
    if not url:
        await update.effective_message.reply_text(
            "❗ لم أتعرف على رابط صالح. أرسل رابط من Facebook أو TikTok أو YouTube أو Instagram."
        )
        return

    data = context.application.bot_data["data"]
    user_id = update.effective_user.id
    user = get_user(data, user_id)

    if not is_exempt(data, user_id) and user["remaining"] <= 0:
        kb = InlineKeyboardMarkup(
            [[InlineKeyboardButton(f"💎 شراء {PAID_DOWNLOADS} محاولة بـ {STARS_PRICE} ⭐", callback_data="buy")]]
        )
        await update.effective_message.reply_text(
            f"⛔ انتهت محاولاتك المجانية.\n\n"
            f"يمكنك شراء <b>{PAID_DOWNLOADS}</b> محاولة إضافية بـ "
            f"<b>{STARS_PRICE} ⭐</b> عبر الأمر /buy",
            parse_mode=ParseMode.HTML,
        )
        return

    msg = await update.effective_message.reply_text("🔎 جارٍ فحص الرابط...")

    try:
        info = await asyncio.to_thread(_extract_info, url)
    except Exception as e:
        logger.exception("فشل استخراج المعلومات")
        await msg.edit_text(f"❌ تعذر قراءة الرابط:\n<code>{e}</code>", parse_mode=ParseMode.HTML)
        return

    formats = extract_formats(info)
    if not formats:
        # لا توجد جودات منفصلة - نقدم خيار افتراضي واحد
        formats = [{"format_id": "best", "height": 0, "ext": "mp4", "size": 0, "has_audio": True}]

    # نخزن الرابط مؤقتاً
    token = f"t{update.effective_message.message_id}_{user_id}"
    context.application.bot_data.setdefault("pending", {})[token] = url

    title = (info.get("title") or "فيديو")[:80]
    buttons = []
    for f in formats:
        label = f"{f['height']}p" if f["height"] else "أفضل جودة"
        if f.get("size"):
            label += f" • {human_size(f['size'])}"
        if not f["has_audio"]:
            label += " 🎵"
        buttons.append(
            [InlineKeyboardButton(label, callback_data=f"dl:{token}:{f['format_id']}")]
        )

    await msg.edit_text(
        f"🎬 <b>{title}</b>\n\n📥 اختر الجودة:",
        reply_markup=InlineKeyboardMarkup(buttons),
        parse_mode=ParseMode.HTML,
    )


def _extract_info(url: str) -> dict:
    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "noplaylist": True,
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        return ydl.extract_info(url, download=False)


def _download_video(url: str, format_id: str, out_dir: Path) -> Path:
    if format_id == "best":
        fmt_selector = "bestvideo*+bestaudio/best"
    else:
        # دمج التنسيق المختار مع أفضل صوت متاح في حال لم يكن يحتوي صوت
        fmt_selector = f"{format_id}+bestaudio/{format_id}/best"

    ydl_opts = {
        "format": fmt_selector,
        "outtmpl": str(out_dir / "%(id)s.%(ext)s"),
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "merge_output_format": "mp4",
        "retries": 3,
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
        filename = ydl.prepare_filename(info)
        # بعد الدمج قد يصبح الامتداد mp4
        p = Path(filename)
        if not p.exists():
            for ext in ("mp4", "mkv", "webm"):
                alt = p.with_suffix(f".{ext}")
                if alt.exists():
                    return alt
        return p


async def handle_download(
    update: Update, context: ContextTypes.DEFAULT_TYPE, token: str, fmt: str
) -> None:
    query = update.callback_query
    pending = context.application.bot_data.get("pending", {})
    url = pending.get(token)
    if not url:
        await query.edit_message_text("❌ انتهت صلاحية الطلب، أرسل الرابط مرة أخرى.")
        return

    data = context.application.bot_data["data"]
    user_id = query.from_user.id
    user = get_user(data, user_id)

    if not is_exempt(data, user_id) and user["remaining"] <= 0:
        await query.edit_message_text("⛔ انتهت محاولاتك. استخدم /buy لشراء المزيد.")
        return

    await query.edit_message_text("⏳ جارٍ التحميل، يرجى الانتظار...")
    await context.bot.send_chat_action(query.message.chat_id, ChatAction.UPLOAD_VIDEO)

    tmp_dir = Path(tempfile.mkdtemp(prefix="dl_", dir=DOWNLOADS_DIR))
    try:
        try:
            file_path = await asyncio.to_thread(_download_video, url, fmt, tmp_dir)
        except Exception as e:
            logger.exception("فشل التحميل")
            await query.edit_message_text(
                f"❌ فشل التحميل:\n<code>{str(e)[:300]}</code>",
                parse_mode=ParseMode.HTML,
            )
            return

        if not file_path.exists():
            await query.edit_message_text("❌ لم يتم إنشاء الملف.")
            return

        size = file_path.stat().st_size
        await query.edit_message_text(
            f"📤 جارٍ رفع الفيديو ({human_size(size)})..."
        )

        try:
            with open(file_path, "rb") as fh:
                await context.bot.send_video(
                    chat_id=query.message.chat_id,
                    video=fh,
                    caption="✅ تم التحميل بنجاح\n@" + (context.bot.username or ""),
                    supports_streaming=True,
                    read_timeout=300,
                    write_timeout=300,
                )
        except Exception as e:
            # إذا كان الملف كبيراً جداً نحاول إرساله كمستند
            logger.warning(f"فشل إرسال كفيديو، نحاول كمستند: {e}")
            try:
                with open(file_path, "rb") as fh:
                    await context.bot.send_document(
                        chat_id=query.message.chat_id,
                        document=fh,
                        caption="✅ تم التحميل (كملف)",
                        read_timeout=600,
                        write_timeout=600,
                    )
            except Exception as e2:
                await context.bot.send_message(
                    query.message.chat_id,
                    f"❌ فشل رفع الملف (الحجم {human_size(size)}):\n<code>{e2}</code>",
                    parse_mode=ParseMode.HTML,
                )
                return

        # حذف الرسالة المؤقتة
        try:
            await query.message.delete()
        except Exception:
            pass

        # خصم محاولة فقط للمستخدمين العاديين
        if not is_exempt(data, user_id):
            user["used"] += 1
            user["remaining"] -= 1
            save_data(data)
            if user["remaining"] in (5, 1, 0):
                await context.bot.send_message(
                    query.message.chat_id,
                    f"ℹ️ تبقى لديك <b>{user['remaining']}</b> محاولة. "
                    f"للشراء استخدم /buy",
                    parse_mode=ParseMode.HTML,
                )
        else:
            data["users"].setdefault(str(user_id), user)
            user["used"] += 1
            save_data(data)

    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        pending.pop(token, None)


# ============================================================
# الدفع بالنجوم
# ============================================================
async def precheckout_handler(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    query = update.pre_checkout_query
    await query.answer(ok=True)


async def successful_payment_handler(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    payment = update.effective_message.successful_payment
    data = context.application.bot_data["data"]
    user = get_user(data, update.effective_user.id)
    user["remaining"] += PAID_DOWNLOADS
    user["paid"] = True
    save_data(data)
    await update.effective_message.reply_text(
        f"✅ تم إضافة <b>{PAID_DOWNLOADS}</b> محاولة لرصيدك!\n"
        f"الرصيد الآن: <b>{user['remaining']}</b> محاولة\n"
        f"شكراً لدعمك ⭐",
        parse_mode=ParseMode.HTML,
    )
    logger.info(
        f"دفع ناجح: user={update.effective_user.id} "
        f"amount={payment.total_amount} {payment.currency}"
    )


# ============================================================
# نقطة التشغيل
# ============================================================
def main() -> None:
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN غير محدد في المتغيرات البيئية")

    app = Application.builder().token(BOT_TOKEN).build()
    app.bot_data["data"] = load_data()

    # الأوامر العامة
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("account", account_cmd))
    app.add_handler(CommandHandler("buy", buy_cmd))

    # أوامر المالك
    app.add_handler(CommandHandler("addchannel", addchannel_cmd))
    app.add_handler(CommandHandler("delchannel", delchannel_cmd))
    app.add_handler(CommandHandler("channels", channels_cmd))
    app.add_handler(CommandHandler("exempt", exempt_cmd))
    app.add_handler(CommandHandler("unexempt", unexempt_cmd))
    app.add_handler(CommandHandler("exempts", exempts_cmd))
    app.add_handler(CommandHandler("grant", grant_cmd))
    app.add_handler(CommandHandler("stats", stats_cmd))

    # الأزرار والروابط
    app.add_handler(CallbackQueryHandler(callback_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, url_handler))

    # الدفع
    app.add_handler(PreCheckoutQueryHandler(precheckout_handler))
    app.add_handler(
        MessageHandler(filters.SUCCESSFUL_PAYMENT, successful_payment_handler)
    )

    logger.info("✅ البوت يعمل الآن...")
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)


if __name__ == "__main__":
    main()

import os
import sqlite3
import logging
import tempfile
import asyncio
import json
import base64
import urllib.request
import urllib.error
from datetime import datetime, timezone, date
from urllib.parse import urlencode
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, LabeledPrice, BotCommand, MenuButtonCommands, InputFile
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, MessageHandler,
    ContextTypes, PreCheckoutQueryHandler, filters
)

load_dotenv()
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("BOT_TOKEN", "")
BOT_USERNAME = os.getenv("BOT_USERNAME", "RajasthanUniversityNotes_bot").lstrip("@")
UPI_ID = os.getenv("UPI_ID", "").strip()
BUSINESS_NAME = os.getenv("BUSINESS_NAME", "RU Notes Store").strip()
PAYMENT_PAGE_URL = os.getenv("PAYMENT_PAGE_URL", "https://keshavmadhav889-maker.github.io/rajasthan-university-notes-bot/payment.html").strip()
CHANNEL_USERNAME = os.getenv("CHANNEL_USERNAME", "")
ADMIN_CHAT_ID = int(os.getenv("ADMIN_CHAT_ID", "0"))
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "")
DB_PATH = os.getenv("DATABASE_PATH", "bot.db")
BUNDLE_DISCOUNT_PERCENT = max(0, min(90, int(os.getenv("BUNDLE_DISCOUNT_PERCENT", "20"))))
DEMO_PAGES = 4
RAZORPAY_KEY_ID = os.getenv("RAZORPAY_KEY_ID", "")
RAZORPAY_KEY_SECRET = os.getenv("RAZORPAY_KEY_SECRET", "")

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("ru-notes-bot")

COURSES = {"BSC":"🎓 B.Sc.","BCOM":"📗 B.Com","MCOM":"📙 M.Com","MSC":"🔬 M.Sc","MA":"📕 M.A","BA":"📘 B.A"}
STREAMS = {"PCM":"PCM","PCB":"PCB"}

def now():
    return datetime.now(timezone.utc).isoformat()

# Official-syllabus-aligned discipline/catalog starters. Paper titles remain editable in Admin.
CATALOG = {
    "BSC": {
        "PCM": ["Physics", "Chemistry", "Mathematics"],
        "PCB": ["Physics", "Chemistry", "Botany", "Zoology"]
    },
    "BCOM": {"": ["Accountancy and Business Statistics", "Business Administration", "Economic Administration and Financial Management", "Garment Production and Export Management"]},
    "MCOM": {"": ["Accountancy and Business Statistics", "Business Administration", "Cost and Management Accounting", "Economic Administration and Financial Management", "Human Resource Management"]},
    "MSC": {"": ["Physics", "Chemistry", "Mathematics", "Botany", "Zoology", "Geology", "Microbiology", "Environmental Science"]},
    "MA": {"": ["English", "Hindi", "History", "Economics", "Philosophy", "Sociology", "Political Science", "Public Administration", "Sanskrit", "Urdu", "Journalism and Mass Communication"]},
    "BA": {"": ["Hindi", "English", "History", "Political Science", "Economics", "Geography", "Sociology", "Psychology", "Philosophy", "Sanskrit", "Urdu", "Public Administration"]}
}

def seed_catalog(c):
    for course, streams in CATALOG.items():
        for stream, subjects_list in streams.items():
            for sem in range(1, 7):
                for subject in subjects_list:
                    c.execute("INSERT OR IGNORE INTO subject_catalog(course,stream,semester,subject) VALUES(?,?,?,?)", (course,stream,sem,subject))

def db():
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    c.execute("""CREATE TABLE IF NOT EXISTS users(
        telegram_id INTEGER PRIMARY KEY, username TEXT, first_name TEXT, created_at TEXT NOT NULL)""")
    c.execute("""CREATE TABLE IF NOT EXISTS products(
        id INTEGER PRIMARY KEY AUTOINCREMENT, course TEXT NOT NULL, stream TEXT DEFAULT '',
        semester INTEGER NOT NULL, subject TEXT NOT NULL, price INTEGER NOT NULL DEFAULT 0,
        file_id TEXT DEFAULT '', preview_file_id TEXT DEFAULT '', active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL,
        UNIQUE(course,stream,semester,subject))""")
    cols = [r["name"] for r in c.execute("PRAGMA table_info(products)").fetchall()]
    if "preview_file_id" not in cols:
        c.execute("ALTER TABLE products ADD COLUMN preview_file_id TEXT DEFAULT ''")
    c.execute("""CREATE TABLE IF NOT EXISTS orders(
        id INTEGER PRIMARY KEY AUTOINCREMENT, telegram_id INTEGER NOT NULL, product_id INTEGER NOT NULL,
        payload TEXT NOT NULL, price INTEGER NOT NULL, status TEXT NOT NULL,
        telegram_charge_id TEXT DEFAULT '', payment_link_id TEXT DEFAULT '', razorpay_payment_id TEXT DEFAULT '',
        created_at TEXT NOT NULL, delivered INTEGER NOT NULL DEFAULT 0)""")
    c.execute("""CREATE TABLE IF NOT EXISTS subject_catalog(
        id INTEGER PRIMARY KEY AUTOINCREMENT, course TEXT NOT NULL, stream TEXT DEFAULT '',
        semester INTEGER NOT NULL, subject TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1,
        UNIQUE(course,stream,semester,subject))""")
    c.execute("""CREATE TABLE IF NOT EXISTS bundle_orders(
        id INTEGER PRIMARY KEY AUTOINCREMENT, telegram_id INTEGER NOT NULL,
        course TEXT NOT NULL, stream TEXT DEFAULT '', semester INTEGER NOT NULL,
        payload TEXT NOT NULL, price INTEGER NOT NULL, original_price INTEGER NOT NULL,
        discount_percent INTEGER NOT NULL, status TEXT NOT NULL,
        telegram_charge_id TEXT DEFAULT '', payment_link_id TEXT DEFAULT '', razorpay_payment_id TEXT DEFAULT '',
        created_at TEXT NOT NULL, delivered INTEGER NOT NULL DEFAULT 0)""")
    order_cols=[r["name"] for r in c.execute("PRAGMA table_info(orders)").fetchall()]
    for col in ("payment_link_id","razorpay_payment_id"):
        if col not in order_cols:
            c.execute(f"ALTER TABLE orders ADD COLUMN {col} TEXT DEFAULT ''")
    for col, definition in (
        ("approval_notified", "INTEGER NOT NULL DEFAULT 0"),
        ("approved_at", "TEXT DEFAULT ''"),
        ("delivered_at", "TEXT DEFAULT ''"),
    ):
        if col not in order_cols:
            c.execute(f"ALTER TABLE orders ADD COLUMN {col} {definition}")
    bundle_cols=[r["name"] for r in c.execute("PRAGMA table_info(bundle_orders)").fetchall()]
    for col in ("payment_link_id","razorpay_payment_id"):
        if col not in bundle_cols:
            c.execute(f"ALTER TABLE bundle_orders ADD COLUMN {col} TEXT DEFAULT ''")
    for col, definition in (
        ("approval_notified", "INTEGER NOT NULL DEFAULT 0"),
        ("approved_at", "TEXT DEFAULT ''"),
        ("delivered_at", "TEXT DEFAULT ''"),
    ):
        if col not in bundle_cols:
            c.execute(f"ALTER TABLE bundle_orders ADD COLUMN {col} {definition}")
    seed_catalog(c)
    c.commit()
    return c

def razorpay_enabled():
    return bool(RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET)


def razorpay_api(method, path, payload=None):
    token = base64.b64encode(f"{RAZORPAY_KEY_ID}:{RAZORPAY_KEY_SECRET}".encode()).decode()
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        f"https://api.razorpay.com{path}", data=data,
        headers={"Authorization": f"Basic {token}", "Content-Type": "application/json",
                 "User-Agent": "RU-Notes-Store/1.0"}, method=method)
    try:
        with urllib.request.urlopen(req, timeout=25) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Razorpay API error {e.code}: {e.read().decode(errors='replace')[:500]}")


async def create_payment_link(ref_id, user_id, amount, description):
    if not razorpay_enabled():
        raise RuntimeError("UPI gateway not configured")
    payload={"amount":int(amount)*100,"currency":"INR","accept_partial":False,
             "reference_id":f"RU{ref_id}{user_id}"[:40],"description":description[:255],
             "reminder_enable":False,
             "notes":{"order_id":str(ref_id),"telegram_user_id":str(user_id)}}
    return await asyncio.to_thread(razorpay_api,"POST","/v1/payment_links",payload)


async def fetch_payment_link(link_id):
    return await asyncio.to_thread(razorpay_api,"GET",f"/v1/payment_links/{link_id}")


def payment_buttons(order_id, short_url, bundle=False):
    check=f"bundlecheck:{order_id}" if bundle else f"paycheck:{order_id}"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💳 UPI / Card से Pay करें",url=short_url)],
        [InlineKeyboardButton("✅ मैंने Payment कर दी — Check करें",callback_data=check)],
        [InlineKeyboardButton("❌ Cancel",callback_data="home")]
    ])


async def poll_razorpay_orders(context):
    """Automatically verify paid Razorpay links and deliver purchased PDFs."""
    if not razorpay_enabled():
        return
    try:
        with db() as c:
            orders = c.execute(
                """SELECT o.id,o.telegram_id,o.price,o.payment_link_id,p.file_id,p.subject
                   FROM orders o JOIN products p ON p.id=o.product_id
                   WHERE o.status='PENDING' AND o.payment_link_id<>'' AND p.active=1
                   ORDER BY o.id ASC LIMIT 25"""
            ).fetchall()
            bundles = c.execute(
                """SELECT * FROM bundle_orders
                   WHERE status='PENDING' AND payment_link_id<>''
                   ORDER BY id ASC LIMIT 10"""
            ).fetchall()

        for o in orders:
            try:
                data = await fetch_payment_link(o["payment_link_id"])
                paid = int(data.get("amount_paid", 0) or 0)
                if str(data.get("status", "")).lower() == "paid" and paid >= int(o["price"]) * 100:
                    with db() as c:
                        cur = c.execute(
                            """UPDATE orders SET status='PAID',razorpay_payment_id=?
                               WHERE id=? AND status='PENDING'""",
                            (str(data.get("id", "")), o["id"])
                        )
                        changed = cur.rowcount
                    if changed and o["file_id"]:
                        await context.bot.send_document(
                            chat_id=o["telegram_id"],
                            document=o["file_id"],
                            caption=f"✅ Payment Verified\n📚 {o['subject']}\n💰 ₹{o['price']}\n📥 आपकी Notes PDF"
                        )
                        with db() as c:
                            c.execute("UPDATE orders SET delivered=1 WHERE id=? AND delivered=0", (o["id"],))
            except Exception:
                log.exception("Razorpay order polling failed for order %s", o["id"])

        for b in bundles:
            try:
                data = await fetch_payment_link(b["payment_link_id"])
                paid = int(data.get("amount_paid", 0) or 0)
                if str(data.get("status", "")).lower() == "paid" and paid >= int(b["price"]) * 100:
                    with db() as c:
                        cur = c.execute(
                            """UPDATE bundle_orders SET status='PAID',razorpay_payment_id=?
                               WHERE id=? AND status='PENDING'""",
                            (str(data.get("id", "")), b["id"])
                        )
                        changed = cur.rowcount
                        rows = c.execute(
                            """SELECT * FROM products
                               WHERE course=? AND stream=? AND semester=? AND active=1 AND file_id<>''
                               ORDER BY subject"""
                            , (b["course"], b["stream"], b["semester"])
                        ).fetchall()
                    if changed:
                        await context.bot.send_message(
                            chat_id=b["telegram_id"],
                            text=f"✅ Payment Verified\n📦 Semester {b['semester']} Pack\n💰 ₹{b['price']}\n📥 आपकी Notes PDFs:"
                        )
                        for p in rows:
                            await context.bot.send_document(
                                chat_id=b["telegram_id"],
                                document=p["file_id"],
                                caption=f"📚 {p['subject']}"
                            )
                        with db() as c:
                            c.execute("UPDATE bundle_orders SET delivered=1 WHERE id=? AND delivered=0", (b["id"],))
            except Exception:
                log.exception("Razorpay bundle polling failed for bundle %s", b["id"])


async def deliver_note(q, order_id):
    with db() as c:
        r=c.execute("""SELECT o.status,o.delivered,p.file_id,p.subject,p.price
                       FROM orders o JOIN products p ON p.id=o.product_id
                       WHERE o.id=? AND o.telegram_id=?""",(order_id,q.from_user.id)).fetchone()
        if not r:
            await q.answer("Order नहीं मिला।",show_alert=True); return
        if r["status"]!="PAID":
            await q.answer("Payment verify नहीं हुई।",show_alert=True); return
        if r["delivered"]:
            await q.answer("PDF पहले ही भेजी जा चुकी है."); return
        if not r["file_id"]:
            await q.answer("Payment मिल गई है, लेकिन PDF उपलब्ध नहीं है। Admin से संपर्क करें।",show_alert=True); return
        c.execute("UPDATE orders SET delivered=1 WHERE id=?",(order_id,))
    await q.message.reply_document(r["file_id"],caption=f"✅ Payment Verified\n📚 {r['subject']}\n💰 ₹{r['price']}\n📥 आपकी Notes PDF")


async def verify_note_payment(q, order_id):
    with db() as c:
        o=c.execute("SELECT * FROM orders WHERE id=? AND telegram_id=?",(order_id,q.from_user.id)).fetchone()
    if not o:
        await q.answer("Order नहीं मिला।",show_alert=True); return
    if o["status"]=="PAID":
        await deliver_note(q,order_id); return
    if not o["payment_link_id"]:
        await q.answer("Payment link नहीं मिला। फिर से Buy Now करें।",show_alert=True); return
    try:
        data=await fetch_payment_link(o["payment_link_id"])
        paid=int(data.get("amount_paid",0) or 0)
        if str(data.get("status","")).lower()=="paid" and paid >= int(o["price"])*100:
            with db() as c:
                c.execute("UPDATE orders SET status='PAID',razorpay_payment_id=? WHERE id=? AND status='PENDING'",
                          (str(data.get("id","")),order_id))
            await q.answer("✅ Payment verified!")
            await deliver_note(q,order_id)
        else:
            await q.answer("⏳ Payment अभी confirm नहीं हुई। Payment के बाद फिर Check करें।",show_alert=True)
    except Exception:
        log.exception("payment verification failed")
        await q.answer("Payment verification अभी नहीं हो पाई। बाद में फिर कोशिश करें।",show_alert=True)


async def deliver_bundle(q, bundle_id):
    with db() as c:
        b=c.execute("SELECT * FROM bundle_orders WHERE id=? AND telegram_id=?",(bundle_id,q.from_user.id)).fetchone()
        if not b:
            await q.answer("Bundle order नहीं मिला।",show_alert=True); return
        if b["status"]!="PAID":
            await q.answer("Payment verify नहीं हुई।",show_alert=True); return
        if b["delivered"]:
            await q.answer("Semester Pack पहले ही भेजा जा चुका है."); return
        rows=c.execute("""SELECT * FROM products WHERE course=? AND stream=? AND semester=? AND active=1 AND file_id<>'' ORDER BY subject""",
                       (b["course"],b["stream"],b["semester"])).fetchall()
        if not rows:
            await q.answer("Payment मिल गई है, लेकिन Pack की PDFs उपलब्ध नहीं हैं। Admin से संपर्क करें।",show_alert=True); return
        c.execute("UPDATE bundle_orders SET delivered=1 WHERE id=?",(bundle_id,))
    await q.message.reply_text(f"✅ Payment Verified\n📦 Semester {b['semester']} Pack\n💰 ₹{b['price']}\n📥 आपकी Notes PDFs:")
    for p in rows:
        await q.message.reply_document(p["file_id"],caption=f"📚 {p['subject']}")


async def verify_bundle_payment(q, bundle_id):
    with db() as c:
        b=c.execute("SELECT * FROM bundle_orders WHERE id=? AND telegram_id=?",(bundle_id,q.from_user.id)).fetchone()
    if not b:
        await q.answer("Bundle order नहीं मिला।",show_alert=True); return
    if b["status"]=="PAID":
        await deliver_bundle(q,bundle_id); return
    if not b["payment_link_id"]:
        await q.answer("Payment link नहीं मिला। फिर से Pack खरीदें।",show_alert=True); return
    try:
        data=await fetch_payment_link(b["payment_link_id"])
        paid=int(data.get("amount_paid",0) or 0)
        if str(data.get("status","")).lower()=="paid" and paid >= int(b["price"])*100:
            with db() as c:
                c.execute("UPDATE bundle_orders SET status='PAID',razorpay_payment_id=? WHERE id=? AND status='PENDING'",
                          (str(data.get("id","")),bundle_id))
            await q.answer("✅ Payment verified!")
            await deliver_bundle(q,bundle_id)
        else:
            await q.answer("⏳ Payment अभी confirm नहीं हुई। Payment के बाद फिर Check करें।",show_alert=True)
    except Exception:
        log.exception("bundle payment verification failed")
        await q.answer("Payment verification अभी नहीं हो पाई। बाद में फिर कोशिश करें।",show_alert=True)


def main_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🎓 B.Sc.",callback_data="course:BSC"), InlineKeyboardButton("📗 B.Com",callback_data="course:BCOM")],
        [InlineKeyboardButton("📙 M.Com",callback_data="course:MCOM"), InlineKeyboardButton("🔬 M.Sc",callback_data="course:MSC")],
        [InlineKeyboardButton("📕 M.A",callback_data="course:MA"), InlineKeyboardButton("📘 B.A",callback_data="course:BA")],
        [InlineKeyboardButton("🆕 Latest Notes",callback_data="latest"), InlineKeyboardButton("⭐ Featured",callback_data="featured")],
        [InlineKeyboardButton("🔎 Search Notes",callback_data="search")],
        [InlineKeyboardButton("🛒 My Purchases",callback_data="purchases")],
        [InlineKeyboardButton("📩 Request Notes",callback_data="request")],
        [InlineKeyboardButton("📢 Join Channel",callback_data="channel"), InlineKeyboardButton("❓ Help",callback_data="help")]
    ])

def admin_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("👤 Users",callback_data="adm:users"), InlineKeyboardButton("📚 Manage Notes",callback_data="adm:products")],
        [InlineKeyboardButton("➕ Add New Note",callback_data="adm:add"), InlineKeyboardButton("🧾 Subject Catalog",callback_data="adm:catalog")],
        [InlineKeyboardButton("📄 Upload / Replace PDF",callback_data="adm:upload"), InlineKeyboardButton("💰 Change Price",callback_data="adm:price")],
        [InlineKeyboardButton("🟠 Approval Queue",callback_data="adm:approvals"), InlineKeyboardButton("📦 Orders",callback_data="adm:orders")],
        [InlineKeyboardButton("📊 Sales",callback_data="adm:sales")],
        [InlineKeyboardButton("📢 Broadcast",callback_data="adm:broadcast"), InlineKeyboardButton("⚙️ Settings",callback_data="adm:settings")]
    ])

def admin_only(user_id):
    return user_id == ADMIN_CHAT_ID


def make_payment_page_url(order_id, amount, subject, bundle=False):
    params={"order":str(order_id),"amount":str(int(amount)),"upi":UPI_ID,"name":BUSINESS_NAME,
            "subject":subject,"bot":BOT_USERNAME,"type":"bundle" if bundle else "note"}
    return PAYMENT_PAGE_URL + ("&" if "?" in PAYMENT_PAGE_URL else "?") + urlencode(params)

async def notify_manual_payment(context, order_id, bundle=False):
    table="bundle_orders" if bundle else "orders"
    with db() as c:
        if bundle:
            row=c.execute("SELECT * FROM bundle_orders WHERE id=?",(order_id,)).fetchone()
            if not row or row["approval_notified"]: return
            text=f"🟠 Payment Approval Required\\n\\n📦 Semester {row['semester']} Complete Pack\\n👤 User ID: {row['telegram_id']}\\n💰 ₹{row['price']}\\n🧾 Order #{order_id}\\n\\nStudent ने payment report किया है। अपने UPI transaction में payment देखकर approve करें।"
        else:
            row=c.execute("SELECT o.*,p.subject FROM orders o JOIN products p ON p.id=o.product_id WHERE o.id=?",(order_id,)).fetchone()
            if not row or row["approval_notified"]: return
            text=f"🟠 Payment Approval Required\\n\\n📚 {row['subject']}\\n👤 User ID: {row['telegram_id']}\\n💰 ₹{row['price']}\\n🧾 Order #{order_id}\\n\\nStudent ने payment report किया है। अपने UPI transaction में payment देखकर approve करें।"
        c.execute(f"UPDATE {table} SET approval_notified=1 WHERE id=?",(order_id,))
    a="approvebundle" if bundle else "approveorder"; r="rejectbundle" if bundle else "rejectorder"
    await context.bot.send_message(chat_id=ADMIN_CHAT_ID,text=text,reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("✅ Approve & Deliver PDF",callback_data=f"{a}:{order_id}")],[InlineKeyboardButton("❌ Reject Payment",callback_data=f"{r}:{order_id}")]]))

async def report_manual_payment(update, context, order_id):
    try: oid=int(order_id)
    except: return False
    uid=update.effective_user.id
    with db() as c:
        o=c.execute("SELECT id,status,telegram_id FROM orders WHERE id=?",(oid,)).fetchone()
        b=c.execute("SELECT id,status,telegram_id FROM bundle_orders WHERE id=?",(oid,)).fetchone()
        if o and o["telegram_id"]==uid:
            if o["status"]=="PENDING": c.execute("UPDATE orders SET status='PAYMENT_REPORTED' WHERE id=? AND status='PENDING'",(oid,))
            bundle=False
        elif b and b["telegram_id"]==uid:
            if b["status"]=="PENDING": c.execute("UPDATE bundle_orders SET status='PAYMENT_REPORTED' WHERE id=? AND status='PENDING'",(oid,))
            bundle=True
        else: return False
    await notify_manual_payment(context,oid,bundle)
    await update.message.reply_text("✅ Payment report मिल गया।\\n\\n🕐 Admin आपके UPI transaction को देखकर approval करेगा। Approval के बाद PDF इसी Telegram chat में भेज दी जाएगी।",reply_markup=main_menu())
    return True

async def approve_manual_order(update, context, oid):
    q=update.callback_query
    with db() as c:
        o=c.execute("SELECT o.*,p.subject,p.file_id FROM orders o JOIN products p ON p.id=o.product_id WHERE o.id=?",(oid,)).fetchone()
        if not o or o["status"]!="PAYMENT_REPORTED": await q.answer("यह order approval के लिए pending नहीं है।",show_alert=True); return
        if not o["file_id"]: await q.answer("इस order की PDF उपलब्ध नहीं है।",show_alert=True); return
        c.execute("UPDATE orders SET status='PAID',approved_at=? WHERE id=? AND status='PAYMENT_REPORTED'",(now(),oid))
    try:
        await context.bot.send_document(chat_id=o["telegram_id"],document=o["file_id"],caption=f"✅ Payment Approved\\n📚 {o['subject']}\\n💰 ₹{o['price']}\\n📥 आपकी Notes PDF")
        with db() as c: c.execute("UPDATE orders SET delivered=1,delivered_at=? WHERE id=?",(now(),oid))
        await q.edit_message_text(f"✅ Order #{oid} approved और PDF deliver हो गई।")
    except Exception:
        log.exception("manual delivery failed"); await q.edit_message_text(f"⚠️ Order #{oid} approved है, लेकिन PDF delivery fail हुई।")

async def reject_manual_order(update, context, oid):
    q=update.callback_query
    with db() as c:
        o=c.execute("SELECT * FROM orders WHERE id=?",(oid,)).fetchone()
        if not o or o["status"]!="PAYMENT_REPORTED": await q.answer("यह order approval के लिए pending नहीं है।",show_alert=True); return
        c.execute("UPDATE orders SET status='REJECTED' WHERE id=? AND status='PAYMENT_REPORTED'",(oid,))
    await context.bot.send_message(o["telegram_id"],"❌ आपका payment report Admin ने reject किया है। कृपया payment details जाँचकर Admin से संपर्क करें।")
    await q.edit_message_text(f"❌ Order #{oid} rejected.")

async def approve_manual_bundle(update, context, bid):
    q=update.callback_query
    with db() as c:
        b=c.execute("SELECT * FROM bundle_orders WHERE id=?",(bid,)).fetchone()
        if not b or b["status"]!="PAYMENT_REPORTED": await q.answer("यह Pack approval के लिए pending नहीं है।",show_alert=True); return
        rows=c.execute("SELECT * FROM products WHERE course=? AND stream=? AND semester=? AND active=1 AND file_id<>'' ORDER BY subject",(b["course"],b["stream"],b["semester"])).fetchall()
        if not rows: await q.answer("Pack की PDFs उपलब्ध नहीं हैं।",show_alert=True); return
        c.execute("UPDATE bundle_orders SET status='PAID',approved_at=? WHERE id=? AND status='PAYMENT_REPORTED'",(now(),bid))
    try:
        await context.bot.send_message(chat_id=b["telegram_id"],text=f"✅ Payment Approved\\n📦 Semester {b['semester']} Complete Pack\\n💰 ₹{b['price']}\\n📥 आपकी Notes PDFs:")
        for p in rows: await context.bot.send_document(chat_id=b["telegram_id"],document=p["file_id"],caption=f"📚 {p['subject']}")
        with db() as c: c.execute("UPDATE bundle_orders SET delivered=1,delivered_at=? WHERE id=?",(now(),bid))
        await q.edit_message_text(f"✅ Pack Order #{bid} approved और PDFs deliver हो गईं।")
    except Exception:
        log.exception("manual bundle delivery failed"); await q.edit_message_text(f"⚠️ Pack #{bid} approved है, लेकिन delivery पूरी नहीं हुई।")

async def reject_manual_bundle(update, context, bid):
    q=update.callback_query
    with db() as c:
        b=c.execute("SELECT * FROM bundle_orders WHERE id=?",(bid,)).fetchone()
        if not b or b["status"]!="PAYMENT_REPORTED": await q.answer("यह Pack approval के लिए pending नहीं है।",show_alert=True); return
        c.execute("UPDATE bundle_orders SET status='REJECTED' WHERE id=? AND status='PAYMENT_REPORTED'",(bid,))
    await context.bot.send_message(b["telegram_id"],"❌ आपका payment report Admin ने reject किया है। कृपया payment details जाँचकर Admin से संपर्क करें।")
    await q.edit_message_text(f"❌ Pack Order #{bid} rejected.")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    with db() as c:
        c.execute("""INSERT OR REPLACE INTO users(telegram_id,username,first_name,created_at)
                     VALUES(?,?,?,COALESCE((SELECT created_at FROM users WHERE telegram_id=?),?))""",
                  (u.id,u.username or "",u.first_name or "",u.id,now()))
    payload=(context.args[0] if context.args else "")
    context.user_data.clear()
    if payload.startswith("paid_") and await report_manual_payment(update, context, payload[5:]): return
    await update.message.reply_text(
        "नमस्ते! 📚\nयहाँ आप अपने Course, Semester और Subject के अनुसार Notes/PDF खरीद सकते हैं।\nनीचे अपना Course चुनें।",
        reply_markup=main_menu())

async def admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not admin_only(update.effective_user.id):
        await update.message.reply_text("यह command केवल Admin के लिए है।")
        return
    context.user_data.clear()
    await update.message.reply_text("🔐 Admin Panel",reply_markup=admin_menu())

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if admin_only(update.effective_user.id):
        context.user_data.clear()
        await update.message.reply_text("❌ प्रक्रिया रद्द कर दी गई।",reply_markup=admin_menu())

async def choose_course(q, course):
    if course == "BSC":
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("PCM",callback_data="stream:BSC:PCM"),InlineKeyboardButton("PCB",callback_data="stream:BSC:PCB")],
            [InlineKeyboardButton("⬅️ Back",callback_data="home")]
        ])
        await q.edit_message_text("B.Sc. Stream चुनें:",reply_markup=kb)
    else:
        await choose_semester(q,course,"")

async def choose_semester(q, course, stream):
    rows = [[InlineKeyboardButton(f"Semester {i}",callback_data=f"sem:{course}:{stream}:{i}")] for i in range(1,7)]
    rows.append([InlineKeyboardButton("⬅️ Back",callback_data="home")])
    await q.edit_message_text("Semester चुनें:",reply_markup=InlineKeyboardMarkup(rows))

async def subjects(q, course, stream, sem):
    sem = int(sem)
    with db() as c:
        catalog = c.execute("""SELECT subject FROM subject_catalog
                               WHERE course=? AND stream=? AND semester=? AND active=1
                               ORDER BY subject""",(course,stream,sem)).fetchall()
        products = c.execute("""SELECT * FROM products WHERE course=? AND stream=? AND semester=? AND active=1
                                ORDER BY subject""",(course,stream,sem)).fetchall()
        by_subject = {r["subject"]: r for r in products}
    text = f"📚 Semester {sem} — Subject चुनें:"
    kb = []
    names = [r["subject"] for r in catalog]
    for r in products:
        if r["subject"] not in names:
            names.append(r["subject"])
    if not names:
        text = "📚 इस Semester के Subjects अभी सेट नहीं हैं।"
    for name in names:
        p = by_subject.get(name)
        if p:
            label = name + (" • PDF" if p["file_id"] else " • जल्द उपलब्ध")
            kb.append([InlineKeyboardButton(label, callback_data=f"prod:{p['id']}")])
        else:
            kb.append([InlineKeyboardButton(name+" • जल्द उपलब्ध", callback_data=f"missing:{course}:{stream}:{sem}")])
    if names and all((by_subject.get(n) and by_subject[n]["file_id"]) for n in names):
        total = sum(by_subject[n]["price"] for n in names)
        bundle = max(1, round(total * (100-BUNDLE_DISCOUNT_PERCENT) / 100))
        kb.append([InlineKeyboardButton(f"📦 Complete Semester Pack — ⭐{bundle}", callback_data=f"bundle:{course}:{stream}:{sem}")])
    kb.append([InlineKeyboardButton("⬅️ Back",callback_data=f"course:{course}")])
    await q.edit_message_text(text,reply_markup=InlineKeyboardMarkup(kb))

async def show_product(q, pid):
    with db() as c:
        r = c.execute("SELECT * FROM products WHERE id=? AND active=1",(pid,)).fetchone()
    if not r:
        await q.answer("यह Notes उपलब्ध नहीं है।",show_alert=True); return
    if not r["file_id"]:
        text = "📚 इस Subject के Notes अभी उपलब्ध नहीं हैं।\nयदि आपको इस Subject के Notes चाहिए तो आप इस Admin ID पर message कर सकते हैं।"
        kb = []
        if ADMIN_USERNAME:
            kb.append([InlineKeyboardButton("📩 Admin को Message करें",url=f"https://t.me/{ADMIN_USERNAME.lstrip('@')}")])
        kb.append([InlineKeyboardButton("⬅️ Back",callback_data=f"sem:{r['course']}:{r['stream']}:{r['semester']}")])
        await q.edit_message_text(text,reply_markup=InlineKeyboardMarkup(kb)); return
    stream = f" / {r['stream']}" if r["stream"] else ""
    text = f"📚 {COURSES.get(r['course'],r['course'])}{stream}\n📖 Semester {r['semester']}\n📝 {r['subject']}\n💰 Price: ₹{r['price']}\n📄 PDF available"
    kb = []
    kb.append([InlineKeyboardButton("👀 4-Page Free Demo",callback_data=f"demo:{pid}")])
    kb.append([InlineKeyboardButton("💳 Buy Now",callback_data=f"buy:{pid}")])
    if ADMIN_USERNAME:
        kb.append([InlineKeyboardButton("📩 Admin से संपर्क करें",url=f"https://t.me/{ADMIN_USERNAME.lstrip('@')}")])
    kb.append([InlineKeyboardButton("⬅️ Back",callback_data=f"sem:{r['course']}:{r['stream']}:{r['semester']}")])
    await q.edit_message_text(text,reply_markup=InlineKeyboardMarkup(kb))

async def create_demo_file_id(context, product_id):
    """Create and cache a 4-page demo PDF from the uploaded original PDF."""
    with db() as c:
        p = c.execute("SELECT * FROM products WHERE id=? AND active=1 AND file_id<>''",(product_id,)).fetchone()
    if not p:
        return None, "PDF उपलब्ध नहीं है।"
    if p["preview_file_id"]:
        return p["preview_file_id"], None

    temp_pdf = None
    temp_demo = None
    try:
        from pypdf import PdfReader, PdfWriter
        tg_file = await context.bot.get_file(p["file_id"])
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as src:
            temp_pdf = src.name
        await tg_file.download_to_drive(temp_pdf)

        reader = PdfReader(temp_pdf)
        total = len(reader.pages)
        if total == 0:
            return None, "इस PDF में कोई page नहीं मिला।"

        # Four representative pages: first 2 + middle + last.
        # If the PDF has fewer than 4 pages, use all available pages.
        if total <= DEMO_PAGES:
            indexes = list(range(total))
        else:
            indexes = sorted(set([0, 1, total // 2, total - 1]))

        writer = PdfWriter()
        for i in indexes:
            writer.add_page(reader.pages[i])

        with tempfile.NamedTemporaryFile(suffix="_demo.pdf", delete=False) as out:
            temp_demo = out.name
        with open(temp_demo, "wb") as fh:
            writer.write(fh)

        # Telegram needs a chat upload before it gives the bot a reusable file_id.
        # Store the generated demo in the admin chat; students then receive the
        # cached file_id without regenerating the PDF.
        with open(temp_demo, "rb") as demo_fh:
            sent = await context.bot.send_document(
                chat_id=ADMIN_CHAT_ID,
                document=InputFile(demo_fh, filename=f"{p['subject']}_Free_Demo.pdf"),
                caption=f"🛠️ Auto Demo Created — #{product_id}\n{p['subject']}\n📄 {len(indexes)} representative pages"
            )

        if not sent.document:
            return None, "Telegram demo upload नहीं कर पाया।"

        with db() as c:
            c.execute("UPDATE products SET preview_file_id=? WHERE id=?",
                      (sent.document.file_id, product_id))
        return sent.document.file_id, None
    except Exception as e:
        log.exception("demo generation failed for product %s: %s", product_id, e)
        return None, "Demo PDF तैयार नहीं हो सकी।"
    finally:
        for path in (temp_pdf, temp_demo):
            if path:
                try: os.remove(path)
                except OSError: pass

async def send_demo(q, pid, context):
    """Send the cached/generated 4-page demo to the student."""
    with db() as c:
        p = c.execute("SELECT * FROM products WHERE id=? AND active=1 AND file_id<>''",(pid,)).fetchone()
    if not p:
        await q.answer("PDF अभी उपलब्ध नहीं है।", show_alert=True)
        return

    preview_id = p["preview_file_id"]
    if not preview_id:
        await q.answer("📄 Demo तैयार किया जा रहा है…")
        preview_id, err = await create_demo_file_id(context, pid)
        if not preview_id:
            await q.message.reply_text(f"❌ {err} Admin से PDF दोबारा upload करवाएं।")
            return

    await q.message.reply_document(
        preview_id,
        caption=f"👀 Free Demo — {p['subject']}\n📄 4 representative pages\n💡 Notes पसंद आए तो Buy Now करें।"
    )

async def buy(q, pid):
    if not UPI_ID:
        await q.message.reply_text("💳 Payment अभी configure नहीं है। Admin को UPI_ID सेट करना होगा।"); return
    with db() as c:
        p=c.execute("SELECT * FROM products WHERE id=? AND active=1 AND file_id<>''",(pid,)).fetchone()
        if not p: await q.answer("PDF अभी उपलब्ध नहीं है।",show_alert=True); return
        payload=f"RU_NOTE:{q.from_user.id}:{pid}:{int(datetime.now().timestamp())}"
        cur=c.execute("INSERT INTO orders(telegram_id,product_id,payload,price,status,created_at) VALUES(?,?,?,?,?,?)",(q.from_user.id,pid,payload,p["price"],"PENDING",now()))
        oid=cur.lastrowid
    url=make_payment_page_url(oid,p["price"],p["subject"])
    await q.message.reply_text(f"💳 Payment Page\\n\\n📚 {p['subject']}\\n💰 Amount: ₹{p['price']}\\n\\nPayment page खोलें और UPI app/QR से payment करें।",reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("💳 Open Payment Page",url=url)],[InlineKeyboardButton("⬅️ Main Menu",callback_data="home")]]))

async def buy_bundle(q, course, stream, sem):
    if not UPI_ID:
        await q.message.reply_text("💳 Payment अभी configure नहीं है। Admin को UPI_ID सेट करना होगा।"); return
    sem=int(sem)
    with db() as c:
        catalog=c.execute("SELECT subject FROM subject_catalog WHERE course=? AND stream=? AND semester=? AND active=1 ORDER BY subject",(course,stream,sem)).fetchall()
        if not catalog: await q.answer("इस Semester की Subject list अभी उपलब्ध नहीं है।",show_alert=True); return
        names=[r["subject"] for r in catalog]; ph=",".join("?" for _ in names)
        rows=c.execute(f"SELECT * FROM products WHERE course=? AND stream=? AND semester=? AND active=1 AND subject IN ({ph})",(course,stream,sem,*names)).fetchall()
        by={r["subject"]:r for r in rows}
        if len(by)!=len(names) or any(not by[n]["file_id"] for n in names): await q.answer("Complete Semester Pack अभी पूरा उपलब्ध नहीं है।",show_alert=True); return
        original=sum(by[n]["price"] for n in names); final=max(1,round(original*(100-BUNDLE_DISCOUNT_PERCENT)/100))
        payload=f"RU_BUNDLE:{q.from_user.id}:{course}:{stream}:{sem}:{int(datetime.now().timestamp())}"
        cur=c.execute("INSERT INTO bundle_orders(telegram_id,course,stream,semester,payload,price,original_price,discount_percent,status,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",(q.from_user.id,course,stream,sem,payload,final,original,BUNDLE_DISCOUNT_PERCENT,"PENDING",now()))
        bid=cur.lastrowid
    url=make_payment_page_url(bid,final,f"{COURSES.get(course,course)} Semester {sem} Complete Pack",True)
    await q.message.reply_text(f"📦 Semester {sem} Complete Pack\\n💰 Original: ₹{original}\\n🎁 Discount: {BUNDLE_DISCOUNT_PERCENT}%\\n💳 Pay: ₹{final}\\n\\nPayment page खोलें और UPI app/QR से payment करें।",reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("💳 Open Payment Page",url=url)],[InlineKeyboardButton("⬅️ Main Menu",callback_data="home")]]))

async def show_missing(q, course, stream, sem):
    kb=[]
    if ADMIN_USERNAME:
        kb.append([InlineKeyboardButton("📩 Admin को Message करें",url=f"https://t.me/{ADMIN_USERNAME.lstrip('@')}")])
    kb.append([InlineKeyboardButton("⬅️ Back",callback_data=f"sem:{course}:{stream}:{sem}")])
    await q.edit_message_text("📚 इस Subject के Notes अभी उपलब्ध नहीं हैं।\nयदि आपको इस Subject के Notes चाहिए तो आप इस Admin ID पर message कर सकते हैं।",reply_markup=InlineKeyboardMarkup(kb))

async def precheckout(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.pre_checkout_query
    with db() as c:
        row = c.execute("SELECT id FROM orders WHERE payload=? AND telegram_id=? AND status='PENDING'",(q.invoice_payload,q.from_user.id)).fetchone()
        brow = c.execute("SELECT id FROM bundle_orders WHERE payload=? AND telegram_id=? AND status='PENDING'",(q.invoice_payload,q.from_user.id)).fetchone()
    await q.answer(ok=bool(row or brow),error_message=None if (row or brow) else "Order not found.")

async def successful(update: Update, context: ContextTypes.DEFAULT_TYPE):
    sp = update.message.successful_payment
    uid = update.effective_user.id
    with db() as c:
        order = c.execute("SELECT * FROM orders WHERE payload=? AND telegram_id=? AND status='PENDING'",(sp.invoice_payload,uid)).fetchone()
        bundle = c.execute("SELECT * FROM bundle_orders WHERE payload=? AND telegram_id=? AND status='PENDING'",(sp.invoice_payload,uid)).fetchone()
        if order:
            p = c.execute("SELECT * FROM products WHERE id=? AND active=1",(order["product_id"],)).fetchone()
            c.execute("UPDATE orders SET status='PAID',telegram_charge_id=? WHERE id=?",(sp.telegram_payment_charge_id,order["id"]))
            if not p or not p["file_id"]:
                await update.message.reply_text("Payment सफल हुआ, लेकिन PDF अभी उपलब्ध नहीं है। Admin से संपर्क करें।"); return
            await update.message.reply_document(p["file_id"],caption=f"📚 {p['subject']}\nधन्यवाद! आपका PDF यहाँ है।")
            c.execute("UPDATE orders SET delivered=1 WHERE id=?",(order["id"],)); return
        if not bundle: return
        c.execute("UPDATE bundle_orders SET status='PAID',telegram_charge_id=? WHERE id=?",(sp.telegram_payment_charge_id,bundle["id"]))
        catalog=c.execute("""SELECT subject FROM subject_catalog WHERE course=? AND stream=? AND semester=? AND active=1 ORDER BY subject""",
                          (bundle["course"],bundle["stream"],bundle["semester"])).fetchall()
        for row in catalog:
            p=c.execute("""SELECT * FROM products WHERE course=? AND stream=? AND semester=? AND subject=? AND active=1 AND file_id<>''""",
                        (bundle["course"],bundle["stream"],bundle["semester"],row["subject"])).fetchone()
            if p:
                await update.message.reply_document(p["file_id"],caption=f"📚 {p['subject']}\n📦 Complete Semester Pack")
        c.execute("UPDATE bundle_orders SET delivered=1 WHERE id=?",(bundle["id"],))
        await update.message.reply_text(f"🎉 Complete Semester Pack delivered!\nआपको {bundle['discount_percent']}% bundle discount मिला।")

async def purchases(q):
    with db() as c:
        rows=c.execute("""SELECT o.id,p.subject,p.course,p.stream,p.semester,o.price,o.created_at
                          FROM orders o JOIN products p ON p.id=o.product_id
                          WHERE o.telegram_id=? AND o.status='PAID' AND o.delivered=1 ORDER BY o.id DESC""",
                       (q.from_user.id,)).fetchall()
    if not rows:
        await q.edit_message_text("🛒 आपकी अभी कोई खरीदारी नहीं है।",reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Main Menu",callback_data="home")]])); return
    kb=[]
    text="🛒 आपकी Purchases:\n\n"
    for r in rows[:20]:
        text += f"📖 {r['subject']} — ⭐ {r['price']}\n"
        kb.append([InlineKeyboardButton(f"📥 {r['subject']}",callback_data=f"download:{r['id']}")])
    kb.append([InlineKeyboardButton("⬅️ Main Menu",callback_data="home")])
    await q.edit_message_text(text,reply_markup=InlineKeyboardMarkup(kb))

def admin_course_buttons(prefix="adducourse"):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🎓 B.Sc.",callback_data=f"{prefix}:BSC"), InlineKeyboardButton("📗 B.Com",callback_data=f"{prefix}:BCOM")],
        [InlineKeyboardButton("📙 M.Com",callback_data=f"{prefix}:MCOM"), InlineKeyboardButton("🔬 M.Sc",callback_data=f"{prefix}:MSC")],
        [InlineKeyboardButton("📕 M.A",callback_data=f"{prefix}:MA"), InlineKeyboardButton("📘 B.A",callback_data=f"{prefix}:BA")],
        [InlineKeyboardButton("⬅️ Admin Panel",callback_data="adm:home")]
    ])

def admin_semester_buttons(prefix, course, stream):
    # UOR 2025-27 PG semester schemes are generally I-IV; UG NEP programmes use I-VI.
    semesters = [1,2,3,4] if course in {"MCOM","MSC","MA"} else [1,2,3,4,5,6]
    rows=[]
    for i in range(0,len(semesters),3):
        rows.append([
            InlineKeyboardButton(f"{n}️⃣",callback_data=f"{prefix}:{course}:{stream}:{n}")
            for n in semesters[i:i+3]
        ])
    rows.append([InlineKeyboardButton("⬅️ Admin Panel",callback_data="adm:home")])
    return InlineKeyboardMarkup(rows)

def admin_price_buttons(prefix, pid):
    prices=[20,29,49,50,79,99,149,199,299]
    rows=[]
    for i in range(0,len(prices),3):
        rows.append([InlineKeyboardButton(f"₹{p}",callback_data=f"{prefix}:{pid}:{p}") for p in prices[i:i+3]])
    rows.append([InlineKeyboardButton("✏️ Custom Price",callback_data=f"admpricecustom:{pid}")])
    rows.append([InlineKeyboardButton("❌ Cancel",callback_data="adm:home")])
    return InlineKeyboardMarkup(rows)

async def admin_add_start(q, context):
    context.user_data.clear()
    context.user_data["admin_action"]="add_course"
    await q.edit_message_text("➕ Add New Note\n\nपहले Course चुनें:",reply_markup=admin_course_buttons())

async def admin_upload_start(q, context):
    context.user_data.clear()
    with db() as c:
        rows=c.execute("SELECT id,subject,semester,price,file_id FROM products WHERE active=1 ORDER BY id DESC LIMIT 30").fetchall()
    if not rows:
        await q.edit_message_text("📄 अभी कोई Product नहीं है। पहले ➕ Add New Note करें.",reply_markup=admin_menu()); return
    kb=[]
    for r in rows:
        status="✅ PDF" if r["file_id"] else "⚠️ PDF बाकी"
        kb.append([InlineKeyboardButton(f"#{r['id']} • {r['subject'][:28]} • S{r['semester']} • {status}",callback_data=f"admupload:{r['id']}")])
    kb.append([InlineKeyboardButton("⬅️ Admin Panel",callback_data="adm:home")])
    await q.edit_message_text("📄 Upload / Replace PDF\n\nजिस Note में PDF लगानी है, उसे चुनें:",reply_markup=InlineKeyboardMarkup(kb))

async def admin_price_start(q, context):
    context.user_data.clear()
    with db() as c:
        rows=c.execute("SELECT id,subject,semester,price,file_id FROM products WHERE active=1 ORDER BY id DESC LIMIT 30").fetchall()
    if not rows:
        await q.edit_message_text("💰 अभी कोई Product नहीं है। पहले ➕ Add New Note करें.",reply_markup=admin_menu()); return
    kb=[]
    for r in rows:
        status="📄" if r["file_id"] else "⚠️"
        kb.append([InlineKeyboardButton(f"#{r['id']} • {r['subject'][:30]} • S{r['semester']} • ₹{r['price']} {status}",callback_data=f"admprice:{r['id']}")])
    kb.append([InlineKeyboardButton("⬅️ Admin Panel",callback_data="adm:home")])
    await q.edit_message_text("💰 Change Price\n\nजिस Note की price बदलनी है, उसे चुनें:",reply_markup=InlineKeyboardMarkup(kb))

async def admin_broadcast_start(q, context):
    context.user_data["admin_action"]="broadcast"
    await q.edit_message_text("📢 अगला message सभी registered users को भेजा जाएगा।\nText/photo/document भेज सकते हैं।\n/cancel से रद्द करें।")

async def admin_text(update, context):
    if update.effective_user.id != ADMIN_CHAT_ID: return
    action=context.user_data.get("admin_action")
    if not action: return
    text=(update.message.text or "").strip()

    if action=="add_subject":
        if not text:
            await update.message.reply_text("Subject का नाम खाली नहीं हो सकता।"); return
        context.user_data.update(admin_action="add_price",subject=text)
        await update.message.reply_text(f"📝 Subject: {text}\n\nअब नीचे price चुनें:",reply_markup=admin_price_buttons("adduprice",0))
        return

    if action=="add_price":
        try: price=int(text); assert price>0
        except:
            await update.message.reply_text("Price केवल positive number में भेजें।"); return
        d=context.user_data
        with db() as c:
            c.execute("""INSERT INTO subject_catalog(course,stream,semester,subject,active)
                         VALUES(?,?,?,?,1) ON CONFLICT(course,stream,semester,subject) DO UPDATE SET active=1""",
                      (d["course"],d["stream"],d["semester"],d["subject"]))
            c.execute("""INSERT INTO products(course,stream,semester,subject,price,file_id,active,created_at)
                         VALUES(?,?,?,?,?,'',1,?) ON CONFLICT(course,stream,semester,subject)
                         DO UPDATE SET price=excluded.price,active=1""",
                      (d["course"],d["stream"],d["semester"],d["subject"],price,now()))
            p=c.execute("SELECT id FROM products WHERE course=? AND stream=? AND semester=? AND subject=?",
                        (d["course"],d["stream"],d["semester"],d["subject"])).fetchone()
        pid=int(p["id"])
        context.user_data.update(admin_action="upload_pdf",product_id=pid)
        await update.message.reply_text(f"✅ Note तैयार है!\n\n📝 {d['subject']}\n💰 Price: ₹{price}\n🆔 Product ID: #{pid}\n\nअब PDF को Document के रूप में भेजें।",
                                        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel",callback_data="adm:home")]]))
        return

    if action=="price_custom":
        try: price=int(text); assert price>0
        except:
            await update.message.reply_text("Price केवल positive number में भेजें।"); return
        pid=int(context.user_data["product_id"])
        with db() as c:
            p=c.execute("SELECT subject FROM products WHERE id=? AND active=1",(pid,)).fetchone()
            if p: c.execute("UPDATE products SET price=? WHERE id=?",(price,pid))
        context.user_data.clear()
        await update.message.reply_text(f"✅ Price update हो गई!\n💰 नई Price: ₹{price}",reply_markup=admin_menu())
        return

    if action=="broadcast":
        context.user_data.clear()
        with db() as c: users=c.execute("SELECT telegram_id FROM users").fetchall()
        sent=0
        for u in users:
            try:
                await context.bot.copy_message(chat_id=u["telegram_id"],from_chat_id=ADMIN_CHAT_ID,message_id=update.message.message_id)
                sent+=1
            except Exception as e:
                log.warning("broadcast %s: %s",u["telegram_id"],e)
        await update.message.reply_text(f"📢 Broadcast complete. Sent: {sent}/{len(users)}",reply_markup=admin_menu())
        return

async def admin_document(update, context):
    if update.effective_user.id != ADMIN_CHAT_ID: return
    if context.user_data.get("admin_action")!="upload_pdf": return
    pid=context.user_data.get("product_id")
    if not update.message.document: return
    filename = update.message.document.file_name or ""
    mime = update.message.document.mime_type or ""
    if mime not in ("application/pdf","application/octet-stream") and not filename.lower().endswith(".pdf"):
        await update.message.reply_text("कृपया PDF document भेजें।")
        return

    with db() as c:
        c.execute("UPDATE products SET file_id=?,preview_file_id='' WHERE id=?",
                  (update.message.document.file_id,pid))
        p=c.execute("SELECT subject FROM products WHERE id=?",(pid,)).fetchone()

    # Demo is created automatically immediately after the original PDF upload.
    preview_id, demo_error = await create_demo_file_id(context, pid)
    context.user_data.clear()

    if preview_id:
        demo_status = "✅ 4-page Free Demo अपने-आप तैयार हो गया है।"
    else:
        demo_status = f"⚠️ Original PDF upload हो गई, लेकिन Demo नहीं बन सका: {demo_error}"

    await update.message.reply_text(
        f"✅ Product #{pid} की PDF upload हो गई।\n\n"
        f"📝 {p['subject'] if p else 'Note'}\n"
        f"{demo_status}\n\n"
        "Student को Subject खोलने पर 👀 4-Page Free Demo मिलेगा।",
        reply_markup=admin_menu())

async def latest_notes(q):
    with db() as c:
        rows=c.execute("SELECT * FROM products WHERE active=1 AND file_id<>'' ORDER BY id DESC LIMIT 12").fetchall()
    kb=[[InlineKeyboardButton(f"{r['subject']} • ⭐{r['price']}",callback_data=f"prod:{r['id']}")] for r in rows]
    kb.append([InlineKeyboardButton("⬅️ Main Menu",callback_data="home")])
    await q.edit_message_text("🆕 Latest Notes" if rows else "🆕 अभी कोई Notes available नहीं हैं.",reply_markup=InlineKeyboardMarkup(kb))

async def featured_notes(q):
    with db() as c:
        rows=c.execute("""SELECT p.*,COUNT(o.id) sales FROM products p
                          LEFT JOIN orders o ON o.product_id=p.id AND o.status='PAID'
                          WHERE p.active=1 AND p.file_id<>'' GROUP BY p.id
                          ORDER BY sales DESC,p.id DESC LIMIT 12""").fetchall()
    kb=[[InlineKeyboardButton(f"⭐ {r['subject']} • {r['price']}",callback_data=f"prod:{r['id']}")] for r in rows]
    kb.append([InlineKeyboardButton("⬅️ Main Menu",callback_data="home")])
    await q.edit_message_text("⭐ Featured Notes",reply_markup=InlineKeyboardMarkup(kb))

async def search_prompt(q, context):
    context.user_data["user_action"] = "search"
    await q.edit_message_text(
        "🔎 Search Notes\n\nSubject, course या keyword लिखें।\nउदाहरण: Physics, Mathematics, Chemistry",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Main Menu",callback_data="home")]])
    )


async def search_results_from_message(update, query):
    query = query.strip()
    like = f"%{query}%"
    with db() as c:
        rows = c.execute("""
            SELECT * FROM products
            WHERE active=1 AND file_id<>''
              AND (subject LIKE ? OR course LIKE ? OR stream LIKE ?)
            ORDER BY id DESC LIMIT 20
        """, (like, like, like)).fetchall()
    kb = [[InlineKeyboardButton(f"📖 {r['subject']} • ⭐{r['price']}", callback_data=f"prod:{r['id']}")] for r in rows]
    kb += [[InlineKeyboardButton("🔎 Search Again",callback_data="search")],
           [InlineKeyboardButton("⬅️ Main Menu",callback_data="home")]]
    text = f"🔎 Search results for: {query}\n\n" + (f"{len(rows)} Notes मिले:" if rows else "कोई matching Notes नहीं मिले।")
    await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(kb))


async def help_menu(q):
    text = (
        "❓ RU Notes Store — Help\n\n"
        "1️⃣ Course → Semester → Subject चुनें।\n"
        "2️⃣ उपलब्ध Notes खोलें।\n"
        "3️⃣ Buy Now दबाकर Razorpay से UPI / Card payment करें।\n"
        "4️⃣ Payment के बाद “मैंने Payment कर दिया” दबाएं। Admin transaction देखकर approve करेगा, फिर PDF Telegram पर मिलेगी।\n\n"
        "📩 Notes नहीं मिल रहे हों तो Request Notes से Admin को बताएं।"
    )
    await q.edit_message_text(text, reply_markup=InlineKeyboardMarkup([
        [InlineKeyboardButton("📚 Select Course",callback_data="home")],
        [InlineKeyboardButton("🛒 My Purchases",callback_data="purchases")],
        [InlineKeyboardButton("📩 Request Notes",callback_data="request")],
        [InlineKeyboardButton("⬅️ Main Menu",callback_data="home")]
    ]))


async def channel_menu(q):
    if CHANNEL_USERNAME:
        kb = [
            [InlineKeyboardButton("📢 Join RU Notes Channel",url=f"https://t.me/{CHANNEL_USERNAME.lstrip('@')}")],
            [InlineKeyboardButton("⬅️ Main Menu",callback_data="home")]
        ]
        text = "📢 Latest updates और नए Notes के लिए हमारे Telegram Channel से जुड़ें।"
    else:
        kb = [[InlineKeyboardButton("⬅️ Main Menu",callback_data="home")]]
        text = "📢 Channel अभी configure नहीं किया गया है। Admin से CHANNEL_USERNAME secret में सेट करवाएं।"
    await q.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb))


async def user_text(update, context):
    if update.effective_user.id == ADMIN_CHAT_ID:
        return
    if context.user_data.get("user_action") != "search":
        return
    query = (update.message.text or "").strip()
    context.user_data.pop("user_action", None)
    await search_results_from_message(update, query)


async def request_notes(q):
    kb=[]
    if ADMIN_USERNAME:
        kb.append([InlineKeyboardButton("📩 Admin को Subject बताएं",url=f"https://t.me/{ADMIN_USERNAME.lstrip('@')}")])
    kb.append([InlineKeyboardButton("⬅️ Main Menu",callback_data="home")])
    await q.edit_message_text("📩 जिस Subject के Notes चाहिए, Admin को Course + Semester + Subject भेजें।",reply_markup=InlineKeyboardMarkup(kb))

async def callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q=update.callback_query
    await q.answer()
    d=q.data
    if d=="home": await q.edit_message_text("Course चुनें:",reply_markup=main_menu()); return
    if d=="channel": await channel_menu(q); return
    if d=="help": await help_menu(q); return
    if d=="latest": await latest_notes(q); return
    if d=="featured": await featured_notes(q); return
    if d=="request": await request_notes(q); return
    if d=="search": await search_prompt(q,context); return
    if d.startswith("approveorder:") and admin_only(q.from_user.id): await approve_manual_order(q,context,int(d.split(":")[1])); return
    if d.startswith("rejectorder:") and admin_only(q.from_user.id): await reject_manual_order(q,context,int(d.split(":")[1])); return
    if d.startswith("approvebundle:") and admin_only(q.from_user.id): await approve_manual_bundle(q,context,int(d.split(":")[1])); return
    if d.startswith("rejectbundle:") and admin_only(q.from_user.id): await reject_manual_bundle(q,context,int(d.split(":")[1])); return

    if d.startswith("adducourse:") and admin_only(q.from_user.id):
        course=d.split(":")[1]
        context.user_data.clear()
        context.user_data["course"]=course
        if course=="BSC":
            context.user_data["admin_action"]="add_stream"
            await q.edit_message_text("➕ Add New Note\n\nB.Sc. Stream चुनें:",reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🧪 PCM",callback_data="addustream:BSC:PCM"),InlineKeyboardButton("🧬 PCB",callback_data="addustream:BSC:PCB")],
                [InlineKeyboardButton("⬅️ Admin Panel",callback_data="adm:home")]
            ]))
        else:
            context.user_data.update(admin_action="add_semester",stream="")
            await q.edit_message_text("➕ Add New Note\n\nSemester चुनें:",reply_markup=admin_semester_buttons("addsem",course,""))
        return

    if d.startswith("addustream:") and admin_only(q.from_user.id):
        _,course,stream=d.split(":")
        context.user_data.update(admin_action="add_semester",course=course,stream=stream)
        await q.edit_message_text(f"➕ Add New Note\n\n{stream} → Semester चुनें:",reply_markup=admin_semester_buttons("addsem",course,stream))
        return

    if d.startswith("addsem:") and admin_only(q.from_user.id):
        _,course,stream,sem=d.split(":")
        context.user_data.update(admin_action="add_subject",course=course,stream=stream,semester=int(sem))
        await q.edit_message_text(f"➕ Add New Note\n\n📚 {COURSES.get(course,course)}{' / '+stream if stream else ''} • Semester {sem}\n\nअब Subject का नाम message में लिखें:",
                                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel",callback_data="adm:home")]]))
        return

    if d.startswith("adduprice:") and admin_only(q.from_user.id):
        _,pid,price=d.split(":")
        if int(pid)!=0: return
        context.user_data["selected_price"]=int(price)
        await q.edit_message_text(f"💰 Selected Price: ₹{price}\n\nअब Confirm करें:",
                                    reply_markup=InlineKeyboardMarkup([
                                        [InlineKeyboardButton(f"✅ Confirm ₹{price}",callback_data=f"adduconfirm:{price}")],
                                        [InlineKeyboardButton("⬅️ Back",callback_data="adm:add")]
                                    ]))
        return

    if d.startswith("adduconfirm:") and admin_only(q.from_user.id):
        price=int(d.split(":")[1])
        dct=context.user_data
        if not all(k in dct for k in ("course","stream","semester","subject")):
            context.user_data.clear()
            await q.edit_message_text("Session expired. फिर से ➕ Add New Note करें.",reply_markup=admin_menu()); return
        with db() as c:
            c.execute("""INSERT INTO subject_catalog(course,stream,semester,subject,active)
                         VALUES(?,?,?,?,1) ON CONFLICT(course,stream,semester,subject) DO UPDATE SET active=1""",
                      (dct["course"],dct["stream"],dct["semester"],dct["subject"]))
            c.execute("""INSERT INTO products(course,stream,semester,subject,price,file_id,active,created_at)
                         VALUES(?,?,?,?,?,'',1,?) ON CONFLICT(course,stream,semester,subject)
                         DO UPDATE SET price=excluded.price,active=1""",
                      (dct["course"],dct["stream"],dct["semester"],dct["subject"],price,now()))
            p=c.execute("SELECT id FROM products WHERE course=? AND stream=? AND semester=? AND subject=?",
                        (dct["course"],dct["stream"],dct["semester"],dct["subject"])).fetchone()
        pid=int(p["id"])
        context.user_data.update(admin_action="upload_pdf",product_id=pid)
        await q.edit_message_text(f"✅ Note तैयार है!\n\n📝 {dct['subject']}\n💰 Price: ₹{price}\n🆔 Product ID: #{pid}\n\nअब PDF को Document के रूप में भेजें.",
                                  reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel",callback_data="adm:home")]]))
        return

    if d.startswith("admupload:") and admin_only(q.from_user.id):
        pid=int(d.split(":")[1])
        with db() as c:
            p=c.execute("SELECT id,subject,price FROM products WHERE id=? AND active=1",(pid,)).fetchone()
        if not p:
            await q.answer("Product नहीं मिला।",show_alert=True); return
        context.user_data.update(admin_action="upload_pdf",product_id=pid)
        await q.edit_message_text(f"📄 Upload PDF\n\n🆔 Product #{pid}\n📝 {p['subject']}\n💰 Price: ₹{p['price']}\n\nअब PDF को Document के रूप में भेजें.",
                                  reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ PDF List",callback_data="adm:upload")],[InlineKeyboardButton("⬅️ Admin Panel",callback_data="adm:home")]]))
        return

    if d.startswith("admprice:") and admin_only(q.from_user.id):
        pid=int(d.split(":")[1])
        with db() as c:
            p=c.execute("SELECT id,subject,price FROM products WHERE id=? AND active=1",(pid,)).fetchone()
        if not p:
            await q.answer("Product नहीं मिला।",show_alert=True); return
        await q.edit_message_text(f"💰 Change Price\n\n🆔 Product #{pid}\n📝 {p['subject']}\n💰 Current: ₹{p['price']}\n\nनई price चुनें:",
                                  reply_markup=admin_price_buttons("admpricev",pid))
        return

    if d.startswith("admpricev:") and admin_only(q.from_user.id):
        _,pid,price=d.split(":")
        pid=int(pid); price=int(price)
        with db() as c:
            p=c.execute("SELECT subject FROM products WHERE id=? AND active=1",(pid,)).fetchone()
            if p: c.execute("UPDATE products SET price=? WHERE id=?",(price,pid))
        if not p:
            await q.answer("Product नहीं मिला।",show_alert=True); return
        await q.edit_message_text(f"✅ Price update हो गई!\n\n📝 {p['subject']}\n💰 नई Price: ₹{price}",
                                  reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("💰 Change Another Price",callback_data="adm:price")],[InlineKeyboardButton("⬅️ Admin Panel",callback_data="adm:home")]]))
        return

    if d.startswith("admpricecustom:") and admin_only(q.from_user.id):
        pid=int(d.split(":")[1])
        if pid==0:
            context.user_data["admin_action"]="add_price"
            await q.edit_message_text(
                "✏️ Custom Price\n\nनई price number में भेजें।\nउदाहरण: 75",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel",callback_data="adm:home")]])
            )
        else:
            context.user_data.update(admin_action="price_custom",product_id=pid)
            await q.edit_message_text(
                f"✏️ Custom Price\n\nProduct #{pid}\nनई price number में भेजें।\nउदाहरण: 75",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel",callback_data="adm:home")]])
            )
        return
    if d.startswith("course:"): await choose_course(q,d.split(":")[1]); return
    if d.startswith("stream:"):
        _,course,stream=d.split(":"); await choose_semester(q,course,stream); return
    if d.startswith("sem:"):
        _,course,stream,sem=d.split(":"); await subjects(q,course,stream,sem); return
    if d.startswith("prod:"): await show_product(q,int(d.split(":")[1])); return
    if d.startswith("demo:"):
        await send_demo(q,int(d.split(":")[1]),context)
        return
    if d.startswith("missing:"):
        _,course,stream,sem=d.split(":"); await show_missing(q,course,stream,int(sem)); return
    if d.startswith("bundle:"):
        _,course,stream,sem=d.split(":"); await buy_bundle(q,course,stream,int(sem)); return
    if d.startswith("buy:"): await buy(q,int(d.split(":")[1])); return
    if d.startswith("paycheck:"):
        await verify_note_payment(q,int(d.split(":")[1])); return
    if d.startswith("bundlecheck:"):
        await verify_bundle_payment(q,int(d.split(":")[1])); return
    if d=="purchases": await purchases(q); return
    if d.startswith("download:"):
        oid=int(d.split(":")[1])
        with db() as c:
            r=c.execute("""SELECT p.file_id,p.subject FROM orders o JOIN products p ON p.id=o.product_id
                           WHERE o.id=? AND o.telegram_id=? AND o.status='PAID' AND o.delivered=1""",
                        (oid,q.from_user.id)).fetchone()
        if not r: await q.answer("यह PDF आपकी purchase में नहीं है।",show_alert=True); return
        await q.message.reply_document(r["file_id"],caption=f"📚 {r['subject']}")
        return
    if d.startswith("admfilter:") and admin_only(q.from_user.id):
        filt=d.split(":")[1]
        with db() as c:
            if filt in ("pending","paid","failed"):
                rows=c.execute("SELECT id,telegram_id,price,status,created_at FROM orders WHERE status=? ORDER BY id DESC LIMIT 30",(filt.upper(),)).fetchall()
                title=f"📦 {filt.title()} Orders"
            elif filt=="today":
                rows=c.execute("SELECT id,telegram_id,price,status,created_at FROM orders WHERE substr(created_at,1,10)=? ORDER BY id DESC LIMIT 30",(date.today().isoformat(),)).fetchall()
                title="📅 Today's Orders"
            else:
                rows=c.execute("SELECT id,telegram_id,price,status,created_at FROM orders WHERE substr(created_at,1,7)=? ORDER BY id DESC LIMIT 30",(date.today().strftime("%Y-%m"),)).fetchall()
                title="📆 This Month's Orders"
        text=title+"\n\n"+("\n".join(f"#{r['id']} • user {r['telegram_id']} • ⭐{r['price']} • {r['status']} • {r['created_at'][:16]}" for r in rows) or "No matching orders")
        await q.edit_message_text(text[:4000],reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("⬅️ Orders",callback_data="adm:orders")],
            [InlineKeyboardButton("⬅️ Admin Panel",callback_data="adm:home")]
        ])); return
    if d=="adm:home" and admin_only(q.from_user.id):
        await q.edit_message_text("🔐 Admin Panel",reply_markup=admin_menu()); return
    if d.startswith("adm:") and admin_only(q.from_user.id):
        a=d.split(":")[1]
        if a=="add": await admin_add_start(q,context); return
        if a=="upload": await admin_upload_start(q,context); return
        if a=="price": await admin_price_start(q,context); return
        if a=="broadcast": await admin_broadcast_start(q,context); return
        if a=="users":
            with db() as c:
                n=c.execute("SELECT COUNT(*) FROM users").fetchone()[0]
                recent=c.execute("SELECT telegram_id,username,first_name FROM users ORDER BY rowid DESC LIMIT 15").fetchall()
            details="\n".join(f"• {r['first_name'] or 'User'} @{r['username'] or '-'} — {r['telegram_id']}" for r in recent) or "No users"
            await q.edit_message_text(f"👤 Total Users: {n}\n\nRecent users:\n{details}",reply_markup=admin_menu()); return
        if a=="products":
            with db() as c:
                rows=c.execute("SELECT id,course,stream,semester,subject,price,file_id,active FROM products ORDER BY id DESC LIMIT 50").fetchall()
            text="📚 Products:\n\n" + ("\n".join(f"#{r['id']} {r['course']} {r['stream']} Sem{r['semester']} — {r['subject']} — ⭐{r['price']} — {'PDF' if r['file_id'] else 'NO PDF'}" for r in rows) or "No products")
            await q.edit_message_text(text[:4000],reply_markup=admin_menu()); return
        if a=="catalog":
            with db() as c:
                rows=c.execute("SELECT course,stream,semester,subject FROM subject_catalog WHERE active=1 ORDER BY course,stream,semester,subject LIMIT 100").fetchall()
            text="🧾 Subject Catalog\n\n"+("\n".join(f"{r['course']} {r['stream']} Sem{r['semester']} — {r['subject']}" for r in rows) or "No subjects")
            await q.edit_message_text(text[:4000],reply_markup=admin_menu()); return
        if a=="approvals":
            with db() as c:
                orders=c.execute("SELECT o.id,o.price,p.subject FROM orders o JOIN products p ON p.id=o.product_id WHERE o.status='PAYMENT_REPORTED' ORDER BY o.id").fetchall()
                bundles=c.execute("SELECT id,price,semester FROM bundle_orders WHERE status='PAYMENT_REPORTED' ORDER BY id").fetchall()
            kb=[]
            for r in orders: kb.append([InlineKeyboardButton(f"📚 #{r['id']} {r['subject']} ₹{r['price']}",callback_data=f"approveorder:{r['id']}")])
            for r in bundles: kb.append([InlineKeyboardButton(f"📦 Pack #{r['id']} Sem{r['semester']} ₹{r['price']}",callback_data=f"approvebundle:{r['id']}")])
            kb.append([InlineKeyboardButton("⬅️ Admin Panel",callback_data="adm:home")])
            await q.edit_message_text("🟠 Approval Queue\\n\\nPayment verify करके order चुनें:",reply_markup=InlineKeyboardMarkup(kb)); return
        if a=="orders":
            with db() as c:
                rows=c.execute("SELECT status,COUNT(*) n FROM orders GROUP BY status").fetchall()
                latest=c.execute("SELECT id,telegram_id,price,status,created_at FROM orders ORDER BY id DESC LIMIT 10").fetchall()
            text="📦 Orders\n" + ("\n".join(f"{r['status']}: {r['n']}" for r in rows) or "No orders")
            text+="\n\nRecent:\n"+"\n".join(f"#{r['id']} user {r['telegram_id']} ⭐{r['price']} {r['status']} {r['created_at'][:10]}" for r in latest)
            kb=[
                [InlineKeyboardButton("🟡 Pending",callback_data="admfilter:pending"), InlineKeyboardButton("🟢 Paid",callback_data="admfilter:paid")],
                [InlineKeyboardButton("🔴 Failed",callback_data="admfilter:failed"), InlineKeyboardButton("📅 Today",callback_data="admfilter:today")],
                [InlineKeyboardButton("📆 This Month",callback_data="admfilter:month")],
                [InlineKeyboardButton("⬅️ Admin Panel",callback_data="adm:home")]
            ]
            await q.edit_message_text(text[:4000],reply_markup=InlineKeyboardMarkup(kb)); return
        if a=="sales":
            today=date.today().isoformat()
            month=date.today().strftime("%Y-%m")
            with db() as c:
                total=c.execute("SELECT COALESCE(SUM(price),0) FROM orders WHERE status='PAID'").fetchone()[0]
                bundle_total=c.execute("SELECT COALESCE(SUM(price),0) FROM bundle_orders WHERE status='PAID'").fetchone()[0]
                td=c.execute("SELECT COALESCE(SUM(price),0) FROM orders WHERE status='PAID' AND substr(created_at,1,10)=?",(today,)).fetchone()[0]
                td+=c.execute("SELECT COALESCE(SUM(price),0) FROM bundle_orders WHERE status='PAID' AND substr(created_at,1,10)=?",(today,)).fetchone()[0]
                mo=c.execute("SELECT COALESCE(SUM(price),0) FROM orders WHERE status='PAID' AND substr(created_at,1,7)=?",(month,)).fetchone()[0]
                mo+=c.execute("SELECT COALESCE(SUM(price),0) FROM bundle_orders WHERE status='PAID' AND substr(created_at,1,7)=?",(month,)).fetchone()[0]
                cnt=c.execute("SELECT COUNT(*) FROM orders WHERE status='PAID'").fetchone()[0]
                bcnt=c.execute("SELECT COUNT(*) FROM bundle_orders WHERE status='PAID'").fetchone()[0]
                top=c.execute("""SELECT p.subject,COUNT(*) n FROM orders o JOIN products p ON p.id=o.product_id
                                 WHERE o.status='PAID' GROUP BY o.product_id ORDER BY n DESC LIMIT 5""").fetchall()
            top_text="\n".join(f"• {r['subject']} — {r['n']} sales" for r in top) or "No sales yet"
            await q.edit_message_text(
                f"📊 Sales\nPaid Orders: {cnt} + {bcnt} bundles\nTotal Sales: ₹{total+bundle_total}\nToday: ₹{td}\nThis Month: ₹{mo}\n\n🔥 Top-selling Notes\n{top_text}",
                reply_markup=admin_menu()); return
        if a=="settings":
            await q.edit_message_text(f"⚙️ Settings\nAdmin ID: {ADMIN_CHAT_ID}\nAdmin Username: {ADMIN_USERNAME or 'not set'}\nDatabase: {DB_PATH}\nBundle Discount: {BUNDLE_DISCOUNT_PERCENT}%",reply_markup=admin_menu()); return

async def error_handler(update,context):
    log.exception("Unhandled error",exc_info=context.error)

async def menu_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("📚 Main Menu", reply_markup=main_menu())

async def courses_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("🎓 Course चुनें:", reply_markup=main_menu())

async def purchases_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    with db() as c:
        rows=c.execute("""SELECT o.id,p.subject,p.price
                          FROM orders o JOIN products p ON p.id=o.product_id
                          WHERE o.telegram_id=? AND o.status='PAID' AND o.delivered=1
                          ORDER BY o.id DESC LIMIT 20""",(update.effective_user.id,)).fetchall()
    if not rows:
        await update.message.reply_text("🛒 आपकी अभी कोई खरीदारी नहीं है।", reply_markup=main_menu())
        return
    text="🛒 आपकी Purchases:\n\n" + "\n".join(f"📖 {r['subject']} — ⭐ {r['price']}" for r in rows)
    await update.message.reply_text(text, reply_markup=main_menu())

async def latest_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    with db() as c:
        rows=c.execute("SELECT * FROM products WHERE active=1 AND file_id<>'' ORDER BY id DESC LIMIT 12").fetchall()
    kb=[[InlineKeyboardButton(f"{r['subject']} • ⭐{r['price']}",callback_data=f"prod:{r['id']}")] for r in rows]
    kb.append([InlineKeyboardButton("⬅️ Main Menu",callback_data="home")])
    await update.message.reply_text("🆕 Latest Notes" if rows else "🆕 अभी कोई Notes available नहीं हैं.",reply_markup=InlineKeyboardMarkup(kb))

async def featured_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    with db() as c:
        rows=c.execute("""SELECT p.*,COUNT(o.id) sales FROM products p
                          LEFT JOIN orders o ON o.product_id=p.id AND o.status='PAID'
                          WHERE p.active=1 AND p.file_id<>'' GROUP BY p.id
                          ORDER BY sales DESC,p.id DESC LIMIT 12""").fetchall()
    kb=[[InlineKeyboardButton(f"⭐ {r['subject']} • {r['price']}",callback_data=f"prod:{r['id']}")] for r in rows]
    kb.append([InlineKeyboardButton("⬅️ Main Menu",callback_data="home")])
    await update.message.reply_text("⭐ Featured Notes",reply_markup=InlineKeyboardMarkup(kb))

async def request_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb=[]
    if ADMIN_USERNAME:
        kb.append([InlineKeyboardButton("📩 Admin को Subject बताएं",url=f"https://t.me/{ADMIN_USERNAME.lstrip('@')}")])
    kb.append([InlineKeyboardButton("⬅️ Main Menu",callback_data="home")])
    await update.message.reply_text("📩 जिस Subject के Notes चाहिए, Admin को Course + Semester + Subject भेजें.",reply_markup=InlineKeyboardMarkup(kb))

async def terms_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📜 Terms & Conditions\n\n"
        "• Notes/PDF की digital delivery payment successful होने के बाद Telegram पर की जाती है।\n"
        "• Purchase से पहले product details और price ध्यान से जांचें।\n"
        "• Payment या delivery समस्या के लिए /paysupport से Admin support लें।\n"
        "• Unauthorized redistribution/sharing से बचें।"
    )

async def paysupport_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if ADMIN_USERNAME:
        await update.message.reply_text(
            "💬 Payment Support\n\n"
            "Payment, duplicate charge या PDF delivery से जुड़ी समस्या के लिए Admin से संपर्क करें।",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📩 Admin Support",url=f"https://t.me/{ADMIN_USERNAME.lstrip('@')}")]
            ])
        )
    else:
        await update.message.reply_text(
            "💬 Payment Support\n\nAdmin username अभी configure नहीं है।"
        )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "❓ RU Notes Store — Help\n\n"
        "Course → Semester → Subject चुनें।\n"
        "Available PDF खोलें → Buy Now → Payment Page पर UPI QR/UPI ID से payment करें।\n"
        "Payment करने के बाद “मैंने Payment कर दिया” दबाएँ। Admin UPI transaction verify करेगा और approval के बाद PDF Telegram में भेजी जाएगी.",
        reply_markup=main_menu())

async def post_init(app):
    await app.bot.set_my_commands([
        BotCommand("menu", "🏠 Main Menu"),
        BotCommand("courses", "🎓 Courses"),
        BotCommand("purchases", "🛒 My Purchases"),
        BotCommand("latest", "🆕 Latest Notes"),
        BotCommand("featured", "⭐ Featured"),
        BotCommand("request", "📩 Request Notes"),
        BotCommand("help", "❓ Help"),
        BotCommand("terms", "📜 Terms & Conditions"),
        BotCommand("paysupport", "💬 Payment Support"),
    ])
    await app.bot.set_chat_menu_button(menu_button=MenuButtonCommands())

def main():
    if not BOT_TOKEN: raise RuntimeError("BOT_TOKEN is missing")
    db().close()
    app=Application.builder().token(BOT_TOKEN).post_init(post_init).build()
    app.add_handler(CommandHandler("start",start))
    app.add_handler(CommandHandler("menu",menu_command))
    app.add_handler(CommandHandler("courses",courses_command))
    app.add_handler(CommandHandler("purchases",purchases_command))
    app.add_handler(CommandHandler("latest",latest_command))
    app.add_handler(CommandHandler("featured",featured_command))
    app.add_handler(CommandHandler("request",request_command))
    app.add_handler(CommandHandler("help",help_command))
    app.add_handler(CommandHandler("terms",terms_command))
    app.add_handler(CommandHandler("paysupport",paysupport_command))
    app.add_handler(CommandHandler("admin",admin))
    app.add_handler(CommandHandler("cancel",cancel))
    app.add_handler(CallbackQueryHandler(callback))
    app.add_handler(MessageHandler(filters.Document.ALL,admin_document))
    # Admin text handler must come before the generic student text handler.
    # In python-telegram-bot, the first matching handler in a group handles the update.
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND,admin_text))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND,user_text))
    app.add_error_handler(error_handler)
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__=="__main__":
    main()

import os
import sqlite3
import logging
from datetime import datetime, timezone
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, LabeledPrice
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, ContextTypes, PreCheckoutQueryHandler, filters

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
ADMIN_CHAT_ID = int(os.getenv("ADMIN_CHAT_ID", "0"))
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "")
DB_PATH = os.getenv("DATABASE_PATH", "bot.db")

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("ru-notes-bot")

COURSES = {
    "BSC": "🎓 B.Sc.",
    "BCOM": "📗 B.Com",
    "MCOM": "📙 M.Com",
    "MSC": "🔬 M.Sc",
    "MA": "📕 M.A",
    "BA": "📘 B.A",
}
STREAMS = {"PCM": "PCM", "PCB": "PCB"}
SEMESTERS = {str(i): f"Semester {i}" for i in range(1, 7)}

def db():
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    c.execute("""CREATE TABLE IF NOT EXISTS users(
        telegram_id INTEGER PRIMARY KEY, username TEXT, first_name TEXT,
        created_at TEXT NOT NULL)""")
    c.execute("""CREATE TABLE IF NOT EXISTS products(
        id INTEGER PRIMARY KEY AUTOINCREMENT, course TEXT NOT NULL,
        stream TEXT DEFAULT '', semester TEXT NOT NULL, subject TEXT NOT NULL,
        price INTEGER NOT NULL DEFAULT 0, file_id TEXT DEFAULT '',
        active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL,
        UNIQUE(course,stream,semester,subject))""")
    c.execute("""CREATE TABLE IF NOT EXISTS orders(
        id INTEGER PRIMARY KEY AUTOINCREMENT, telegram_id INTEGER NOT NULL,
        product_id INTEGER NOT NULL, payload TEXT NOT NULL, price INTEGER NOT NULL,
        status TEXT NOT NULL, telegram_charge_id TEXT DEFAULT '',
        created_at TEXT NOT NULL, delivered INTEGER NOT NULL DEFAULT 0)""")
    c.commit()
    return c

def now():
    return datetime.now(timezone.utc).isoformat()

def menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🎓 B.Sc.", callback_data="course:BSC"),
         InlineKeyboardButton("📗 B.Com", callback_data="course:BCOM")],
        [InlineKeyboardButton("📙 M.Com", callback_data="course:MCOM"),
         InlineKeyboardButton("🔬 M.Sc", callback_data="course:MSC")],
        [InlineKeyboardButton("📕 M.A", callback_data="course:MA"),
         InlineKeyboardButton("📘 B.A", callback_data="course:BA")],
        [InlineKeyboardButton("🛒 My Purchases", callback_data="purchases")]
    ])

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    with db() as c:
        c.execute("INSERT OR REPLACE INTO users(telegram_id,username,first_name,created_at) VALUES(?,?,?,COALESCE((SELECT created_at FROM users WHERE telegram_id=?),?))",
                  (u.id,u.username or "",u.first_name or "",u.id,now()))
    await update.message.reply_text(
        "नमस्ते! 📚\nयहाँ आप अपने Course, Semester और Subject के अनुसार Notes/PDF खरीद सकते हैं।\nनीचे अपना Course चुनें।",
        reply_markup=menu())

async def admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_CHAT_ID:
        await update.message.reply_text("यह command केवल Admin के लिए है।")
        return
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("👤 Users",callback_data="adm:users"),
         InlineKeyboardButton("📚 Products / Notes",callback_data="adm:products")],
        [InlineKeyboardButton("➕ Add Subject",callback_data="adm:add"),
         InlineKeyboardButton("📄 Upload PDF",callback_data="adm:upload")],
        [InlineKeyboardButton("💰 Change Price",callback_data="adm:price"),
         InlineKeyboardButton("📦 Orders",callback_data="adm:orders")],
        [InlineKeyboardButton("📊 Sales",callback_data="adm:sales"),
         InlineKeyboardButton("📢 Broadcast",callback_data="adm:broadcast")],
        [InlineKeyboardButton("⚙️ Settings",callback_data="adm:settings")]
    ])
    await update.message.reply_text("🔐 Admin Panel",reply_markup=kb)

async def choose_course(q, course):
    if course == "BSC":
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("PCM",callback_data="stream:BSC:PCM"),
             InlineKeyboardButton("PCB",callback_data="stream:BSC:PCB")],
            [InlineKeyboardButton("⬅️ Back",callback_data="home")]
        ])
        await q.edit_message_text("B.Sc. Stream चुनें:",reply_markup=kb)
    else:
        await choose_semester(q, course, "")

async def choose_semester(q, course, stream):
    rows = []
    for i in range(1,7):
        rows.append([InlineKeyboardButton(f"Semester {i}",callback_data=f"sem:{course}:{stream}:{i}")])
    rows.append([InlineKeyboardButton("⬅️ Back",callback_data=f"backcourse:{course}")])
    await q.edit_message_text("Semester चुनें:",reply_markup=InlineKeyboardMarkup(rows))

async def subjects(q, course, stream, sem):
    with db() as c:
        rows = c.execute("SELECT * FROM products WHERE course=? AND stream=? AND semester=? AND active=1 ORDER BY subject",
                         (course,stream,sem)).fetchall()
    if not rows:
        text = "📚 इस Semester के Notes अभी उपलब्ध नहीं हैं।\nयदि आपको Notes चाहिए तो Admin से संपर्क करें।"
        kb = [[InlineKeyboardButton("📩 Admin को Message करें",url=f"https://t.me/{ADMIN_USERNAME.lstrip('@')}")] if ADMIN_USERNAME else []]
    else:
        text = "📚 Subject चुनें:"
        kb = [[InlineKeyboardButton(r["subject"],callback_data=f"prod:{r['id']}")] for r in rows]
        kb.append([InlineKeyboardButton("⬅️ Back",callback_data=f"streamback:{course}:{stream}")])
    await q.edit_message_text(text,reply_markup=InlineKeyboardMarkup(kb))

async def product(q, pid):
    with db() as c:
        r = c.execute("SELECT * FROM products WHERE id=? AND active=1",(pid,)).fetchone()
    if not r:
        await q.answer("यह Notes उपलब्ध नहीं है।",show_alert=True); return
    stream = f" / {r['stream']}" if r["stream"] else ""
    text = f"📚 {r['course']}{stream}\n📖 Semester {r['semester']}\n📝 {r['subject']}\n💰 Price: ⭐ {r['price']} Stars\n📄 PDF उपलब्ध"
    kb = [[InlineKeyboardButton("💳 Buy Now",callback_data=f"buy:{pid}")],
          [InlineKeyboardButton("📩 Admin से संपर्क करें",url=f"https://t.me/{ADMIN_USERNAME.lstrip('@')}")] if ADMIN_USERNAME else [],
          [InlineKeyboardButton("⬅️ Back",callback_data=f"semback:{r['course']}:{r['stream']}:{r['semester']}")]]
    await q.edit_message_text(text,reply_markup=InlineKeyboardMarkup([x for x in kb if x]))

async def buy(q, pid):
    with db() as c:
        r = c.execute("SELECT * FROM products WHERE id=? AND active=1 AND file_id<>''",(pid,)).fetchone()
        if not r:
            await q.answer("PDF अभी उपलब्ध नहीं है।",show_alert=True); return
        payload = f"RU_NOTE:{q.from_user.id}:{pid}:{int(datetime.now().timestamp())}"
        c.execute("INSERT INTO orders(telegram_id,product_id,payload,price,status,created_at) VALUES(?,?,?,?,?,?)",
                  (q.from_user.id,pid,payload,r["price"],"PENDING",now()))
        order_id = c.lastrowid
    await q.message.reply_invoice(
        title=r["subject"][:32],
        description=f"Rajasthan University Notes - {r['subject']}"[:255],
        payload=payload,
        currency="XTR",
        prices=[LabeledPrice(r["subject"][:32], r["price"])],
        provider_token="",
        start_parameter=f"ru-note-{order_id}",
    )

async def precheckout(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.pre_checkout_query
    if not q.invoice_payload.startswith("RU_NOTE:"):
        await q.answer(ok=False,error_message="Invalid payment.")
        return
    with db() as c:
        r = c.execute("SELECT id FROM orders WHERE payload=? AND telegram_id=? AND status='PENDING'",
                      (q.invoice_payload,q.from_user.id)).fetchone()
    await q.answer(ok=bool(r), error_message=None if r else "Order not found.")

async def successful(update: Update, context: ContextTypes.DEFAULT_TYPE):
    sp = update.message.successful_payment
    with db() as c:
        order = c.execute("SELECT * FROM orders WHERE payload=? AND telegram_id=?",
                          (sp.invoice_payload,update.effective_user.id)).fetchone()
        if not order: return
        c.execute("UPDATE orders SET status='PAID',telegram_charge_id=? WHERE id=?",
                  (sp.telegram_payment_charge_id,order["id"]))
        p = c.execute("SELECT * FROM products WHERE id=?",(order["product_id"],)).fetchone()
        if not p or not p["file_id"]: return
        await update.message.reply_document(p["file_id"],caption=f"📚 {p['subject']}\nधन्यवाद! आपका PDF यहाँ है।")
        c.execute("UPDATE orders SET delivered=1 WHERE id=?",(order["id"],))
        
async def callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    d = q.data
    if d == "home":
        await q.edit_message_text("Course चुनें:",reply_markup=menu()); return
    if d.startswith("course:"):
        await choose_course(q,d.split(":")[1]); return
    if d.startswith("stream:"):
        _,course,stream=d.split(":"); await choose_semester(q,course,stream); return
    if d.startswith("sem:"):
        _,course,stream,sem=d.split(":"); await subjects(q,course,stream,sem); return
    if d.startswith("prod:"):
        await product(q,int(d.split(":")[1])); return
    if d.startswith("buy:"):
        await buy(q,int(d.split(":")[1])); return
    if d == "purchases":
        with db() as c:
            rows=c.execute("""SELECT p.subject,p.course,p.stream,p.semester,o.created_at
                              FROM orders o JOIN products p ON p.id=o.product_id
                              WHERE o.telegram_id=? AND o.status='PAID' AND o.delivered=1
                              ORDER BY o.id DESC""",(q.from_user.id,)).fetchall()
        if not rows:
            await q.edit_message_text("🛒 आपकी अभी कोई खरीदारी नहीं है।",reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Main Menu",callback_data="home")]]))
            return
        text="🛒 आपकी Purchases:\n\n"
        for r in rows[:20]:
            text += f"📖 {r['subject']} — {r['course']} {r['stream']} Sem {r['semester']}\n"
        await q.edit_message_text(text,reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Main Menu",callback_data="home")]]))
        return
    if d.startswith("adm:"):
        if q.from_user.id != ADMIN_CHAT_ID: return
        action=d.split(":")[1]
        with db() as c:
            if action=="users":
                n=c.execute("SELECT COUNT(*) FROM users").fetchone()[0]
                await q.edit_message_text(f"👤 Total Users: {n}")
            elif action=="products":
                n=c.execute("SELECT COUNT(*) FROM products").fetchone()[0]
                await q.edit_message_text(f"📚 Total Products: {n}\n\nAdvanced product management UI अगला चरण है।")
            elif action=="orders":
                rows=c.execute("SELECT status,COUNT(*) n FROM orders GROUP BY status").fetchall()
                await q.edit_message_text("📦 Orders\n"+"\n".join(f"{r['status']}: {r['n']}" for r in rows) or "No orders")
            elif action=="sales":
                total=c.execute("SELECT COALESCE(SUM(price),0) FROM orders WHERE status='PAID'").fetchone()[0]
                await q.edit_message_text(f"📊 Total Sales: ⭐ {total}")
            else:
                await q.edit_message_text(f"⚙️ {action.title()} module तैयार किया जा रहा है।")
        return

async def error_handler(update, context):
    log.exception("Unhandled error", exc_info=context.error)

def main():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN is missing")
    db().close()
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start",start))
    app.add_handler(CommandHandler("admin",admin))
    app.add_handler(CallbackQueryHandler(callback))
    app.add_handler(PreCheckoutQueryHandler(precheckout))
    app.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT,successful))
    app.add_error_handler(error_handler)
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()

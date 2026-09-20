import os
import sqlite3
import logging
from datetime import datetime, timezone, date
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, LabeledPrice, BotCommand, MenuButtonCommands
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, MessageHandler,
    ContextTypes, PreCheckoutQueryHandler, filters
)

load_dotenv()
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("BOT_TOKEN", "")
CHANNEL_USERNAME = os.getenv("CHANNEL_USERNAME", "")
ADMIN_CHAT_ID = int(os.getenv("ADMIN_CHAT_ID", "0"))
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "")
DB_PATH = os.getenv("DATABASE_PATH", "bot.db")
BUNDLE_DISCOUNT_PERCENT = max(0, min(90, int(os.getenv("BUNDLE_DISCOUNT_PERCENT", "20"))))

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
        file_id TEXT DEFAULT '', active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL,
        UNIQUE(course,stream,semester,subject))""")
    c.execute("""CREATE TABLE IF NOT EXISTS orders(
        id INTEGER PRIMARY KEY AUTOINCREMENT, telegram_id INTEGER NOT NULL, product_id INTEGER NOT NULL,
        payload TEXT NOT NULL, price INTEGER NOT NULL, status TEXT NOT NULL,
        telegram_charge_id TEXT DEFAULT '', created_at TEXT NOT NULL, delivered INTEGER NOT NULL DEFAULT 0)""")
    c.execute("""CREATE TABLE IF NOT EXISTS subject_catalog(
        id INTEGER PRIMARY KEY AUTOINCREMENT, course TEXT NOT NULL, stream TEXT DEFAULT '',
        semester INTEGER NOT NULL, subject TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1,
        UNIQUE(course,stream,semester,subject))""")
    c.execute("""CREATE TABLE IF NOT EXISTS bundle_orders(
        id INTEGER PRIMARY KEY AUTOINCREMENT, telegram_id INTEGER NOT NULL,
        course TEXT NOT NULL, stream TEXT DEFAULT '', semester INTEGER NOT NULL,
        payload TEXT NOT NULL, price INTEGER NOT NULL, original_price INTEGER NOT NULL,
        discount_percent INTEGER NOT NULL, status TEXT NOT NULL,
        telegram_charge_id TEXT DEFAULT '', created_at TEXT NOT NULL, delivered INTEGER NOT NULL DEFAULT 0)""")
    seed_catalog(c)
    c.commit()
    return c

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
        [InlineKeyboardButton("👤 Users",callback_data="adm:users"), InlineKeyboardButton("📚 Products / Notes",callback_data="adm:products")],
        [InlineKeyboardButton("➕ Add Subject",callback_data="adm:add"), InlineKeyboardButton("🧾 Subject Catalog",callback_data="adm:catalog")],
        [InlineKeyboardButton("📄 Upload PDF",callback_data="adm:upload"), InlineKeyboardButton("💰 Change Price",callback_data="adm:price")],
        [InlineKeyboardButton("📦 Orders",callback_data="adm:orders"), InlineKeyboardButton("📊 Sales",callback_data="adm:sales")],
        [InlineKeyboardButton("📢 Broadcast",callback_data="adm:broadcast"), InlineKeyboardButton("⚙️ Settings",callback_data="adm:settings")]
    ])

def admin_only(user_id):
    return user_id == ADMIN_CHAT_ID

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    with db() as c:
        c.execute("""INSERT OR REPLACE INTO users(telegram_id,username,first_name,created_at)
                     VALUES(?,?,?,COALESCE((SELECT created_at FROM users WHERE telegram_id=?),?))""",
                  (u.id,u.username or "",u.first_name or "",u.id,now()))
    context.user_data.clear()
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
    text = f"📚 {COURSES.get(r['course'],r['course'])}{stream}\n📖 Semester {r['semester']}\n📝 {r['subject']}\n💰 Price: ⭐ {r['price']} Stars\n📄 PDF available"
    kb = [[InlineKeyboardButton("💳 Buy Now",callback_data=f"buy:{pid}")]]
    if ADMIN_USERNAME:
        kb.append([InlineKeyboardButton("📩 Admin से संपर्क करें",url=f"https://t.me/{ADMIN_USERNAME.lstrip('@')}")])
    kb.append([InlineKeyboardButton("⬅️ Back",callback_data=f"sem:{r['course']}:{r['stream']}:{r['semester']}")])
    await q.edit_message_text(text,reply_markup=InlineKeyboardMarkup(kb))

async def buy(q, pid):
    with db() as c:
        p = c.execute("SELECT * FROM products WHERE id=? AND active=1 AND file_id<>''",(pid,)).fetchone()
        if not p:
            await q.answer("PDF अभी उपलब्ध नहीं है।",show_alert=True); return
        payload = f"RU_NOTE:{q.from_user.id}:{pid}:{int(datetime.now().timestamp())}"
        c.execute("""INSERT INTO orders(telegram_id,product_id,payload,price,status,created_at)
                     VALUES(?,?,?,?,?,?)""",(q.from_user.id,pid,payload,p["price"],"PENDING",now()))
    await q.message.reply_invoice(
        title=p["subject"][:32],
        description=f"Rajasthan University Notes - {p['subject']}"[:255],
        payload=payload, currency="XTR",
        prices=[LabeledPrice(p["subject"][:32],p["price"])],
        provider_token="", start_parameter=f"ru-note-{pid}")

async def buy_bundle(q, course, stream, sem):
    sem = int(sem)
    with db() as c:
        catalog = c.execute("""SELECT subject FROM subject_catalog
                               WHERE course=? AND stream=? AND semester=? AND active=1
                               ORDER BY subject""",(course,stream,sem)).fetchall()
        if not catalog:
            await q.answer("इस Semester की Subject list अभी उपलब्ध नहीं है।",show_alert=True); return
        names=[r["subject"] for r in catalog]
        placeholders=",".join("?" for _ in names)
        rows=c.execute(f"""SELECT * FROM products WHERE course=? AND stream=? AND semester=? AND active=1
                           AND subject IN ({placeholders})""",(course,stream,sem,*names)).fetchall()
        by={r["subject"]:r for r in rows}
        if len(by)!=len(names) or any(not by[n]["file_id"] for n in names):
            await q.answer("Complete Semester Pack अभी पूरा उपलब्ध नहीं है।",show_alert=True); return
        original=sum(by[n]["price"] for n in names)
        final=max(1,round(original*(100-BUNDLE_DISCOUNT_PERCENT)/100))
        payload=f"RU_BUNDLE:{q.from_user.id}:{course}:{stream}:{sem}:{int(datetime.now().timestamp())}"
        c.execute("""INSERT INTO bundle_orders(telegram_id,course,stream,semester,payload,price,original_price,discount_percent,status,created_at)
                     VALUES(?,?,?,?,?,?,?,?,?,?)""",(q.from_user.id,course,stream,sem,payload,final,original,BUNDLE_DISCOUNT_PERCENT,"PENDING",now()))
    await q.message.reply_invoice(title=f"{COURSES.get(course,course)} Sem {sem} Pack"[:32],
        description=f"Complete Semester Notes Pack • {BUNDLE_DISCOUNT_PERCENT}% bundle discount"[:255],
        payload=payload,currency="XTR",prices=[LabeledPrice("Complete Semester Pack",final)],
        provider_token="",start_parameter=f"ru-pack-{course.lower()}-{sem}")

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

async def admin_add_start(q, context):
    context.user_data["admin_action"]="add_course"
    await q.edit_message_text("➕ Add Subject\nCourse भेजें: BSC / BCOM / MCOM / MSC / MA / BA\n\n/cancel से रद्द करें।")

async def admin_upload_start(q, context):
    context.user_data["admin_action"]="upload_id"
    await q.edit_message_text("📄 PDF Upload\nपहले Product ID भेजें, फिर उसी के बाद PDF Document भेजें।\n\n/cancel से रद्द करें।")

async def admin_price_start(q, context):
    context.user_data["admin_action"]="price"
    await q.edit_message_text("💰 Product ID और नई price इस format में भेजें:\n123 50\n\n/cancel से रद्द करें।")

async def admin_broadcast_start(q, context):
    context.user_data["admin_action"]="broadcast"
    await q.edit_message_text("📢 अगला message सभी registered users को भेजा जाएगा।\nText/photo/document भेज सकते हैं।\n/cancel से रद्द करें।")

async def admin_text(update, context):
    if update.effective_user.id != ADMIN_CHAT_ID: return
    action=context.user_data.get("admin_action")
    if not action: return
    text=(update.message.text or "").strip()

    if action=="add_course":
        course=text.upper()
        if course not in COURSES:
            await update.message.reply_text("गलत Course. BSC/BCOM/MCOM/MSC/MA/BA भेजें।"); return
        context.user_data.update(admin_action="add_stream",course=course)
        await update.message.reply_text("Stream भेजें। केवल B.Sc. के लिए PCM या PCB; बाकी में NONE भेजें।")
        return
    if action=="add_stream":
        stream=text.upper()
        course=context.user_data["course"]
        if course=="BSC" and stream not in STREAMS:
            await update.message.reply_text("B.Sc. के लिए PCM या PCB भेजें।"); return
        if course!="BSC": stream=""
        context.user_data.update(admin_action="add_semester",stream=stream)
        await update.message.reply_text("Semester 1 से 6 में कोई एक भेजें।")
        return
    if action=="add_semester":
        if text not in [str(i) for i in range(1,7)]:
            await update.message.reply_text("Semester 1 से 6 में कोई एक भेजें।"); return
        context.user_data.update(admin_action="add_subject",semester=int(text))
        await update.message.reply_text("Subject का नाम भेजें।")
        return
    if action=="add_subject":
        context.user_data.update(admin_action="add_price",subject=text)
        await update.message.reply_text("Price Stars में भेजें। उदाहरण: 50")
        return
    if action=="add_price":
        try: price=int(text); assert price>0
        except: await update.message.reply_text("Price केवल positive number में भेजें।"); return
        d=context.user_data
        with db() as c:
            c.execute("""INSERT INTO subject_catalog(course,stream,semester,subject,active)
                         VALUES(?,?,?,?,1)
                         ON CONFLICT(course,stream,semester,subject)
                         DO UPDATE SET active=1""",
                      (d["course"],d["stream"],d["semester"],d["subject"]))
            c.execute("""INSERT INTO products(course,stream,semester,subject,price,file_id,active,created_at)
                         VALUES(?,?,?,?,?,'',1,?)
                         ON CONFLICT(course,stream,semester,subject)
                         DO UPDATE SET price=excluded.price, active=1""",
                      (d["course"],d["stream"],d["semester"],d["subject"],price,now()))
            p=c.execute("""SELECT id FROM products
                           WHERE course=? AND stream=? AND semester=? AND subject=?""",
                        (d["course"],d["stream"],d["semester"],d["subject"])).fetchone()
        context.user_data.update(admin_action="upload_pdf",product_id=int(p["id"]))
        await update.message.reply_text(
            f"✅ Subject/Product save हो गया।\nProduct ID: #{p['id']}\n\nअब इसी chat में PDF को Document के रूप में भेजें।\nPDF upload होते ही Student के लिए Buy Now और payment चालू हो जाएगा.",
            reply_markup=admin_menu())
        return
    if action=="upload_id":
        if not text.isdigit():
            await update.message.reply_text("Product ID number भेजें।"); return
        with db() as c:
            p=c.execute("SELECT id,subject FROM products WHERE id=?",(int(text),)).fetchone()
        if not p:
            await update.message.reply_text("Product ID नहीं मिला।"); return
        context.user_data.update(admin_action="upload_pdf",product_id=int(text))
        await update.message.reply_text(f"अब Product #{text} ({p['subject']}) की PDF Document के रूप में भेजें।")
        return
    if action=="price":
        parts=text.split()
        if len(parts)!=2 or not all(x.isdigit() for x in parts):
            await update.message.reply_text("Format: ProductID Price\nउदाहरण: 123 50"); return
        pid,price=map(int,parts)
        with db() as c: c.execute("UPDATE products SET price=? WHERE id=?",(price,pid))
        context.user_data.clear()
        await update.message.reply_text("✅ Price update हो गई।",reply_markup=admin_menu())
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
    if update.message.document.mime_type not in ("application/pdf","application/octet-stream") and not update.message.document.file_name.lower().endswith(".pdf"):
        await update.message.reply_text("कृपया PDF document भेजें।"); return
    with db() as c: c.execute("UPDATE products SET file_id=? WHERE id=?",(update.message.document.file_id,pid))
    context.user_data.clear()
    await update.message.reply_text(f"✅ Product #{pid} की PDF upload हो गई।",reply_markup=admin_menu())

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
        "3️⃣ Buy Now दबाकर Telegram Stars से payment करें।\n"
        "4️⃣ Payment successful होने पर PDF Telegram में automatically मिलेगा।\n\n"
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
    if d.startswith("course:"): await choose_course(q,d.split(":")[1]); return
    if d.startswith("stream:"):
        _,course,stream=d.split(":"); await choose_semester(q,course,stream); return
    if d.startswith("sem:"):
        _,course,stream,sem=d.split(":"); await subjects(q,course,stream,sem); return
    if d.startswith("prod:"): await show_product(q,int(d.split(":")[1])); return
    if d.startswith("missing:"):
        _,course,stream,sem=d.split(":"); await show_missing(q,course,stream,int(sem)); return
    if d.startswith("bundle:"):
        _,course,stream,sem=d.split(":"); await buy_bundle(q,course,stream,int(sem)); return
    if d.startswith("buy:"): await buy(q,int(d.split(":")[1])); return
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
                f"📊 Sales\nPaid Orders: {cnt} + {bcnt} bundles\nTotal Sales: ⭐ {total+bundle_total}\nToday: ⭐ {td}\nThis Month: ⭐ {mo}\n\n🔥 Top-selling Notes\n{top_text}",
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

async def post_init(app):
    await app.bot.set_my_commands([
        BotCommand("menu", "🏠 Main Menu"),
        BotCommand("courses", "🎓 Courses"),
        BotCommand("purchases", "🛒 My Purchases"),
        BotCommand("latest", "🆕 Latest Notes"),
        BotCommand("featured", "⭐ Featured"),
        BotCommand("request", "📩 Request Notes"),
        BotCommand("help", "❓ Help"),
    ])
    await app.bot.set_chat_menu_button(menu_button=MenuButtonCommands())

def main():
    if not BOT_TOKEN: raise RuntimeError("BOT_TOKEN is missing")
    db().close()
    app=Application.builder().token(BOT_TOKEN).post_init(post_init).build()
    app.add_handler(CommandHandler("start",start))
    app.add_handler(CommandHandler("menu",menu_command))
    app.add_handler(CommandHandler("courses",courses_command))
    app.add_handler(CommandHandler("admin",admin))
    app.add_handler(CommandHandler("cancel",cancel))
    app.add_handler(CallbackQueryHandler(callback))
    app.add_handler(PreCheckoutQueryHandler(precheckout))
    app.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT,successful))
    app.add_handler(MessageHandler(filters.Document.ALL,admin_document))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND,user_text))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND,admin_text))
    app.add_error_handler(error_handler)
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__=="__main__":
    main()

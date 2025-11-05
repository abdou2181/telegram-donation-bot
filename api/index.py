import logging
import os
import aiosqlite
from datetime import datetime
from flask import Flask, request, jsonify
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, LabeledPrice
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes, PreCheckoutQueryHandler
import threading
import asyncio

# ===================== CONFIG =====================
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TOKEN = os.getenv('TOKEN')
if not TOKEN:
    raise ValueError("TOKEN not set in environment")

ADMIN_USER_ID = int(os.getenv('ADMIN_USER_ID', '0'))
DB_PATH = os.path.join(os.path.dirname(__file__), '..', 'db', 'donations.db')
os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)

app = Flask(__name__)
application = Application.builder().token(TOKEN).build()

# ===================== DATABASE =====================
async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript('''
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                last_name TEXT,
                first_seen TEXT
            );
            CREATE TABLE IF NOT EXISTS donations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                amount INTEGER,
                payload TEXT,
                timestamp TEXT
            );
        ''')
        await db.commit()

# Run DB init in background
def run_init():
    asyncio.run(init_db())
threading.Thread(target=run_init, daemon=True).start()

# ===================== HELPERS =====================
async def log_user(user):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            'INSERT OR IGNORE INTO users VALUES (?,?,?,?,?)',
            (user.id, user.username or '', user.first_name or '', user.last_name or '', datetime.utcnow().isoformat())
        )
        await db.commit()

async def log_donation(user_id, amount, payload):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            'INSERT INTO donations (user_id, amount, payload, timestamp) VALUES (?,?,?,?)',
            (user_id, amount, payload, datetime.utcnow().isoformat())
        )
        await db.commit()

# ===================== HANDLERS =====================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await log_user(update.effective_user)
    keyboard = [
        [InlineKeyboardButton("1 Star", callback_data='donate_1')],
        [InlineKeyboardButton("10 Stars", callback_data='donate_10')],
        [InlineKeyboardButton("100 Stars", callback_data='donate_100')],
        [InlineKeyboardButton("Custom Amount", callback_data='custom')]
    ]
    await update.message.reply_text(
        'Choose a donation amount in Stars:',
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data.startswith('donate_'):
        amount = int(query.data.split('_')[1])
        await send_invoice(context, query.message.chat_id, amount, query.from_user.id)
    elif query.data == 'custom':
        context.user_data['waiting'] = True
        await query.edit_message_text("Reply with amount (e.g., 50):")

async def send_invoice(context, chat_id, amount, user_id):
    await context.bot.send_invoice(
        chat_id=chat_id,
        title=f"Donate {amount} Stars",
        description="Thank you for your support!",
        payload=f"donation_{amount}_{user_id}",
        provider_token="",
        currency="XTR",
        prices=[LabeledPrice("Donation", amount * 100)],
        start_parameter="donate"
    )

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data.get('waiting'):
        try:
            amount = int(update.message.text)
            if amount >= 1:
                await send_invoice(context, update.effective_chat.id, amount, update.effective_user.id)
            else:
                await update.message.reply_text("Minimum 1 Star.")
            context.user_data['waiting'] = False
        except:
            await update.message.reply_text("Send a number.")

async def precheckout(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.pre_checkout_query
    await context.bot.answer_pre_checkout_query(pre_checkout_query_id=query.id, ok=True)

async def success(update: Update, context: ContextTypes.DEFAULT_TYPE):
    payment = update.message.successful_payment
    amount = payment.total_amount // 100
    user_id = update.effective_user.id
    payload = payment.invoice_payload
    await log_donation(user_id, amount, payload)
    await update.message.reply_text(f"Thank you for donating {amount} Stars!")

async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_USER_ID:
        await update.message.reply_text("Admin only.")
        return
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT COUNT(*), COALESCE(SUM(amount),0) FROM donations") as cur:
            d_count, d_sum = await cur.fetchone()
        async with db.execute("SELECT COUNT(*) FROM users") as cur:
            u_count = (await cur.fetchone())[0]
    await update.message.reply_text(
        f"Users: {u_count}\nTotal Stars: {d_sum}\nDonations: {d_count}"
    )

# Register handlers
application.add_handler(CommandHandler("start", start))
application.add_handler(CallbackQueryHandler(button))
application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
application.add_handler(PreCheckoutQueryHandler(precheckout))
application.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, success))
application.add_handler(CommandHandler("stats", stats))

# ===================== WEBHOOK (SYNC) =====================
@app.route(f'/webhook/{TOKEN}', methods=['POST'])
def webhook():
    try:
        json_data = request.get_json()
        if not json_data:
            return 'No JSON', 400
        update = Update.de_json(json_data, application.bot)
        # Run async handler in existing loop
        loop = asyncio.get_event_loop()
        loop.create_task(application.process_update(update))
        return 'OK'
    except Exception as e:
        logger.error(f"Webhook error: {e}")
        return 'Error', 500

@app.route('/')
def health():
    return 'Bot is running!'

# ===================== STARTUP =====================
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))

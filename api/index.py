import logging
import os
import sqlite3
from datetime import datetime
from flask import Flask, request, jsonify
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, LabeledPrice
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes, PreCheckoutQueryHandler
import threading

# ===================== CONFIG =====================
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TOKEN = os.getenv('TOKEN')
if not TOKEN:
    raise RuntimeError("ERROR: Set TOKEN in Vercel Environment Variables")

ADMIN_USER_ID = int(os.getenv('ADMIN_USER_ID', '0'))
DB_PATH = os.path.join(os.path.dirname(__file__), '..', 'db', 'donations.db')
os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)

app = Flask(__name__)
application = Application.builder().token(TOKEN).build()

# ===================== SYNC DB (NO ASYNC!) =====================
def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            last_name TEXT,
            first_seen TEXT
        )
    ''')
    conn.execute('''
        CREATE TABLE IF NOT EXISTS donations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            amount INTEGER,
            payload TEXT,
            timestamp TEXT
        )
    ''')
    conn.commit()
    conn.close()

def log_user(user):
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        'INSERT OR IGNORE INTO users VALUES (?,?,?,?,?)',
        (user.id, user.username or '', user.first_name or '', user.last_name or '', datetime.utcnow().isoformat())
    )
    conn.commit()
    conn.close()

def log_donation(user_id, amount, payload):
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        'INSERT INTO donations (user_id, amount, payload, timestamp) VALUES (?,?,?,?)',
        (user_id, amount, payload, datetime.utcnow().isoformat())
    )
    conn.commit()
    conn.close()

# Run DB init
threading.Thread(target=init_db, daemon=True).start()

# ===================== BOT HANDLERS (SYNC WRAPPERS) =====================
def run_async(coro):
    """Run async function in background"""
    def wrapper():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(coro)
    threading.Thread(target=wrapper, daemon=True).start()

async def start_async(update: Update, context: ContextTypes.DEFAULT_TYPE):
    log_user(update.effective_user)
    keyboard = [
        [InlineKeyboardButton("1 Star", callback_data='donate_1')],
        [InlineKeyboardButton("10 Stars", callback_data='donate_10')],
        [InlineKeyboardButton("100 Stars", callback_data='donate_100')],
        [InlineKeyboardButton("Custom Amount", callback_data='custom')]
    ]
    await update.message.reply_text(
        'Choose donation:',
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def button_async(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data.startswith('donate_'):
        amount = int(query.data.split('_')[1])
        await send_invoice(context, query.message.chat_id, amount, query.from_user.id)
    elif query.data == 'custom':
        context.user_data['wait'] = True
        await query.edit_message_text("Send amount (e.g., 50):")

async def send_invoice(context, chat_id, amount, user_id):
    await context.bot.send_invoice(
        chat_id=chat_id,
        title=f"Donate {amount} Stars",
        description="Thanks for support!",
        payload=f"don_{amount}_{user_id}",
        provider_token="",
        currency="XTR",
        prices=[LabeledPrice("Donation", amount * 100)],
        start_parameter="donate"
    )

async def text_async(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data.get('wait'):
        try:
            amount = int(update.message.text)
            if amount >= 1:
                await send_invoice(context, update.effective_chat.id, amount, update.effective_user.id)
            context.user_data['wait'] = False
        except:
            await update.message.reply_text("Send a number.")

async def precheckout_async(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await context.bot.answer_pre_checkout_query(update.pre_checkout_query.id, ok=True)

async def success_async(update: Update, context: ContextTypes.DEFAULT_TYPE):
    payment = update.message.successful_payment
    amount = payment.total_amount // 100
    log_donation(update.effective_user.id, amount, payment.invoice_payload)
    await update.message.reply_text(f"Thank you for {amount} Stars!")

async def stats_async(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_USER_ID:
        return
    conn = sqlite3.connect(DB_PATH)
    d_count, d_sum = conn.execute("SELECT COUNT(*), COALESCE(SUM(amount),0) FROM donations").fetchone()
    u_count = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    conn.close()
    await update.message.reply_text(f"Users: {u_count}\nStars: {d_sum}\nDonations: {d_count}")

# Register
application.add_handler(CommandHandler("start", lambda u, c: run_async(start_async(u, c))))
application.add_handler(CallbackQueryHandler(lambda u, c: run_async(button_async(u, c))))
application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, lambda u, c: run_async(text_async(u, c))))
application.add_handler(PreCheckoutQueryHandler(lambda u, c: run_async(precheckout_async(u, c))))
application.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, lambda u, c: run_async(success_async(u, c))))
application.add_handler(CommandHandler("stats", lambda u, c: run_async(stats_async(u, c))))

# ===================== WEBHOOK (SYNC) =====================
@app.route(f'/webhook/{TOKEN}', methods=['POST'])
def webhook():
    try:
        update = Update.de_json(request.get_json(), application.bot)
        run_async(application.process_update(update))
        return 'OK'
    except Exception as e:
        logger.error(f"Error: {e}")
        return 'Error', 500

@app.route('/')
def health():
    return 'Bot is alive!'

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))

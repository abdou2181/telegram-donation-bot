import logging
import os
import sqlite3
from datetime import datetime
from flask import Flask, request
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, LabeledPrice
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes, PreCheckoutQueryHandler

# ===================== CONFIG =====================
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TOKEN = os.getenv('TOKEN')
if not TOKEN:
    raise RuntimeError("TOKEN missing! Set in Vercel env.")

ADMIN_USER_ID = int(os.getenv('ADMIN_USER_ID', '0'))
DB_PATH = os.path.join(os.path.dirname(__file__), '..', 'db', 'donations.db')
os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)

app = Flask(__name__)
application = Application.builder().token(TOKEN).build()

# ===================== SYNC DB =====================
def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.executescript('''
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

# ===================== BOT HANDLERS =====================
def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    log_user(update.effective_user)
    keyboard = [
        [InlineKeyboardButton("1 Star", callback_data='donate_1')],
        [InlineKeyboardButton("10 Stars", callback_data='donate_10')],
        [InlineKeyboardButton("100 Stars", callback_data='donate_100')],
        [InlineKeyboardButton("Custom Amount", callback_data='custom')]
    ]
    update.message.reply_text(
        'Choose donation amount:',
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    query.answer()
    if query.data.startswith('donate_'):
        amount = int(query.data.split('_')[1])
        send_invoice(context, query.message.chat_id, amount, query.from_user.id)
    elif query.data == 'custom':
        context.user_data['wait'] = True
        query.edit_message_text("Send amount (e.g., 50):")

def send_invoice(context, chat_id, amount, user_id):
    context.bot.send_invoice(
        chat_id=chat_id,
        title=f"Donate {amount} Stars",
        description="Thank you!",
        payload=f"don_{amount}_{user_id}",
        provider_token="",
        currency="XTR",
        prices=[LabeledPrice("Donation", amount * 100)],
        start_parameter="donate"
    )

def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data.get('wait'):
        try:
            amount = int(update.message.text)
            if amount >= 1:
                send_invoice(context, update.effective_chat.id, amount, update.effective_user.id)
            context.user_data['wait'] = False
        except:
            update.message.reply_text("Invalid number.")

def precheckout(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.bot.answer_pre_checkout_query(update.pre_checkout_query.id, ok=True)

def success(update: Update, context: ContextTypes.DEFAULT_TYPE):
    payment = update.message.successful_payment
    amount = payment.total_amount // 100
    log_donation(update.effective_user.id, amount, payment.invoice_payload)
    update.message.reply_text(f"Thank you for {amount} Stars!")

def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_USER_ID:
        return
    conn = sqlite3.connect(DB_PATH)
    d_count, d_sum = conn.execute("SELECT COUNT(*), COALESCE(SUM(amount),0) FROM donations").fetchone()
    u_count = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    conn.close()
    update.message.reply_text(f"Users: {u_count}\nStars: {d_sum}\nDonations: {d_count}")

# Register handlers
application.add_handler(CommandHandler("start", start))
application.add_handler(CallbackQueryHandler(button))
application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
application.add_handler(PreCheckoutQueryHandler(precheckout))
application.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, success))
application.add_handler(CommandHandler("stats", stats))

# ===================== WEBHOOK =====================
@app.route(f'/webhook/{TOKEN}', methods=['POST'])
def webhook():
    try:
        update = Update.de_json(request.get_json(), application.bot)
        application.process_update(update)
        return 'OK'
    except Exception as e:
        logger.error(f"Error: {e}")
        return 'Error', 500

@app.route('/')
def health():
    return 'Bot alive!'

# ===================== STARTUP =====================
init_db()  # Run sync

import logging
import os
import aiosqlite
from datetime import datetime
from flask import Flask, request, abort
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup,
    LabeledPrice
)
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, filters, ContextTypes,
    PreCheckoutQueryHandler
)

# -------------------------------------------------
# CONFIG
# -------------------------------------------------
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

TOKEN = os.getenv('TOKEN')
ADMIN_USER_ID = int(os.getenv('ADMIN_USER_ID', '0'))

DB_PATH = os.path.join(os.path.dirname(__file__), '..', 'db', 'donations.db')
os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)

app = Flask(__name__)
application = Application.builder().token(TOKEN).build()

# -------------------------------------------------
# DATABASE
# -------------------------------------------------
async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                last_name TEXT,
                first_seen TEXT
            )
        ''')
        await db.execute('''
            CREATE TABLE IF NOT EXISTS donations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                amount INTEGER,
                payload TEXT,
                timestamp TEXT
            )
        ''')
        await db.commit()

async def log_user(user):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute('''
            INSERT OR IGNORE INTO users
            (user_id, username, first_name, last_name, first_seen)
            VALUES (?, ?, ?, ?, ?)
        ''', (
            user.id,
            user.username or '',
            user.first_name or '',
            user.last_name or '',
            datetime.utcnow().isoformat()
        ))
        await db.commit()

async def log_donation(user_id: int, amount: int, payload: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute('''
            INSERT INTO donations (user_id, amount, payload, timestamp)
            VALUES (?, ?, ?, ?)
        ''', (user_id, amount, payload, datetime.utcnow().isoformat()))
        await db.commit()

async def get_stats():
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute('SELECT COUNT(*), COALESCE(SUM(amount),0) FROM donations') as cur:
            row = await cur.fetchone()
            total_donations, total_stars = row
        async with db.execute('SELECT COUNT(*) FROM users') as cur:
            total_users = (await cur.fetchone())[0]
    return total_users, total_stars, total_donations

async def get_last_donations(limit: int = 10):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute('''
            SELECT d.*, u.username, u.first_name
            FROM donations d
            LEFT JOIN users u ON d.user_id = u.user_id
            ORDER BY d.id DESC LIMIT ?
        ''', (limit,)) as cur:
            rows = await cur.fetchall()
    return [dict(r) for r in rows]

# -------------------------------------------------
# BOT HANDLERS
# -------------------------------------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    await log_user(user)

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
    data = query.data

    if data.startswith('donate_'):
        amount = int(data.split('_')[1])
        await send_invoice(context, query.message.chat_id, amount, query.from_user.id)

    elif data == 'custom':
        context.user_data['waiting_for_amount'] = True
        await query.edit_message_text(
            "Please reply with the custom amount (e.g., 50 for 50 Stars):"
        )

async def send_invoice(context: ContextTypes.DEFAULT_TYPE, chat_id: int, amount: int, user_id: int):
    title = f"Donate {amount} Stars"
    description = "Thank you for supporting with Stars! This helps keep things going. ❤️"
    payload = f"donation_{amount}_{user_id}"
    prices = [LabeledPrice("Donation", amount * 100)]

    await context.bot.send_invoice(
        chat_id=chat_id,
        title=title,
        description=description,
        payload=payload,
        provider_token="",
        currency="XTR",
        prices=prices,
        start_parameter="donation-bot"
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data.get('waiting_for_amount'):
        try:
            amount = int(update.message.text.strip())
            if amount < 1:
                await update.message.reply_text("Amount must be at least 1 Star. Try again:")
                return
            await send_invoice(context, update.effective_chat.id, amount, update.effective_user.id)
            context.user_data['waiting_for_amount'] = False
        except ValueError:
            await update.message.reply_text("Please enter a valid number (e.g., 25). Try again:")

async def precheckout(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.pre_checkout_query
    await context.bot.answer_pre_checkout_query(pre_checkout_query_id=query.id, ok=True)

async def successful_payment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    payment = update.message.successful_payment
    amount = payment.total_amount // 100
    user_id = update.effective_user.id
    payload = payment.invoice_payload

    await log_donation(user_id, amount, payload)

    await update.message.reply_text(
        f"Thank you for your **{amount} Stars** donation! Your support means the world!\n"
        "Want to donate again? Just /start",
        parse_mode='Markdown'
    )

# ADMIN COMMANDS
async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_USER_ID:
        await update.message.reply_text("Admin only.")
        return
    users, stars, count = await get_stats()
    await update.message.reply_text(
        f"*Bot Statistics*\n"
        f"• Users: `{users}`\n"
        f"• Total Stars: `{stars}`\n"
        f"• Donations: `{count}`",
        parse_mode='Markdown'
    )

async def donations(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_USER_ID:
        await update.message.reply_text("Admin only.")
        return
    rows = await get_last_donations(10)
    if not rows:
        await update.message.reply_text("No donations yet.")
        return
    lines = []
    for r in rows:
        name = (r['first_name'] or '') + (f" @{r['username']}" if r['username'] else '')
        lines.append(f"`{r['amount']}` Stars – {name} – {r['timestamp'][:19].replace('T',' ')}")
    await update.message.reply_text(
        "*Last 10 Donations*\n" + "\n".join(lines),
        parse_mode='Markdown'
    )

# Register handlers
application.add_handler(CommandHandler("start", start))
application.add_handler(CallbackQueryHandler(button))
application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
application.add_handler(PreCheckoutQueryHandler(precheckout))
application.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, successful_payment))
application.add_handler(CommandHandler("stats", stats))
application.add_handler(CommandHandler("donations", donations))

# -------------------------------------------------
# WEBHOOK
# -------------------------------------------------
@app.route(f'/webhook/{TOKEN}', methods=['POST'])
async def webhook():
    if request.method != 'POST':
        abort(405)
    json_data = request.get_json(force=True)
    if not json_data:
        abort(400)

    update = Update.de_json(json_data, application.bot)
    await application.initialize()
    await application.process_update(update)
    return 'OK', 200

@app.route('/')
def index():
    return 'Bot is alive!'

if __name__ == '__main__':
    import asyncio
    asyncio.run(init_db())
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))

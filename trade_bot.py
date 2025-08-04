import sqlite3
from pyrogram import Client, filters
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from pyrogram.enums import ChatMemberStatus
import os

API_ID = int(os.environ["API_ID"])
API_HASH = os.environ["API_HASH"]
BOT_TOKEN = os.environ["BOT_TOKEN"]

app = Client("trade_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# SQLite setup
conn = sqlite3.connect("trades.db", check_same_thread=False)
cursor = conn.cursor()

cursor.execute("""
CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_id TEXT,
    buyer TEXT,
    seller TEXT,
    amount TEXT,
    details TEXT,
    group_id INTEGER,
    message_id INTEGER,
    status TEXT DEFAULT 'pending',
    buyer_agree INTEGER DEFAULT 0,
    seller_agree INTEGER DEFAULT 0
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS group_counters (
    group_id INTEGER PRIMARY KEY,
    last_trade_number INTEGER DEFAULT 0
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS group_admins (
    group_id INTEGER,
    admin_id INTEGER,
    PRIMARY KEY (group_id, admin_id)
)
""")

conn.commit()

# Generate group-specific trade ID
def create_trade_id(group_id):
    cursor.execute("SELECT last_trade_number FROM group_counters WHERE group_id = ?", (group_id,))
    row = cursor.fetchone()
    if row:
        last_number = row[0] + 1
        cursor.execute("UPDATE group_counters SET last_trade_number = ? WHERE group_id = ?", (last_number, group_id))
    else:
        last_number = 1
        cursor.execute("INSERT INTO group_counters (group_id, last_trade_number) VALUES (?, ?)", (group_id, last_number))
    conn.commit()
    return f"Trd-{last_number:04d}"

# /start command
@app.on_message(filters.command("start") & filters.private)
async def start_handler(client: Client, message: Message):
    await message.reply(
        "👋 Hello! I'm your Trade Bot.\n\n"
        "Use me in a group to manage safe trades between buyers and sellers.\n\n"
        "📌 Commands:\n"
        "/trade <buyer> <seller> <amount> <details>\n"
        "/done <trade_id> — Mark trade as completed (admins only)\n"
        "/setadmin — Register to receive trade notifications (run in group)\n"
        "/unsetadmin — Stop receiving trade notifications (run in group)\n"
        "/listadmins — List registered trade admins in this group"
    )

# /setadmin command
@app.on_message(filters.command("setadmin") & filters.group)
async def set_admin(client: Client, message: Message):
    admin_id = message.from_user.id
    group_id = message.chat.id

    member = await client.get_chat_member(group_id, admin_id)
    if member.status not in (ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER):
        return await message.reply("❌ Only group admins can use this command.")

    cursor.execute("INSERT OR IGNORE INTO group_admins (group_id, admin_id) VALUES (?, ?)", (group_id, admin_id))
    conn.commit()
    await message.reply("✅ You are now registered to receive trade notifications for this group.")

# /unsetadmin command
@app.on_message(filters.command("unsetadmin") & filters.group)
async def unset_admin(client: Client, message: Message):
    admin_id = message.from_user.id
    group_id = message.chat.id

    cursor.execute("DELETE FROM group_admins WHERE group_id = ? AND admin_id = ?", (group_id, admin_id))
    conn.commit()
    await message.reply("🛑 You will no longer receive trade notifications for this group.")

# /listadmins command
@app.on_message(filters.command("listadmins") & filters.group)
async def list_admins(client: Client, message: Message):
    group_id = message.chat.id
    cursor.execute("SELECT admin_id FROM group_admins WHERE group_id = ?", (group_id,))
    rows = cursor.fetchall()

    if not rows:
        return await message.reply("❌ No trade admins have registered in this group.")

    text = "👮‍♂️ <b>Registered Trade Admins:</b>\n"
    for row in rows:
        try:
            user = await client.get_users(row[0])
            name = user.mention
            text += f"• {name}\n"
        except:
            continue

    await message.reply(text)

# /trade command
@app.on_message(filters.command("trade") & filters.group)
async def trade_handler(client: Client, message: Message):
    if len(message.command) < 5:
        return await message.reply("Usage: /trade <buyer> <seller> <amount> <details>")

    buyer = message.command[1]
    seller = message.command[2]
    amount = message.command[3]
    details = " ".join(message.command[4:])
    group_id = message.chat.id
    trade_id = create_trade_id(group_id)

    text = f"""<b>🆕 New Trade Created</b>

🆔 <b>Trade ID:</b> <code>{trade_id}</code>
👤 <b>Buyer:</b> {buyer}
🧑‍💼 <b>Seller:</b> {seller}
💰 <b>Amount:</b> {amount}
📦 <b>Details:</b> {details}

Please confirm the trade."""

    buttons = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Agree", callback_data=f"agree|{trade_id}|{buyer}|{seller}"),
            InlineKeyboardButton("❌ Cancel", callback_data=f"cancel|{trade_id}|{buyer}|{seller}")
        ]
    ])

    sent = await message.reply(text, reply_markup=buttons)
    cursor.execute("""INSERT INTO trades 
        (trade_id, buyer, seller, amount, details, group_id, message_id) 
        VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (trade_id, buyer, seller, amount, details, group_id, sent.id))
    conn.commit()

# Callback handler
@app.on_callback_query()
async def callback_handler(client: Client, callback_query: CallbackQuery):
    data = callback_query.data.split("|")
    action, trade_id, buyer, seller = data
    group_id = callback_query.message.chat.id
    user = f"@{callback_query.from_user.username}" if callback_query.from_user.username else None

    if user not in [buyer, seller]:
        return await callback_query.answer("Only the buyer or seller can respond.", show_alert=True)

    mention = callback_query.from_user.mention

    cursor.execute("SELECT buyer_agree, seller_agree, message_id, group_id, status, amount, details FROM trades WHERE trade_id = ? AND group_id = ?", (trade_id, group_id))
    result = cursor.fetchone()
    if not result:
        return await callback_query.answer("Trade not found.", show_alert=True)

    buyer_agree, seller_agree, msg_id, group_id, status, amount, details = result

    if status != "pending":
        return await callback_query.answer("Trade is already completed or cancelled.", show_alert=True)

    if action == "agree":
        if user == buyer:
            cursor.execute("UPDATE trades SET buyer_agree = 1 WHERE trade_id = ? AND group_id = ?", (trade_id, group_id))
        elif user == seller:
            cursor.execute("UPDATE trades SET seller_agree = 1 WHERE trade_id = ? AND group_id = ?", (trade_id, group_id))
        conn.commit()

        cursor.execute("SELECT buyer_agree, seller_agree FROM trades WHERE trade_id = ? AND group_id = ?", (trade_id, group_id))
        buyer_agree, seller_agree = cursor.fetchone()

        if buyer_agree and seller_agree:
            final_text = f"✅ Both parties agreed on trade <code>{trade_id}</code>.\nThe deal is now locked! @admin"
            cursor.execute("UPDATE trades SET status = 'agreed' WHERE trade_id = ? AND group_id = ?", (trade_id, group_id))
            conn.commit()

            await callback_query.message.edit_reply_markup(
                InlineKeyboardMarkup([[InlineKeyboardButton("✅ Trade Locked", callback_data="locked")]])
            )
            await callback_query.message.reply(final_text)

            # Notify all registered admins
            cursor.execute("SELECT admin_id FROM group_admins WHERE group_id = ?", (group_id,))
            admin_rows = cursor.fetchall()

            if admin_rows:
                link_group_id = str(group_id).replace("-100", "")
                message_link = f"https://t.me/c/{link_group_id}/{msg_id}"

                trade_form = (
                    f"📢 <b>Trade Locked</b> in <b>{callback_query.message.chat.title}</b>\n\n"
                    f"🆔 <b>Trade ID:</b> <code>{trade_id}</code>\n"
                    f"👤 <b>Buyer:</b> {buyer}\n"
                    f"🧑‍💼 <b>Seller:</b> {seller}\n"
                    f"💰 <b>Amount:</b> {amount}\n"
                    f"📦 <b>Details:</b> {details}\n\n"
                    f"✅ Both parties agreed on this trade."
                )

                for row in admin_rows:
                    admin_id = row[0]
                    try:
                        await client.send_message(
                            admin_id,
                            trade_form,
                            reply_markup=InlineKeyboardMarkup([
                                [InlineKeyboardButton("📄 Trade Description", url=message_link)]
                            ])
                        )
                    except Exception as e:
                        print(f"Failed to DM admin {admin_id}: {e}")
        else:
            await callback_query.message.reply(f"🤝 Trade <code>{trade_id}</code> confirmed by {mention}.\nWaiting for the other party...")

    elif action == "cancel":
        cursor.execute("UPDATE trades SET status = 'cancelled' WHERE trade_id = ? AND group_id = ?", (trade_id, group_id))
        conn.commit()
        await callback_query.message.edit_reply_markup(reply_markup=None)
        await callback_query.message.reply(f"❌ Trade <code>{trade_id}</code> cancelled by {mention}.")

    elif action == "locked":
        return await callback_query.answer("✅ This trade is already locked.", show_alert=True)

# /done command for admins
@app.on_message(filters.command("done") & filters.group)
async def done_handler(client: Client, message: Message):
    try:
        member = await client.get_chat_member(message.chat.id, message.from_user.id)
        if member.status not in (ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER):
            return await message.reply("❌ Only group admins can use this command.")
    except:
        return await message.reply("⚠️ Could not verify admin status.")

    if len(message.command) < 2:
        return await message.reply("Usage: /done <trade_id>")

    trade_id = message.command[1]
    group_id = message.chat.id

    cursor.execute("SELECT buyer, seller FROM trades WHERE trade_id = ? AND group_id = ?", (trade_id, group_id))
    row = cursor.fetchone()
    if not row:
        return await message.reply("❌ Trade ID not found.")

    buyer, seller = row
    cursor.execute("UPDATE trades SET status = 'done' WHERE trade_id = ? AND group_id = ?", (trade_id, group_id))
    conn.commit()

    await message.reply(
        f"✅ Trade <code>{trade_id}</code> marked as done by {message.from_user.mention}\n\n"
        f"👤 Buyer: <b>{buyer}</b>\n🧑‍💼 Seller: <b>{seller}</b>\n\nThanks for the deal!"
    )

# Start the bot
print("✅ Trade Bot is starting...")
app.run()

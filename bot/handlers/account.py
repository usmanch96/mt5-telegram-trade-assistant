import asyncio
from telegram import Update
from telegram.ext import ContextTypes
from mt5.monitor import get_account_details
from database.models import authorize_user, revoke_user
from bot.keyboards import get_main_keyboard, get_back_keyboard, get_persistent_menu_keyboard
from bot.middleware import restricted
from services.logger import logger

# MT5 account trade mode constants
_TRADE_MODE_LABELS = {0: "Demo", 1: "Contest", 2: "Real"}

def _margin_bar(level: float) -> str:
    """Renders a compact 10-block visual bar for margin health level."""
    filled = min(10, int(level / 100))  # Each block = 100%
    return "🟩" * filled + "⬜" * (10 - filled)

def format_account_info(acc: dict) -> str:
    """Formats MT5 account information dictionary into a rich Markdown string."""
    profit_emoji = "🟢" if acc["profit"] >= 0 else "🔴"
    profit_sign  = "+" if acc["profit"] >= 0 else ""

    trade_mode_label = _TRADE_MODE_LABELS.get(acc["trade_mode"], str(acc["trade_mode"]))

    no_trades = acc["margin_level"] == 0
    if no_trades:
        margin_section = f"🛡️ Margin Level · ℹ️ No open trades\n"
    elif acc["margin_level"] < 200:
        margin_section = (
            f"🛡️ Margin Level · `{acc['margin_level']:,.0f}%` 🚨 CRITICAL\n"
            f"{_margin_bar(acc['margin_level'])}\n"
        )
    elif acc["margin_level"] < 500:
        margin_section = (
            f"🛡️ Margin Level · `{acc['margin_level']:,.0f}%` ⚠️ Warning\n"
            f"{_margin_bar(acc['margin_level'])}\n"
        )
    else:
        margin_section = (
            f"🛡️ Margin Level · `{acc['margin_level']:,.0f}%` ✅ Healthy\n"
            f"{_margin_bar(acc['margin_level'])}\n"
        )

    return (
        f"📊 *Account Status*\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"👤 `{acc['login']}` · {acc['name']}\n"
        f"🏢 {acc['company']}\n"
        f"⚙️ Mode `{trade_mode_label}` · Leverage `1:{acc['leverage']}`\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"💰 *Balance* · `{acc['balance']:,.2f} {acc['currency']}`\n"
        f"📈 *Equity* · `{acc['equity']:,.2f} {acc['currency']}`\n"
        f"{profit_emoji} *Float PnL* · `{profit_sign}{acc['profit']:,.2f} {acc['currency']}`\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🔒 Locked · `{acc['margin']:,.2f}`  🔓 Free · `{acc['margin_free']:,.2f}`\n"
        f"{margin_section}"
    )

_WELCOME_TEXT = (
    "🤖 *MT5 Trade Assistant*\n"
    "━━━━━━━━━━━━━━━━━━━━\n"
    "Your professional Telegram trading terminal.\n\n"
    "📊 Account status  •  💼 Open positions\n"
    "🚀 Execute trades  •  🥞 Layer entries\n"
    "⚡ Batch controls  •  🔔 Price alerts\n"
    "━━━━━━━━━━━━━━━━━━━━\n"
    "👇 Select an option to get started:"
)

@restricted
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles the /start or /menu command, sending the main menu with a live account snapshot."""
    if update.message:
        # Silently load the persistent bottom reply keyboard
        await update.message.reply_text(
            text="✅ *Bot keyboard ready.* Use the buttons below or the inline menu above.",
            reply_markup=get_persistent_menu_keyboard(),
            parse_mode="Markdown"
        )

        # Try to fetch live account info for the welcome card
        acc = await asyncio.to_thread(get_account_details)
        if acc:
            profit_emoji = "🟢" if acc["profit"] >= 0 else "🔴"
            profit_sign  = "+" if acc["profit"] >= 0 else ""
            snapshot = (
                f"🤖 *MT5 Trade Assistant*\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"👤 `{acc['login']}` · {acc['name']}\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"💰 Balance · `{acc['balance']:,.2f} {acc['currency']}`\n"
                f"📈 Equity · `{acc['equity']:,.2f} {acc['currency']}`\n"
                f"{profit_emoji} Float PnL · `{profit_sign}{acc['profit']:,.2f} {acc['currency']}`\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"👇 Select an option:"
            )
        else:
            snapshot = _WELCOME_TEXT

        await update.message.reply_text(
            text=snapshot,
            parse_mode="Markdown",
            reply_markup=get_main_keyboard()
        )
    else:
        query = update.callback_query
        await query.answer()
        await query.edit_message_text(
            text=_WELCOME_TEXT,
            parse_mode="Markdown",
            reply_markup=get_main_keyboard()
        )

@restricted
async def cb_account_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Callback query handler to display the account status."""
    query = update.callback_query
    await query.answer()

    await query.edit_message_text(
        text="🔄 _Querying MT5 terminal..._",
        parse_mode="Markdown"
    )

    acc_details = await asyncio.to_thread(get_account_details)

    if not acc_details:
        await query.edit_message_text(
            text=(
                "❌ *Connection Failed*\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                "Could not retrieve account data from MT5.\n"
                "Please ensure the terminal is running and logged in."
            ),
            parse_mode="Markdown",
            reply_markup=get_back_keyboard()
        )
        return

    await query.edit_message_text(
        text=format_account_info(acc_details),
        parse_mode="Markdown",
        reply_markup=get_back_keyboard()
    )

@restricted
async def cb_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Callback query handler to return back to the main menu."""
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        text=_WELCOME_TEXT,
        parse_mode="Markdown",
        reply_markup=get_main_keyboard()
    )


@restricted
async def cmd_authorize_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Authorizes a new user ID in the whitelist (Restricted to whitelisted admins)."""
    if not update.message:
        return
        
    args = context.args
    if not args:
        await update.message.reply_text(
            "✍️ *Usage:* `/authorize <user_id> [username]`\nExample: `/authorize 123456789 JohnDoe`",
            parse_mode="Markdown"
        )
        return
        
    try:
        target_id = int(args[0])
    except ValueError:
        await update.message.reply_text("❌ User ID must be a number.")
        return
        
    username = args[1] if len(args) > 1 else "ChatUser"
    
    success = await asyncio.to_thread(authorize_user, target_id, username)
    if success:
        await update.message.reply_text(
            f"✅ *User Whitelisted successfully!*\n\n"
            f"👤 *ID:* `{target_id}`\n"
            f"🏷️ *Username:* `{username}`\n\n"
            f"They can now access the bot via `/start`.",
            parse_mode="Markdown"
        )
    else:
        await update.message.reply_text(f"ℹ️ User `{target_id}` is already whitelisted or failed to save.")

@restricted
async def cmd_revoke_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Revokes access for a whitelisted user ID (Restricted to whitelisted admins)."""
    if not update.message:
        return
        
    args = context.args
    if not args:
        await update.message.reply_text(
            "✍️ *Usage:* `/revoke <user_id>`\nExample: `/revoke 123456789`",
            parse_mode="Markdown"
        )
        return
        
    try:
        target_id = int(args[0])
    except ValueError:
        await update.message.reply_text("❌ User ID must be a number.")
        return
        
    if target_id == update.effective_user.id:
        await update.message.reply_text("❌ You cannot revoke your own admin access.")
        return
        
    success = await asyncio.to_thread(revoke_user, target_id)
    if success:
        await update.message.reply_text(
            f"🚫 *Access Revoked successfully!*\n\n"
            f"👤 *ID:* `{target_id}`\n\n"
            f"This user can no longer send commands or click buttons.",
            parse_mode="Markdown"
        )
    else:
        await update.message.reply_text(f"❌ User `{target_id}` was not found in the whitelist.")

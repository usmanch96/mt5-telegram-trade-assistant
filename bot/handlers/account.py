import asyncio
from telegram import Update, ReplyKeyboardRemove
from telegram.ext import ContextTypes
from mt5.monitor import get_account_details
from database.models import authorize_user, revoke_user
from config import settings
from bot.keyboards import get_main_keyboard
from bot.middleware import restricted

# MT5 account trade mode constants
_TRADE_MODE_LABELS = {0: "Demo", 1: "Contest", 2: "Real"}

def _margin_bar(level: float) -> str:
    """Renders a compact 10-block visual bar for margin health level."""
    filled = min(10, int(level / 100))  # Each block = 100%
    return "🟩" * filled + "⬜" * (10 - filled)

def format_account_info(acc: dict) -> str:
    """Formats MT5 account information dictionary into a rich Markdown status card."""
    profit_emoji = "🟢" if acc["profit"] >= 0 else "🔴"
    profit_sign  = "+" if acc["profit"] >= 0 else ""

    trade_mode_label = _TRADE_MODE_LABELS.get(acc["trade_mode"], str(acc["trade_mode"]))

    no_trades = acc["margin_level"] == 0
    if no_trades:
        margin_section = "🛡️ Margin Level · ℹ️ No open trades\n"
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

def _get_offline_account_text() -> str:
    """Fallback text displayed when MT5 terminal is unreachable."""
    return (
        "📊 *Account Status*\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "⚠️ *Connection Pending / Terminal Offline*\n"
        "Could not retrieve live account data from MT5.\n"
        "Please check that your terminal is open and logged in.\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "👇 Tap Refresh Status to retry:"
    )

async def render_account_dashboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Unified renderer that displays or updates the live Account Status card in place."""
    acc_details = await asyncio.to_thread(get_account_details)
    text = format_account_info(acc_details) if acc_details else _get_offline_account_text()
    reply_markup = get_main_keyboard()
    chat_id = update.effective_chat.id

    # 1. Triggered from an inline button (CallbackQuery)
    if update.callback_query:
        query = update.callback_query
        try:
            await query.answer()
        except Exception:
            pass
        try:
            await query.edit_message_text(text, parse_mode="Markdown", reply_markup=reply_markup)
            context.user_data["active_card_msg_id"] = query.message.message_id
        except Exception as e:
            if "message is not modified" not in str(e).lower():
                raise
        return

    # 2. Triggered from text message (Bottom Menu button or /start, /status, /menu)
    if update.message:
        # Delete user's command message so chat doesn't get cluttered
        try:
            await update.message.delete()
        except Exception:
            pass

        # Try to edit the existing card on screen in place!
        old_card_id = context.user_data.get("active_card_msg_id")
        if old_card_id:
            try:
                await context.bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=old_card_id,
                    text=text,
                    parse_mode="Markdown",
                    reply_markup=reply_markup
                )
                return  # Updated in place cleanly!
            except Exception as e:
                if "message is not modified" in str(e).lower():
                    return
                # If old card no longer exists, fall through to create a new one

        # Remove any lingering bottom reply keyboard from user's screen
        if not context.user_data.get("keyboard_removed"):
            try:
                rm = await context.bot.send_message(
                    chat_id=chat_id,
                    text="⚡",
                    reply_markup=ReplyKeyboardRemove()
                )
                await context.bot.delete_message(chat_id=chat_id, message_id=rm.message_id)
                context.user_data["keyboard_removed"] = True
            except Exception:
                pass

        # Send fresh card and track its ID
        msg = await context.bot.send_message(
            chat_id=chat_id,
            text=text,
            parse_mode="Markdown",
            reply_markup=reply_markup
        )
        context.user_data["active_card_msg_id"] = msg.message_id

@restricted
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles /start, /menu, /account, /dashboard, sending or updating the unified Account Status screen."""
    await render_account_dashboard(update, context)

@restricted
async def cb_account_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Refreshes or displays the unified Account Status screen in place."""
    await render_account_dashboard(update, context)

@restricted
async def cb_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Callback query handler to return back to the unified Account Status screen in place."""
    await render_account_dashboard(update, context)

@restricted
async def cmd_authorize_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Authorizes a new user ID in the whitelist (Restricted to Super Admins)."""
    if not update.message:
        return

    caller_id = update.effective_user.id
    if caller_id not in settings.AUTHORIZED_USERS:
        await update.message.reply_text("❌ Only Super Admins (.env configured) can authorize new users.")
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
    """Revokes access for a whitelisted user ID (Restricted to Super Admins)."""
    if not update.message:
        return

    caller_id = update.effective_user.id
    if caller_id not in settings.AUTHORIZED_USERS:
        await update.message.reply_text("❌ Only Super Admins (.env configured) can revoke user access.")
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

    if target_id in settings.AUTHORIZED_USERS:
        await update.message.reply_text("❌ Cannot revoke a Super Admin listed in .env configuration.")
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

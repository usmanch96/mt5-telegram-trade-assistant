import asyncio
from telegram import Update, ReplyKeyboardRemove
from telegram.ext import ContextTypes
from mt5.monitor import get_account_details
from config import settings
from bot.keyboards import get_main_keyboard
from bot.middleware import restricted
from services.logger import logger

# MT5 account trade mode constants
_TRADE_MODE_LABELS = {0: "Demo", 1: "Contest", 2: "Real"}

def _margin_bar(level: float) -> str:
    """Renders a compact 10-block visual bar for margin health level."""
    filled = min(10, int(level / 100))  # Each block = 100%
    return "🟩" * filled + "⬜" * (10 - filled)

def format_account_info(acc: dict, risk_lines: list = None) -> str:
    """Formats MT5 account information dictionary into a rich Markdown status card."""
    trade_mode_label = _TRADE_MODE_LABELS.get(acc["trade_mode"], str(acc["trade_mode"]))

    # Detect if positions exist
    positions_count = acc.get("positions_total", 0)
    has_open_trades = positions_count > 0 or (risk_lines and len(risk_lines) > 0) or acc.get("profit", 0) != 0

    if not has_open_trades:
        margin_section = "🛡️ Margin Level · ℹ️ No open trades\n"
    elif acc.get("margin_level", 0) == 0 or acc.get("margin", 0) == 0:
        # Zero margin required (e.g. Exness Unlimited Leverage or fully hedged) -> 0% margin risk!
        margin_section = (
            "🛡️ Margin Level · `∞` (Zero Margin) ✅ Healthy\n"
            "━━━━━━━━━━\n"
            f"{_margin_bar(1000)}\n"
        )
    elif acc["margin_level"] < 200:
        margin_section = (
            f"🛡️ Margin Level · `{acc['margin_level']:,.0f}%` 🚨 CRITICAL\n"
            f"━━━━━━━━━━\n"
            f"{_margin_bar(acc['margin_level'])}\n"
        )
    elif acc["margin_level"] < 500:
        margin_section = (
            f"🛡️ Margin Level · `{acc['margin_level']:,.0f}%` ⚠️ Warning\n"
            f"━━━━━━━━━━\n"
            f"{_margin_bar(acc['margin_level'])}\n"
        )
    else:
        margin_section = (
            f"🛡️ Margin Level · `{acc['margin_level']:,.0f}%` ✅ Healthy\n"
            f"━━━━━━━━━━\n"
            f"{_margin_bar(acc['margin_level'])}\n"
        )

    if risk_lines:
        risk_block = "\n" + "\n".join(risk_lines) + "\n"
    else:
        profit_emoji = "🟢" if acc["profit"] >= 0 else "🔴"
        profit_sign  = "+" if acc["profit"] >= 0 else ""
        risk_block = f"\n📊 *Float P&L* · {profit_emoji} `{profit_sign}{acc['profit']:,.2f} {acc['currency']}`\n"

    # Human-readable leverage (Exness unlimited leverage is represented as 2,000,000,000)
    leverage_val = acc.get("leverage", 1)
    leverage_str = "1:Unlimited" if leverage_val >= 1000000 else f"1:{leverage_val}"

    return (
        f"📊 *Account Status*\n"
        f"━━━━━━━━━━\n"
        f"👤 `{acc['login']}` · {acc['name']}\n"
        f"🏢 {acc['company']}\n"
        f"⚙️ Mode `{trade_mode_label}` · Leverage `{leverage_str}`\n"
        f"━━━━━━━━━━\n"
        f"💰 *Balance* · `{acc['balance']:,.2f} {acc['currency']}`\n"
        f"📈 *Equity* · `{acc['equity']:,.2f} {acc['currency']}`\n"
        f"━━━━━━━━━━"
        f"{risk_block}"
        f"━━━━━━━━━━\n"
        f"🔒 Locked · `{acc['margin']:,.2f}`  🔓 Free · `{acc['margin_free']:,.2f}`\n"
        f"{margin_section}"
    )

def _get_offline_account_text() -> str:
    """Fallback text displayed when MT5 terminal is unreachable."""
    return (
        "📊 *Account Status*\n"
        "━━━━━━━━━━\n"
        "⚠️ *Connection Pending / Terminal Offline*\n"
        "Could not retrieve live account data from MT5.\n"
        "Please check that your terminal is open and logged in.\n"
        "━━━━━━━━━━\n"
        "👇 Tap Refresh Status to retry:"
    )

async def render_account_dashboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Unified renderer that displays or updates the live Account Status card in place."""
    try:
        acc_details = await asyncio.to_thread(get_account_details)
        if acc_details:
            from bot.handlers.batch import compute_portfolio_risk_summary_lines
            risk_lines = await compute_portfolio_risk_summary_lines()
            text = format_account_info(acc_details, risk_lines=risk_lines)
        else:
            text = _get_offline_account_text()
    except Exception as e:
        logger.error(f"Error fetching account details: {e}")
        text = _get_offline_account_text()

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

    # 2. Triggered from command (/start, /menu, /status, /dashboard)
    if update.message:
        # Delete previous active card so chat stays clean with only 1 card
        old_card_id = context.user_data.get("active_card_msg_id")
        if old_card_id:
            try:
                await context.bot.delete_message(chat_id=chat_id, message_id=old_card_id)
            except Exception:
                pass

        # Send fresh card at the bottom of the chat right below user's command
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
async def cmd_sound_test(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Plays a test chime & voice alert on the local PC speaker to verify audio setup."""
    from services.sound import play_sound, get_sounds_dir
    from bot.keyboards import get_back_keyboard
    
    sounds_dir = get_sounds_dir()
    play_sound("TEST")

    mode_desc = {
        "both": "🔊 Chime + Voice Announcement",
        "chime": "🔔 Chime Only (WAV)",
        "voice": "🗣️ Voice Announcement Only",
    }.get(settings.SOUND_ALERT_MODE, settings.SOUND_ALERT_MODE.title())

    dir_desc = f"`{sounds_dir}`" if sounds_dir else "⚠️ System Default Chime"

    text = (
        "🔊 *PC Sound Alert Test*\n"
        "━━━━━━━━━━\n"
        f"• Status: {'✅ Active' if settings.SOUND_ALERTS_ENABLED else '❌ Disabled'}\n"
        f"• Mode: {mode_desc}\n"
        f"• Sounds Directory: {dir_desc}\n\n"
        "▶️ _Playing test audio on your PC speaker now!_"
    )
    if update.message:
        await update.message.reply_text(text, parse_mode="Markdown")
    elif update.callback_query:
        await update.callback_query.answer("Played test sound alert to PC!")
        await update.callback_query.edit_message_text(text, parse_mode="Markdown", reply_markup=get_back_keyboard("menu_main"))

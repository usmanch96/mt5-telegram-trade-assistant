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

    if risk_lines:
        risk_block = "\n" + "\n".join(risk_lines) + "\n"
    else:
        profit_emoji = "🟢" if acc["profit"] >= 0 else "🔴"
        profit_sign  = "+" if acc["profit"] >= 0 else ""
        risk_block = f"\n📊 *Float P&L* · {profit_emoji} `{profit_sign}{acc['profit']:,.2f} {acc['currency']}`\n"

    return (
        f"📊 *Account Status*\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"👤 `{acc['login']}` · {acc['name']}\n"
        f"🏢 {acc['company']}\n"
        f"⚙️ Mode `{trade_mode_label}` · Leverage `1:{acc['leverage']}`\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"💰 *Balance* · `{acc['balance']:,.2f} {acc['currency']}`\n"
        f"📈 *Equity* · `{acc['equity']:,.2f} {acc['currency']}`\n"
        f"━━━━━━━━━━━━━━━━━━━━"
        f"{risk_block}"
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

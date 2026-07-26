import asyncio
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ContextTypes,
    ConversationHandler,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
)
from mt5.monitor import get_symbol_info
from database.models import add_price_alert, get_active_price_alerts, deactivate_price_alert
from bot.keyboards import get_alert_keyboard, get_back_keyboard, get_main_keyboard
from bot.middleware import restricted
from bot.conv_filters import NOT_MENU_BUTTON
from services.logger import logger

# States
SELECT_ALERT_SYMBOL, INPUT_ALERT_PRICE = range(2)

POPULAR_SYMBOLS = ["EURUSD", "GBPUSD", "XAUUSD", "US30", "NAS100", "BTCUSD"]

def get_symbol_keyboard() -> InlineKeyboardMarkup:
    keyboard = []
    row = []
    for sym in POPULAR_SYMBOLS:
        row.append(InlineKeyboardButton(sym, callback_data=f"asym_{sym}"))
        if len(row) == 2:
            keyboard.append(row)
            row = []
    keyboard.append([InlineKeyboardButton("✍️ Other Symbol", callback_data="asym_other")])
    keyboard.append([InlineKeyboardButton("❌ Cancel", callback_data="alert_cancel")])
    return InlineKeyboardMarkup(keyboard)

@restricted
async def cb_alerts_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Displays the main alerts panel."""
    query = update.callback_query
    
    text = (
        "🔔 *Price Alerts Manager*\n\n"
        "Set custom triggers. The bot will notify you instantly when the market price hits your targets."
    )
    
    if query:
        await query.answer()
        await query.edit_message_text(
            text=text,
            parse_mode="Markdown",
            reply_markup=get_alert_keyboard()
        )
    elif update.message:
        await update.message.reply_text(
            text=text,
            parse_mode="Markdown",
            reply_markup=get_alert_keyboard()
        )

@restricted
async def cb_view_active_alerts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Displays the list of active price alerts with delete options."""
    query = update.callback_query
    await query.answer()
    
    alerts = await asyncio.to_thread(get_active_price_alerts)
    
    if not alerts:
        await query.edit_message_text(
            text="🔔 *Active Price Alerts*\n\n📭 _No active price alerts set._",
            parse_mode="Markdown",
            reply_markup=get_back_keyboard("menu_alerts")
        )
        return
        
    lines = ["🔔 *Active Price Alerts*\n"]
    keyboard_buttons = []
    
    for alert in alerts:
        direction = "≥" if alert["condition_type"] == "ABOVE" else "≤"
        lines.append(f"🔸 `{alert['symbol']}` {direction} `{alert['target_price']}`")
        
        # Action button to delete this alert
        btn = InlineKeyboardButton(f"❌ Del {alert['symbol']} @ {alert['target_price']}", callback_data=f"alert_del_{alert['id']}")
        keyboard_buttons.append([btn])
        
    keyboard_buttons.append([InlineKeyboardButton("⬅️ Back", callback_data="menu_alerts")])

    
    await query.edit_message_text(
        text="\n".join(lines),
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard_buttons)
    )

@restricted
async def cb_delete_alert_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Deletes an active price alert."""
    query = update.callback_query
    await query.answer()
    
    alert_id = int(query.data.split("_")[2])
    
    deleted = await asyncio.to_thread(deactivate_price_alert, alert_id)
    if deleted:
        # Instantly refresh screen
        await cb_view_active_alerts(update, context)
    else:
        await query.edit_message_text(
            text="❌ Failed to delete alert.",
            reply_markup=get_back_keyboard("menu_alerts")
        )

# --- Add Alert Wizard ---

@restricted
async def start_add_alert(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Starts the add alert wizard."""
    query = update.callback_query
    await query.answer()
    
    await query.edit_message_text(
        text="🔔 *Add Price Alert*\nChoose a symbol to watch:",
        parse_mode="Markdown",
        reply_markup=get_symbol_keyboard()
    )
    return SELECT_ALERT_SYMBOL

async def cb_select_alert_symbol(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """User selected a symbol via button."""
    query = update.callback_query
    await query.answer()
    
    selection = query.data.split("_")[1]
    if selection == "other":
        await query.edit_message_text(
            text="✍️ *Enter Symbol*\nPlease type the symbol name (e.g. `XAUUSD`):",
            parse_mode="Markdown"
        )
        return SELECT_ALERT_SYMBOL
        
    context.user_data["alert_symbol"] = selection
    
    # Query current price structure
    sym_info = await asyncio.to_thread(get_symbol_info, selection)
    curr_price = sym_info["bid"] if sym_info else 0.0
    
    await query.edit_message_text(
        text=f"🔣 *Symbol:* `{selection}`\n💲 *Current Price:* `{curr_price}`\n\nEnter target alert price:",
        parse_mode="Markdown"
    )
    return INPUT_ALERT_PRICE

async def txt_select_alert_symbol(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """User typed custom symbol."""
    symbol = update.message.text.upper().strip()
    
    sym_info = await asyncio.to_thread(get_symbol_info, symbol)
    if not sym_info:
        await update.message.reply_text(f"❌ Symbol `{symbol}` not recognized on MT5. Try again:")
        return SELECT_ALERT_SYMBOL
        
    context.user_data["alert_symbol"] = symbol
    await update.message.reply_text(
        text=f"🔣 *Symbol:* `{symbol}`\n💲 *Current Price:* `{sym_info['bid']}`\n\nEnter target alert price:",
        parse_mode="Markdown"
    )
    return INPUT_ALERT_PRICE

async def txt_alert_price_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Saves price alert, auto-detecting ABOVE or BELOW direction."""
    symbol = context.user_data["alert_symbol"]
    price_text = update.message.text.strip()
    
    try:
        target_price = float(price_text)
        if target_price <= 0:
            raise ValueError()
    except ValueError:
        await update.message.reply_text("❌ Please enter a valid positive decimal price:")
        return INPUT_ALERT_PRICE
        
    # Get current price to determine ABOVE/BELOW condition
    sym_info = await asyncio.to_thread(get_symbol_info, symbol)
    if not sym_info:
        await update.message.reply_text("❌ Failed to query current price of symbol. Aborted.", reply_markup=get_back_keyboard("menu_alerts"))
        return ConversationHandler.END
        
    current_price = sym_info["bid"]
    
    if target_price > current_price:
        condition = "ABOVE"
    else:
        condition = "BELOW"
        
    alert_id = await asyncio.to_thread(add_price_alert, symbol, target_price, condition)
    
    if alert_id:
        direction_emoji = "📈" if condition == "ABOVE" else "📉"
        await update.message.reply_text(
            text=(
                f"✅ *Price Alert Set successfully!*\n\n"
                f"🔣 *Symbol:* `{symbol}`\n"
                f"{direction_emoji} *Target:* `{condition} {target_price}`\n"
                f"💲 *Current Price:* `{current_price}`\n\n"
                f"Bot will send you a message when hit."
            ),
            parse_mode="Markdown",
            reply_markup=get_back_keyboard("menu_alerts")
        )
    else:
        await update.message.reply_text("❌ Failed to save alert to database.", reply_markup=get_back_keyboard("menu_alerts"))
        
    return ConversationHandler.END

async def cb_cancel_alert(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """User cancelled alert wizard."""
    query = update.callback_query
    await query.answer("Alert setup cancelled.")
    await query.edit_message_text("❌ *Alert setup cancelled.*", parse_mode="Markdown", reply_markup=get_back_keyboard("menu_alerts"))
    return ConversationHandler.END

async def cmd_cancel_alert(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Cancel via command."""
    await update.message.reply_text("❌ *Alert setup cancelled.*", parse_mode="Markdown", reply_markup=get_main_keyboard())
    return ConversationHandler.END

# --- Handler Config ---

def get_alert_conv_handler():
    return ConversationHandler(
        entry_points=[
            CallbackQueryHandler(start_add_alert, pattern="^alert_add$")
        ],
        states={
            SELECT_ALERT_SYMBOL: [
                CallbackQueryHandler(cb_select_alert_symbol, pattern=r"^asym_.*$"),
                MessageHandler(NOT_MENU_BUTTON, txt_select_alert_symbol)
            ],
            INPUT_ALERT_PRICE: [
                MessageHandler(NOT_MENU_BUTTON, txt_alert_price_input)
            ]
        },
        fallbacks=[
            CallbackQueryHandler(cb_cancel_alert, pattern="^alert_cancel$"),
            CommandHandler("cancel", cmd_cancel_alert)
        ],
        allow_reentry=True
    )

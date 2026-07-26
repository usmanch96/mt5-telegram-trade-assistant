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
from mt5.risk_calculator import calculate_lot_size_by_delta
from mt5.executor import send_market_order
from bot.keyboards import (
    get_back_keyboard, 
    get_main_keyboard, 
    get_preset_lots_keyboard, 
    get_preset_risk_keyboard
)
from bot.middleware import restricted
from bot.conv_filters import NOT_MENU_BUTTON
from services.logger import logger

# Conversation States
(
    SELECT_SYMBOL,
    SELECT_DIRECTION,
    SELECT_LOT_TYPE,
    INPUT_LOT_MANUAL,
    INPUT_RISK_PCT,
    INPUT_SL,
    INPUT_TP,
    CONFIRM_TRADE
) = range(8)

POPULAR_SYMBOLS = ["XAUUSD", "EURUSD", "GBPUSD", "US30", "NAS100", "BTCUSD"]

def get_symbol_keyboard() -> InlineKeyboardMarkup:
    keyboard = []
    row = []
    for sym in POPULAR_SYMBOLS:
        row.append(InlineKeyboardButton(sym, callback_data=f"sym_{sym}"))
        if len(row) == 2:
            keyboard.append(row)
            row = []
    keyboard.append([InlineKeyboardButton("✍️ Enter Other Symbol", callback_data="sym_other")])
    keyboard.append([InlineKeyboardButton("❌ Cancel", callback_data="trade_cancel")])
    return InlineKeyboardMarkup(keyboard)

def get_direction_keyboard() -> InlineKeyboardMarkup:
    keyboard = [
        [
            InlineKeyboardButton("🟩 BUY (Long)", callback_data="dir_BUY"),
            InlineKeyboardButton("🟥 SELL (Short)", callback_data="dir_SELL")
        ],
        [InlineKeyboardButton("❌ Cancel", callback_data="trade_cancel")]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_lot_type_keyboard() -> InlineKeyboardMarkup:
    keyboard = [
        [
            InlineKeyboardButton("✍️ Manual Lot Size", callback_data="ltype_manual"),
            InlineKeyboardButton("🛡️ Risk-Based Auto-Lot", callback_data="ltype_risk")
        ],
        [InlineKeyboardButton("❌ Cancel", callback_data="trade_cancel")]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_sl_keyboard(can_skip: bool) -> InlineKeyboardMarkup:
    keyboard = []
    if can_skip:
        keyboard.append([InlineKeyboardButton("⏭️ Skip / No Stop Loss", callback_data="sl_0")])
    keyboard.append([InlineKeyboardButton("❌ Cancel", callback_data="trade_cancel")])
    return InlineKeyboardMarkup(keyboard)

def get_tp_keyboard(has_sl: bool) -> InlineKeyboardMarkup:
    keyboard = []
    if has_sl:
        keyboard.append([
            InlineKeyboardButton("⚖️ RR 1:1", callback_data="tp_rr1"),
            InlineKeyboardButton("⚖️ RR 1:2", callback_data="tp_rr2"),
            InlineKeyboardButton("⚖️ RR 1:3", callback_data="tp_rr3")
        ])
    keyboard.append([InlineKeyboardButton("⏭️ Skip / No Take Profit", callback_data="tp_0")])
    keyboard.append([InlineKeyboardButton("❌ Cancel", callback_data="trade_cancel")])
    return InlineKeyboardMarkup(keyboard)

def get_confirm_keyboard() -> InlineKeyboardMarkup:
    keyboard = [
        [
            InlineKeyboardButton("🚀 PLACE TRADE", callback_data="trade_confirm_yes"),
            InlineKeyboardButton("❌ CANCEL", callback_data="trade_cancel")
        ]
    ]
    return InlineKeyboardMarkup(keyboard)

# --- Conversation handlers ---

@restricted
async def start_trade_flow(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Entry point for the trade wizard (triggered by command /trade, reply button or callback)."""
    context.user_data.clear()
    
    msg_text = "🚀 *Open New Position*\nSelect a symbol to trade or write one:"
    
    if update.callback_query:
        query = update.callback_query
        await query.answer()
        await query.edit_message_text(text=msg_text, parse_mode="Markdown", reply_markup=get_symbol_keyboard())
    else:
        await update.message.reply_text(msg_text, parse_mode="Markdown", reply_markup=get_symbol_keyboard())
        
    return SELECT_SYMBOL

async def cb_select_symbol(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """User selected a symbol via button."""
    query = update.callback_query
    await query.answer()
    
    selection = query.data.split("_")[1]
    if selection == "other":
        await query.edit_message_text(
            text="✍️ *Enter Symbol Name*\nPlease type the exact symbol name (e.g. `XAUUSD`, `EURUSD`):",
            parse_mode="Markdown"
        )
        return SELECT_SYMBOL
        
    context.user_data["symbol"] = selection
    await query.edit_message_text(
        text=f"🔣 *Symbol:* `{selection}`\n\nSelect order direction:",
        parse_mode="Markdown",
        reply_markup=get_direction_keyboard()
    )
    return SELECT_DIRECTION

async def txt_select_symbol(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """User typed custom symbol name."""
    symbol = update.message.text.upper().strip()
    
    sym_info = await asyncio.to_thread(get_symbol_info, symbol)
    if not sym_info:
        await update.message.reply_text(
            f"❌ Symbol `{symbol}` is not recognized or not available on MT5. Please try again:"
        )
        return SELECT_SYMBOL
        
    context.user_data["symbol"] = symbol
    await update.message.reply_text(
        f"🔣 *Symbol:* `{symbol}`\n\nSelect order direction:",
        parse_mode="Markdown",
        reply_markup=get_direction_keyboard()
    )
    return SELECT_DIRECTION

async def cb_select_direction(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """User selected BUY or SELL."""
    query = update.callback_query
    await query.answer()
    
    direction = query.data.split("_")[1]
    context.user_data["direction"] = direction
    
    await query.edit_message_text(
        text=f"🔣 *Symbol:* `{context.user_data['symbol']}`\n↕️ *Direction:* `{direction}`\n\nChoose lot size mode:",
        parse_mode="Markdown",
        reply_markup=get_lot_type_keyboard()
    )
    return SELECT_LOT_TYPE

async def cb_select_lot_type(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """User chose manual or risk-based lots. Display presets."""
    query = update.callback_query
    await query.answer()
    
    ltype = query.data.split("_")[1]
    context.user_data["lot_type"] = ltype
    
    if ltype == "manual":
        await query.edit_message_text(
            text="📏 *Select Manual Lot Size Preset* or type custom value:",
            parse_mode="Markdown",
            reply_markup=get_preset_lots_keyboard()
        )
        return INPUT_LOT_MANUAL
    else:
        await query.edit_message_text(
            text="🛡️ *Select Account Risk Percentage* or type custom value:",
            parse_mode="Markdown",
            reply_markup=get_preset_risk_keyboard()
        )
        return INPUT_RISK_PCT

# --- Presets Callbacks ---

async def cb_preset_lots(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Processes preset lot size button clicks."""
    query = update.callback_query
    await query.answer()
    
    choice = query.data.split("_")[1]
    if choice == "custom":
        await query.edit_message_text(
            text="✍️ *Enter Custom Lot Size*\nPlease type your lot volume (e.g. `0.05`):",
            parse_mode="Markdown"
        )
        return INPUT_LOT_MANUAL
        
    lots = float(choice)
    context.user_data["lots"] = lots
    
    await query.edit_message_text(
        text=(
            f"📏 *Lot Size Selected:* `{lots:.2f}`\n\n"
            f"🔴 *Enter Stop Loss (SL) Price*\n"
            f"Please type the absolute price level for your Stop Loss (e.g., `2415.50`):"
        ),
        parse_mode="Markdown",
        reply_markup=get_sl_keyboard(can_skip=True)
    )
    return INPUT_SL

async def cb_preset_risk(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Processes preset risk button clicks."""
    query = update.callback_query
    await query.answer()
    
    choice = query.data.split("_")[1]
    if choice == "custom":
        await query.edit_message_text(
            text="✍️ *Enter Custom Risk Percentage*\nPlease type target risk percentage (e.g. `1.5`):",
            parse_mode="Markdown"
        )
        return INPUT_RISK_PCT
        
    risk = float(choice)
    context.user_data["risk_pct"] = risk
    
    await query.edit_message_text(
        text=(
            f"🛡️ *Risk Selected:* `{risk:.1f}%` of equity\n\n"
            f"🔴 *Enter Stop Loss (SL) Price*\n"
            f"A Stop Loss price is **mandatory** for risk-based lot sizing.\n"
            f"Please type the absolute price level for your Stop Loss (e.g., `2415.50`):"
        ),
        parse_mode="Markdown",
        reply_markup=get_sl_keyboard(can_skip=False)
    )
    return INPUT_SL

# --- Text entries fallbacks ---

async def txt_lot_manual(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """User typed manual lot size."""
    try:
        val = float(update.message.text.strip())
        if val <= 0:
            raise ValueError()
    except ValueError:
        await update.message.reply_text("❌ Invalid entry. Please enter a valid lot size (e.g. `0.05`):")
        return INPUT_LOT_MANUAL
        
    context.user_data["lots"] = val
    
    await update.message.reply_text(
        text=(
            f"📏 *Lot Size:* `{val}`\n\n"
            f"🔴 *Enter Stop Loss (SL) Price*\n"
            f"Please type the absolute price level for your Stop Loss (e.g., `2415.50`):"
        ),
        parse_mode="Markdown",
        reply_markup=get_sl_keyboard(can_skip=True)
    )
    return INPUT_SL

async def txt_risk_pct(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """User typed risk percentage."""
    try:
        val = float(update.message.text.strip())
        if val <= 0 or val > 20:
            await update.message.reply_text("❌ Please enter a risk percent between 0.1% and 20%:")
            return INPUT_RISK_PCT
    except ValueError:
        await update.message.reply_text("❌ Invalid number format. Try again (e.g. `1.5`):")
        return INPUT_RISK_PCT
        
    context.user_data["risk_pct"] = val
    
    await update.message.reply_text(
        text=(
            f"🛡️ *Risk:* `{val}%` of equity\n\n"
            f"🔴 *Enter Stop Loss (SL) Price*\n"
            f"A Stop Loss price is **mandatory** for risk-based lot sizing.\n"
            f"Please type the absolute price level for your Stop Loss (e.g. `2415.50`):"
        ),
        parse_mode="Markdown",
        reply_markup=get_sl_keyboard(can_skip=False)
    )
    return INPUT_SL

# --- Stop Loss and Take profit ---

async def cb_select_sl(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """User clicked SL skip button (sl_0)."""
    query = update.callback_query
    await query.answer()
    
    selection = query.data.split("_")[1]
    
    if selection == "0":
        if context.user_data.get("lot_type") == "risk":
            await query.edit_message_text(
                text="🚨 *Stop Loss is mandatory for risk-based lot sizing.*\nPlease enter the absolute SL price level (e.g. `2415.50`):",
                reply_markup=get_sl_keyboard(can_skip=False)
            )
            return INPUT_SL
            
        context.user_data["sl_price"] = 0.0
        
        # Move directly to TP step
        await query.edit_message_text(
            text=(
                f"🔣 *Symbol:* `{context.user_data['symbol']}`\n"
                f"📏 *Lot Size:* `{context.user_data['lots']:.2f}`\n"
                f"🔴 *SL:* `None`\n\n"
                f"🟢 *Enter Take Profit (TP) Price*\n"
                f"Please type the absolute price level for your Take Profit (e.g. `2480.00`):"
            ),
            parse_mode="Markdown",
            reply_markup=get_tp_keyboard(has_sl=False)
        )
        return INPUT_TP
        
    return INPUT_SL

async def txt_custom_sl(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """User typed absolute Stop Loss price."""
    symbol = context.user_data["symbol"]
    direction = context.user_data["direction"]
    
    try:
        val = float(update.message.text.strip())
        if val <= 0:
            raise ValueError()
    except ValueError:
        await update.message.reply_text("❌ Please enter a valid positive price level (e.g. `2415.50`):")
        return INPUT_SL
        
    # Query live price to validate position of SL relative to entry
    sym_info = await asyncio.to_thread(get_symbol_info, symbol)
    if not sym_info:
        await update.message.reply_text("❌ Failed to query current price from MT5. Try entering the SL price again:")
        return INPUT_SL
        
    entry_price = sym_info["ask"] if direction == "BUY" else sym_info["bid"]
    
    # Validation checks
    if direction == "BUY" and val >= entry_price:
        await update.message.reply_text(
            f"❌ *Invalid BUY Stop Loss.*\n"
            f"The Stop Loss price must be **below** the current entry price (current Ask: `{entry_price}`).\n"
            f"Please enter a valid price lower than `{entry_price}`:",
            parse_mode="Markdown"
        )
        return INPUT_SL
    elif direction == "SELL" and val <= entry_price:
        await update.message.reply_text(
            f"❌ *Invalid SELL Stop Loss.*\n"
            f"The Stop Loss price must be **above** the current entry price (current Bid: `{entry_price}`).\n"
            f"Please enter a valid price higher than `{entry_price}`:",
            parse_mode="Markdown"
        )
        return INPUT_SL
        
    context.user_data["sl_price"] = val
    context.user_data["entry_price"] = entry_price
    
    # Calculate risk-based lot size if requested
    if context.user_data.get("lot_type") == "risk":
        risk_pct = context.user_data["risk_pct"]
        sl_delta = abs(entry_price - val)
        
        lots = await asyncio.to_thread(calculate_lot_size_by_delta, symbol, risk_pct, sl_delta)
        if not lots:
            await update.message.reply_text(
                "❌ *Error calculating risk lots.*\nCould not fetch contract ticks. Defaulting to manual lot: `0.01` lots.",
                parse_mode="Markdown"
            )
            context.user_data["lots"] = 0.01
        else:
            context.user_data["lots"] = lots
            
    await update.message.reply_text(
        text=(
            f"🔣 *Symbol:* `{symbol}`\n"
            f"📏 *Lot Size:* `{context.user_data['lots']:.2f}`\n"
            f"🔴 *SL:* `{val}`\n\n"
            f"🟢 *Enter Take Profit (TP) Price*\n"
            f"Please type the absolute price level for your Take Profit (e.g. `2480.00`), "
            f"or select an RR ratio button below:"
        ),
        parse_mode="Markdown",
        reply_markup=get_tp_keyboard(has_sl=True)
    )
    return INPUT_TP

async def cb_select_tp(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """User clicked TP option (tp_0 or RR ratios)."""
    query = update.callback_query
    await query.answer()
    
    selection = query.data.split("_")[1]
    
    if selection == "0":
        context.user_data["tp_price"] = 0.0
        return await display_confirmation(query.message, context)
        
    if selection.startswith("rr"):
        ratio = float(selection[2:])
        sl_price = context.user_data.get("sl_price", 0.0)
        
        if sl_price <= 0:
            await query.edit_message_text(
                text="❌ Cannot calculate RR ratio because Stop Loss was not set.\nPlease type the absolute TP price level:",
                reply_markup=get_tp_keyboard(has_sl=False)
            )
            return INPUT_TP
            
        symbol = context.user_data["symbol"]
        direction = context.user_data["direction"]
        
        # Retrieve live prices to calculate relative TP
        sym_info = await asyncio.to_thread(get_symbol_info, symbol)
        if not sym_info:
            await query.edit_message_text("❌ Failed to query prices from MT5. Type absolute TP price level:")
            return INPUT_TP
            
        entry_price = sym_info["ask"] if direction == "BUY" else sym_info["bid"]
        sl_delta = abs(entry_price - sl_price)
        
        if direction == "BUY":
            tp_val = entry_price + (sl_delta * ratio)
        else:
            tp_val = entry_price - (sl_delta * ratio)
            
        tp_val = round(tp_val, sym_info["digits"])
        context.user_data["tp_price"] = tp_val
        return await display_confirmation(query.message, context)
        
    return INPUT_TP

async def txt_custom_tp(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """User typed custom absolute TP price."""
    symbol = context.user_data["symbol"]
    direction = context.user_data["direction"]
    
    try:
        val = float(update.message.text.strip())
        if val <= 0:
            raise ValueError()
    except ValueError:
        await update.message.reply_text("❌ Please enter a valid positive price level (e.g. `2480.00`):")
        return INPUT_TP
        
    sym_info = await asyncio.to_thread(get_symbol_info, symbol)
    if not sym_info:
        await update.message.reply_text("❌ Failed to query current price from MT5. Try entering the TP price again:")
        return INPUT_TP
        
    entry_price = sym_info["ask"] if direction == "BUY" else sym_info["bid"]
    
    # Validation checks
    if direction == "BUY" and val <= entry_price:
        await update.message.reply_text(
            f"❌ *Invalid BUY Take Profit.*\n"
            f"The Take Profit price must be **above** the current entry price (current Ask: `{entry_price}`).\n"
            f"Please enter a valid price higher than `{entry_price}`:",
            parse_mode="Markdown"
        )
        return INPUT_TP
    elif direction == "SELL" and val >= entry_price:
        await update.message.reply_text(
            f"❌ *Invalid SELL Take Profit.*\n"
            f"The Take Profit price must be **below** the current entry price (current Bid: `{entry_price}`).\n"
            f"Please enter a valid price lower than `{entry_price}`:",
            parse_mode="Markdown"
        )
        return INPUT_TP
        
    context.user_data["tp_price"] = val
    return await display_confirmation(update.message, context)

async def display_confirmation(message, context):
    """Formats and displays the final confirmation card before placing the trade."""
    symbol    = context.user_data["symbol"]
    direction = context.user_data["direction"]
    lots      = context.user_data["lots"]
    sl_price  = context.user_data.get("sl_price", 0.0)
    tp_price  = context.user_data.get("tp_price", 0.0)

    sym_info = await asyncio.to_thread(get_symbol_info, symbol)
    if not sym_info:
        await message.reply_text(
            "❌ Failed to fetch current prices. Order cancelled.",
            reply_markup=get_main_keyboard()
        )
        return ConversationHandler.END

    digits      = sym_info["digits"]
    entry_price = sym_info["ask"] if direction == "BUY" else sym_info["bid"]
    context.user_data["entry_price"] = entry_price

    dir_emoji = "🟩 BUY  ▲" if direction == "BUY" else "🟥 SELL ▼"
    sl_txt    = f"`{sl_price:.{digits}f}`" if sl_price > 0 else "_None_"
    tp_txt    = f"`{tp_price:.{digits}f}`" if tp_price > 0 else "_None_"

    # Estimated risk in account currency (rough: delta × lots × contract size factor)
    if sl_price > 0:
        sl_delta   = abs(entry_price - sl_price)
        tick_val   = sym_info.get("trade_tick_value", 0)
        tick_size  = sym_info.get("trade_tick_size", 1)
        est_risk   = (sl_delta / tick_size) * tick_val * lots if tick_size > 0 else 0
        risk_txt   = f"≈ `{est_risk:,.2f}` {sym_info.get('currency_profit', '')}"
    else:
        risk_txt = "_No SL set_"

    confirm_text = (
        f"📋 *Order Ticket*\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🔣 *Symbol* · `{symbol}`\n"
        f"↕️ *Direction* · {dir_emoji}\n"
        f"📏 *Volume* · `{lots:.2f} Lots`\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📥 *Est. Entry* · `{entry_price:.{digits}f}`\n"
        f"🔴 *Stop Loss* · {sl_txt}\n"
        f"🟢 *Take Profit* · {tp_txt}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"⚠️ *Est. Risk* · {risk_txt}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"_Review carefully before confirming._"
    )

    if hasattr(message, "edit_text"):
        await message.edit_text(confirm_text, parse_mode="Markdown", reply_markup=get_confirm_keyboard())
    else:
        await message.reply_text(confirm_text, parse_mode="Markdown", reply_markup=get_confirm_keyboard())

    return CONFIRM_TRADE


async def cb_confirm_trade(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """User clicked 'PLACE TRADE'."""
    query = update.callback_query
    await query.answer()
    
    await query.edit_message_text(
        text="⚡ *Sending order to MetaTrader 5...*",
        parse_mode="Markdown"
    )
    
    symbol = context.user_data["symbol"]
    direction = context.user_data["direction"]
    lots = context.user_data["lots"]
    sl = context.user_data.get("sl_price", 0.0)
    tp = context.user_data.get("tp_price", 0.0)
    
    res = await asyncio.to_thread(send_market_order, symbol, direction, lots, sl, tp, comment="TG Bot trade")
    
    if res["success"]:
        pnl_emoji = "🟩" if direction == "BUY" else "🟥"
        succ_msg = (
            f"✅ *Trade Executed successfully!*\n\n"
            f"🎫 *Ticket:* `#{res['ticket']}`\n"
            f"🔣 *Symbol:* `{symbol}`\n"
            f"↕️ *Action:* {pnl_emoji} `{direction}`\n"
            f"📏 *Lots:* `{lots:.2f}`\n"
            f"🔴 *SL:* `{sl}` | 🟢 *TP:* `{tp}`"
        )
        await query.edit_message_text(succ_msg, parse_mode="Markdown", reply_markup=get_back_keyboard())
    else:
        fail_msg = f"❌ *Trade Execution Failed!*\n\n*Error details:* {res['message']}"
        await query.edit_message_text(fail_msg, parse_mode="Markdown", reply_markup=get_back_keyboard())
        
    return ConversationHandler.END

async def cb_cancel_trade(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """User clicked the cancel inline button during trade flow."""
    query = update.callback_query
    await query.answer("Order placement cancelled.")
    
    await query.edit_message_text(
        text="❌ *Order placement cancelled.*",
        parse_mode="Markdown",
        reply_markup=get_back_keyboard()
    )
    return ConversationHandler.END

async def cmd_cancel_trade(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """User typed /cancel during trade flow."""
    await update.message.reply_text(
        text="❌ *Order placement cancelled.*",
        parse_mode="Markdown",
        reply_markup=get_main_keyboard()
    )
    return ConversationHandler.END


# --- Handler Config ---

def get_trade_conv_handler():
    """Returns the configured ConversationHandler for trade wizard."""
    return ConversationHandler(
        entry_points=[
            CallbackQueryHandler(start_trade_flow, pattern="^menu_open_trade$"),
            CommandHandler("trade", start_trade_flow)
        ],
        states={
            SELECT_SYMBOL: [
                CallbackQueryHandler(cb_select_symbol, pattern=r"^sym_.*$"),
                MessageHandler(NOT_MENU_BUTTON, txt_select_symbol)
            ],
            SELECT_DIRECTION: [
                CallbackQueryHandler(cb_select_direction, pattern=r"^dir_.*$")
            ],
            SELECT_LOT_TYPE: [
                CallbackQueryHandler(cb_select_lot_type, pattern=r"^ltype_.*$")
            ],
            INPUT_LOT_MANUAL: [
                CallbackQueryHandler(cb_preset_lots, pattern=r"^lots_.*$"),
                MessageHandler(NOT_MENU_BUTTON, txt_lot_manual)
            ],
            INPUT_RISK_PCT: [
                CallbackQueryHandler(cb_preset_risk, pattern=r"^risk_.*$"),
                MessageHandler(NOT_MENU_BUTTON, txt_risk_pct)
            ],
            INPUT_SL: [
                CallbackQueryHandler(cb_select_sl, pattern=r"^sl_.*$"),
                MessageHandler(NOT_MENU_BUTTON, txt_custom_sl)
            ],
            INPUT_TP: [
                CallbackQueryHandler(cb_select_tp, pattern=r"^tp_.*$"),
                MessageHandler(NOT_MENU_BUTTON, txt_custom_tp)
            ],
            CONFIRM_TRADE: [
                CallbackQueryHandler(cb_confirm_trade, pattern="^trade_confirm_yes$")
            ]
        },
        fallbacks=[
            CallbackQueryHandler(cb_cancel_trade, pattern="^trade_cancel$"),
            CommandHandler("cancel", cmd_cancel_trade)
        ],

        allow_reentry=True
    )

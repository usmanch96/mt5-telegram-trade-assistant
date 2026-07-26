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
import MetaTrader5 as mt5
from mt5.monitor import get_symbol_info
from mt5.executor import send_market_order, send_pending_order
from bot.keyboards import get_main_keyboard, get_back_keyboard
from bot.handlers.trade import get_symbol_keyboard
from bot.middleware import restricted
from bot.conv_filters import NOT_MENU_BUTTON
from services.logger import logger

# States for Layering Conversation
(
    SELECT_SYMBOL,
    SELECT_DIRECTION,
    ENTER_TOTAL_LOTS,
    SELECT_NUM_LAYERS,
    ENTER_SL_PRICE,
    ENTER_TP_PRICE,
    CONFIRM_LAYERS
) = range(7)

# --- Keyboards ---

def get_layer_direction_keyboard() -> InlineKeyboardMarkup:
    """Returns BUY/SELL choices for layering."""
    keyboard = [
        [
            InlineKeyboardButton("🟢 BUY (Long)", callback_data="ldir_buy"),
            InlineKeyboardButton("🔴 SELL (Short)", callback_data="ldir_sell")
        ],
        [InlineKeyboardButton("❌ Cancel", callback_data="layer_cancel")]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_layer_count_keyboard() -> InlineKeyboardMarkup:
    """Preset numbers of layers."""
    keyboard = [
        [
            InlineKeyboardButton("3 Layers", callback_data="lcnt_3"),
            InlineKeyboardButton("5 Layers", callback_data="lcnt_5"),
            InlineKeyboardButton("8 Layers", callback_data="lcnt_8")
        ],
        [
            InlineKeyboardButton("10 Layers", callback_data="lcnt_10"),
            InlineKeyboardButton("✍️ Custom", callback_data="lcnt_custom")
        ],
        [InlineKeyboardButton("❌ Cancel", callback_data="layer_cancel")]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_layer_tp_keyboard() -> InlineKeyboardMarkup:
    """Take profit skipping helper button."""
    keyboard = [
        [
            InlineKeyboardButton("⏭️ Skip TP (No Target)", callback_data="ltp_skip")
        ],
        [InlineKeyboardButton("❌ Cancel", callback_data="layer_cancel")]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_layer_confirm_keyboard() -> InlineKeyboardMarkup:
    """Execution trigger keyboard."""
    keyboard = [
        [
            InlineKeyboardButton("🚀 Execute Layer Stack", callback_data="lconf_execute")
        ],
        [InlineKeyboardButton("❌ Cancel", callback_data="layer_cancel")]
    ]
    return InlineKeyboardMarkup(keyboard)

# --- Helper Math ---

def get_pip_size(symbol: str, sym_info: dict) -> float:
    """Translates pips to absolute price points using calculator specs."""
    digits = sym_info["digits"]
    point = sym_info["point"]
    tick_size = sym_info["trade_tick_size"]
    
    is_forex = (digits == 3 or digits == 5) and any(x in symbol for x in ["USD", "EUR", "GBP", "JPY", "AUD", "CAD", "NZD", "CHF"])
    if is_forex:
        return 10 * point
    else:
        return 10 * tick_size

# --- Handler Functions ---

@restricted
async def cmd_start_layer_wizard(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Starts the Progressive Layering wizard (via command or persistent button)."""
    # Clean previous state data
    for k in list(context.user_data.keys()):
        if k.startswith("layer_"):
            context.user_data.pop(k)
            
    text = (
        "🥞 *Progressive Layering Wizard*\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "Places multiple entries at calculated intervals\n"
        "(1 market order + pending limits) to improve your average entry.\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "👇 Select your target symbol:"
    )
    
    if update.callback_query:
        query = update.callback_query
        await query.answer()
        await query.edit_message_text(text=text, parse_mode="Markdown", reply_markup=get_symbol_keyboard())
    else:
        await update.message.reply_text(text=text, parse_mode="Markdown", reply_markup=get_symbol_keyboard())
        
    return SELECT_SYMBOL

async def cb_layer_symbol_select(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Saves symbol and requests trade direction."""
    query = update.callback_query
    await query.answer()
    
    symbol = query.data.split("_")[1].upper()
    context.user_data["layer_symbol"] = symbol
    
    await query.edit_message_text(
        text=f"🔣 *Symbol:* `{symbol}`\n\n👇 *Select execution direction:*",
        parse_mode="Markdown",
        reply_markup=get_layer_direction_keyboard()
    )
    return SELECT_DIRECTION

async def cb_layer_direction_select(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Saves direction and prompts for total lot size."""
    query = update.callback_query
    await query.answer()
    
    direction = query.data.split("_")[1].upper() # BUY or SELL
    context.user_data["layer_direction"] = direction
    
    symbol = context.user_data["layer_symbol"]
    
    await query.edit_message_text(
        text=(
            f"🔣 *Symbol:* `{symbol}` | *Direction:* `{direction}`\n\n"
            f"✍️ *Type Total Lot Volume:* (e.g. `0.10`):"
        ),
        parse_mode="Markdown"
    )
    return ENTER_TOTAL_LOTS

async def txt_layer_total_lots(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Validates and saves total lot volume."""
    text = update.message.text.strip()
    try:
        total_lots = float(text)
        if total_lots < 0.01:
            raise ValueError()
    except ValueError:
        await update.message.reply_text("❌ Total lot size must be a positive number of at least `0.01`. Please try again:")
        return ENTER_TOTAL_LOTS
        
    context.user_data["layer_total_lots"] = total_lots
    symbol = context.user_data["layer_symbol"]
    direction = context.user_data["layer_direction"]
    
    await update.message.reply_text(
        text=(
            f"🔣 *Symbol:* `{symbol}` | *Direction:* `{direction}`\n"
            f"💰 *Total Volume:* `{total_lots:.2f} Lots`\n\n"
            f"👇 *Select number of entry layers:* "
        ),
        parse_mode="Markdown",
        reply_markup=get_layer_count_keyboard()
    )
    return SELECT_NUM_LAYERS

async def cb_layer_count_select(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Processes layers count selection buttons."""
    query = update.callback_query
    await query.answer()
    
    choice = query.data.split("_")[1]
    
    if choice == "custom":
        await query.edit_message_text(
            text="✍️ *Type custom layers count* (e.g. `6` or `10`):",
            parse_mode="Markdown"
        )
        return SELECT_NUM_LAYERS
        
    num_layers = int(choice)
    return await save_layer_count_and_proceed(query.message, num_layers, context, is_callback=True)

async def txt_layer_count_custom(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Processes custom typed layers count."""
    text = update.message.text.strip()
    try:
        num_layers = int(text)
        if num_layers < 2 or num_layers > 20:
            raise ValueError()
    except ValueError:
        await update.message.reply_text("❌ Layers count must be a whole number between `2` and `20`. Try again:")
        return SELECT_NUM_LAYERS
        
    return await save_layer_count_and_proceed(update.message, num_layers, context, is_callback=False)

async def save_layer_count_and_proceed(message, num_layers: int, context: ContextTypes.DEFAULT_TYPE, is_callback: bool) -> int:
    """Helper to validate lot size split and prompt for Stop Loss price."""
    symbol = context.user_data["layer_symbol"]
    total_lots = context.user_data["layer_total_lots"]
    
    # Fetch symbol specs to align lot step dynamically
    sym_info = await asyncio.to_thread(get_symbol_info, symbol)
    if not sym_info:
        volume_min = 0.01
        volume_max = 500.0
        volume_step = 0.01
    else:
        volume_min = sym_info["volume_min"]
        volume_max = sym_info["volume_max"]
        volume_step = sym_info["volume_step"]
        
    raw_lot_per_layer = total_lots / num_layers
    
    # Round to the nearest volume step
    lot_per_layer = round(raw_lot_per_layer / volume_step) * volume_step
    
    # Determine decimals formatting
    step_str = f"{volume_step:.8f}".rstrip("0")
    decimals = len(step_str.split(".")[1]) if "." in step_str else 0
    lot_per_layer = round(lot_per_layer, decimals)
    
    if lot_per_layer < volume_min:
        text = (
            f"⚠️ *Volume too low for {num_layers} layers.*\n"
            f"Dividing `{total_lots:.2f}` lots into `{num_layers}` layers equals `{raw_lot_per_layer:.4f}` lots per trade.\n"
            f"After aligning to your broker's volume step (`{volume_step}`), the size becomes `{lot_per_layer:.{decimals}f}` Lots, which is below the broker minimum of `{volume_min}` lots.\n\n"
            f"Please enter or select a smaller number of layers:"
        )
        if is_callback:
            await message.edit_text(text, parse_mode="Markdown", reply_markup=get_layer_count_keyboard())
        else:
            await message.reply_text(text, parse_mode="Markdown", reply_markup=get_layer_count_keyboard())
        return SELECT_NUM_LAYERS
        
    context.user_data["layer_count"] = num_layers
    context.user_data["layer_lot_size"] = lot_per_layer
    
    direction = context.user_data["layer_direction"]
    
    # Recalculate total aligned volume for the matrix
    aligned_total_volume = lot_per_layer * num_layers
    context.user_data["layer_total_lots"] = aligned_total_volume
    
    prompt = (
        f"🔣 *Symbol:* `{symbol}` | *Direction:* `{direction}`\n"
        f"💰 *Aligned Volume:* `{aligned_total_volume:.{decimals}f} Lots` (`{num_layers}` layers of `{lot_per_layer:.{decimals}f}` lots)\n\n"
        f"✍️ *Type absolute Stop Loss (SL) price level:* (e.g., `2415.00`):"
    )
    
    if is_callback:
        await message.edit_text(prompt, parse_mode="Markdown")
    else:
        await message.reply_text(prompt, parse_mode="Markdown")
    return ENTER_SL_PRICE

async def txt_layer_sl_price(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Validates absolute Stop Loss price and computes pricing step spacing automatically."""
    text = update.message.text.strip()
    try:
        sl = float(text)
        if sl <= 0:
            raise ValueError()
    except ValueError:
        await update.message.reply_text("❌ Stop Loss must be a valid positive price. Try again:")
        return ENTER_SL_PRICE
        
    symbol = context.user_data["layer_symbol"]
    direction = context.user_data["layer_direction"]
    num_layers = context.user_data["layer_count"]
    total_lots = context.user_data["layer_total_lots"]
    
    # 1. Fetch current price
    sym_info = await asyncio.to_thread(get_symbol_info, symbol)
    if not sym_info:
        await update.message.reply_text("❌ Failed to query symbol specs from broker. Try again:")
        return ENTER_SL_PRICE
        
    tick = await asyncio.to_thread(mt5.symbol_info_tick, symbol)
    if not tick:
        await update.message.reply_text("❌ Failed to fetch current price. Try again:")
        return ENTER_SL_PRICE
        
    current_price = tick.ask if direction == "BUY" else tick.bid
    
    # 2. Validate Stop Loss direction
    if direction == "BUY" and sl >= current_price:
        await update.message.reply_text(
            f"❌ *Invalid BUY Stop Loss.*\n"
            f"SL (`{sl}`) must be **below** the current market entry price (`{current_price:.5f}`).\n"
            f"Please enter the price again:"
        )
        return ENTER_SL_PRICE
    elif direction == "SELL" and sl <= current_price:
        await update.message.reply_text(
            f"❌ *Invalid SELL Stop Loss.*\n"
            f"SL (`{sl}`) must be **above** the current market entry price (`{current_price:.5f}`).\n"
            f"Please enter the price again:"
        )
        return ENTER_SL_PRICE
        
    # 3. Calculate price step and pip spacing automatically
    price_delta = abs(current_price - sl)
    price_step = price_delta / num_layers
    
    pip_size = get_pip_size(symbol, sym_info)
    pip_step = price_step / pip_size
    
    context.user_data["layer_sl_price"] = sl
    context.user_data["layer_price_step"] = price_step
    context.user_data["layer_pip_step"] = pip_step
    
    await update.message.reply_text(
        text=(
            f"🔣 *Symbol:* `{symbol}` | *Direction:* `{direction}`\n"
            f"💰 *Total Volume:* `{total_lots:.2f} Lots` (`{num_layers}` layers)\n"
            f"🔴 *Stop Loss:* `{sl:.5f}`\n"
            f"📏 *Calculated Spacing:* `{pip_step:.1f} Pips` per layer\n\n"
            f"✍️ *Type absolute Take Profit (TP) price level* or click Skip below:"
        ),
        parse_mode="Markdown",
        reply_markup=get_layer_tp_keyboard()
    )
    return ENTER_TP_PRICE

async def txt_layer_tp_price(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Validates and saves the Take Profit price, then triggers confirmation."""
    text = update.message.text.strip()
    try:
        tp = float(text)
        if tp <= 0:
            raise ValueError()
    except ValueError:
        await update.message.reply_text("❌ Take Profit must be a positive price level. Try again:")
        return ENTER_TP_PRICE
        
    symbol = context.user_data["layer_symbol"]
    direction = context.user_data["layer_direction"]
    
    # Validate TP direction
    tick = await asyncio.to_thread(mt5.symbol_info_tick, symbol)
    if not tick:
        await update.message.reply_text("❌ Failed to query current price. Try again:")
        return ENTER_TP_PRICE
        
    current_price = tick.ask if direction == "BUY" else tick.bid
    
    if direction == "BUY" and tp <= current_price:
        await update.message.reply_text(
            f"❌ *Invalid BUY Take Profit.*\n"
            f"TP (`{tp}`) must be **above** the entry price (`{current_price:.5f}`).\n"
            f"Try again:"
        )
        return ENTER_TP_PRICE
    elif direction == "SELL" and tp >= current_price:
        await update.message.reply_text(
            f"❌ *Invalid SELL Take Profit.*\n"
            f"TP (`{tp}`) must be **below** the entry price (`{current_price:.5f}`).\n"
            f"Try again:"
        )
        return ENTER_TP_PRICE
        
    context.user_data["layer_tp_price"] = tp
    return await show_layer_confirmation(update.message, context, is_callback=False)

async def cb_layer_tp_skip(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Skips TP selection and goes to confirmation."""
    query = update.callback_query
    await query.answer()
    
    context.user_data["layer_tp_price"] = 0.0
    return await show_layer_confirmation(query.message, context, is_callback=True)

async def show_layer_confirmation(message, context: ContextTypes.DEFAULT_TYPE, is_callback: bool) -> int:
    """Generates the breakdown matrix of entries and requests confirmation."""
    symbol       = context.user_data["layer_symbol"]
    direction    = context.user_data["layer_direction"]
    total_lots   = context.user_data["layer_total_lots"]
    num_layers   = context.user_data["layer_count"]
    lot_per_layer = context.user_data["layer_lot_size"]
    pip_step     = context.user_data["layer_pip_step"]
    price_step   = context.user_data["layer_price_step"]
    sl           = context.user_data["layer_sl_price"]
    tp           = context.user_data["layer_tp_price"]

    sym_info = await asyncio.to_thread(get_symbol_info, symbol)
    digits = sym_info["digits"] if sym_info else 5

    tick = await asyncio.to_thread(mt5.symbol_info_tick, symbol)
    current_price = tick.ask if direction == "BUY" else tick.bid

    dir_emoji = "🟩" if direction == "BUY" else "🟥"
    sl_txt = f"`{sl:.{digits}f}`"
    tp_txt = f"`{tp:.{digits}f}`" if tp > 0 else "`None`"

    lines = [
        "🥞 *Layer Stack Confirmation*",
        "━━━━━━━━━━━━━━━━━━━━",
        f"🔣 *Symbol* · `{symbol}`  {dir_emoji} `{direction}`",
        f"📊 *Volume* · `{total_lots:.2f} Lots` across `{num_layers}` entries (`{lot_per_layer:.2f}L` each)",
        f"🔴 *Stop Loss* · {sl_txt}  🟢 *Take Profit* · {tp_txt}",
        f"📍 *Spacing* · `{pip_step:.1f} Pips` · `{price_step:.{digits}f}` pts per layer",
        "━━━━━━━━━━━━━━━━━━━━",
        "*#   Type             Price*",
    ]

    # Generate the entry grid matrix
    for idx in range(num_layers):
        price_entry = current_price - idx * price_step if direction == "BUY" else current_price + idx * price_step
        if idx == 0:
            type_str = f"Market {direction[:1]}" + ("UY" if direction == "BUY" else "ELL")
        else:
            type_str = f"{'Buy' if direction == 'BUY' else 'Sell'} Limit"
        lines.append(f"`{idx+1:02d}` · `{type_str:<12}` · `{price_entry:.{digits}f}`")

    lines.append("━━━━━━━━━━━━━━━━━━━━")
    lines.append("_Layer 1 fires as a market order. Layers 2+ set as GTC pending limits._")

    confirm_text = "\n".join(lines)

    if is_callback:
        await message.edit_text(confirm_text, parse_mode="Markdown", reply_markup=get_layer_confirm_keyboard())
    else:
        await message.reply_text(confirm_text, parse_mode="Markdown", reply_markup=get_layer_confirm_keyboard())

    return CONFIRM_LAYERS

async def cb_layer_execute(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Fires all orders to MT5 in a background thread task group."""
    query = update.callback_query
    await query.answer()

    symbol      = context.user_data["layer_symbol"]
    direction   = context.user_data["layer_direction"]
    num_layers  = context.user_data["layer_count"]
    lot_per_layer = context.user_data["layer_lot_size"]
    price_step  = context.user_data["layer_price_step"]
    sl          = context.user_data["layer_sl_price"]
    tp          = context.user_data["layer_tp_price"]

    sym_info = await asyncio.to_thread(get_symbol_info, symbol)
    digits = sym_info["digits"] if sym_info else 5

    tick = await asyncio.to_thread(mt5.symbol_info_tick, symbol)
    current_price = tick.ask if direction == "BUY" else tick.bid

    await query.edit_message_text(
        text=f"⚡ *Firing {num_layers}-layer stack for {symbol}...*",
        parse_mode="Markdown"
    )

    success_count = 0
    layer_results = []  # List of (label, success, detail)

    # 1. Layer 1 — Market order
    res_market = await asyncio.to_thread(
        send_market_order,
        symbol=symbol, direction=direction, volume=lot_per_layer,
        sl=sl, tp=tp, comment="TG Layer 1"
    )
    if res_market["success"]:
        success_count += 1
        layer_results.append(("✅ L1", f"Market {direction} @ `{current_price:.{digits}f}`"))
    else:
        layer_results.append(("❌ L1", f"Market failed · {res_market['message']}"))

    # 2. Layers 2+ — Pending Limit orders
    for idx in range(1, num_layers):
        if direction == "BUY":
            price_entry = current_price - idx * price_step
            order_dir   = "BUY_LIMIT"
        else:
            price_entry = current_price + idx * price_step
            order_dir   = "SELL_LIMIT"

        res_pending = await asyncio.to_thread(
            send_pending_order,
            symbol=symbol, direction=order_dir, volume=lot_per_layer,
            price=price_entry, sl=sl, tp=tp, comment=f"TG Layer {idx+1}"
        )
        lbl = f"L{idx+1}"
        if res_pending["success"]:
            success_count += 1
            layer_results.append(("✅ " + lbl, f"Limit @ `{price_entry:.{digits}f}`"))
        else:
            layer_results.append(("❌ " + lbl, f"Limit failed · {res_pending['message']}"))

    # 3. Build execution report
    all_ok     = success_count == num_layers
    hdr_emoji  = "✅" if all_ok else ("⚠️" if success_count > 0 else "❌")
    tally_bar  = "🟩" * success_count + "⬜" * (num_layers - success_count)

    report_lines = [
        f"{hdr_emoji} *Layer Stack Report*",
        "━━━━━━━━━━━━━━━━━━━━",
        f"🔣 `{symbol}` · {direction}  · `{success_count}/{num_layers}` placed",
        f"{tally_bar}",
        "━━━━━━━━━━━━━━━━━━━━",
    ]
    for status, detail in layer_results:
        report_lines.append(f"`{status}` · {detail}")

    report_lines.append("━━━━━━━━━━━━━━━━━━━━")
    report_lines.append("_Pending orders remain active as GTC until triggered or cancelled._")

    await query.edit_message_text(
        text="\n".join(report_lines),
        parse_mode="Markdown",
        reply_markup=get_back_keyboard("menu_main")
    )
    
    return ConversationHandler.END

async def cb_cancel_layer(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Cancels wizard and returns to main menu."""
    query = update.callback_query
    await query.answer("Operation cancelled.")
    await query.edit_message_text(
        text="❌ *Layering execution cancelled.*",
        parse_mode="Markdown",
        reply_markup=get_back_keyboard("menu_main")
    )
    return ConversationHandler.END

async def cmd_cancel_layer(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Abort layering conversation via command."""
    await update.message.reply_text(
        text="❌ *Layering execution cancelled.*",
        parse_mode="Markdown",
        reply_markup=get_main_keyboard()
    )
    return ConversationHandler.END

# --- Conversation Handler Configuration ---

def get_layer_conv_handler():
    """Generates configured ConversationHandler for stateful dynamic layering."""
    return ConversationHandler(
        entry_points=[
            CommandHandler("layer", cmd_start_layer_wizard),
            CallbackQueryHandler(cmd_start_layer_wizard, pattern="^menu_layering$"),
            MessageHandler(filters.Regex("^🥞 Layering Trade$"), cmd_start_layer_wizard)
        ],
        states={
            SELECT_SYMBOL: [
                CallbackQueryHandler(cb_layer_symbol_select, pattern=r"^sym_.*$")
            ],
            SELECT_DIRECTION: [
                CallbackQueryHandler(cb_layer_direction_select, pattern=r"^ldir_.*$")
            ],
            ENTER_TOTAL_LOTS: [
                MessageHandler(NOT_MENU_BUTTON, txt_layer_total_lots)
            ],
            SELECT_NUM_LAYERS: [
                CallbackQueryHandler(cb_layer_count_select, pattern=r"^lcnt_.*$"),
                MessageHandler(NOT_MENU_BUTTON, txt_layer_count_custom)
            ],
            ENTER_SL_PRICE: [
                MessageHandler(NOT_MENU_BUTTON, txt_layer_sl_price)
            ],
            ENTER_TP_PRICE: [
                CallbackQueryHandler(cb_layer_tp_skip, pattern="^ltp_skip$"),
                MessageHandler(NOT_MENU_BUTTON, txt_layer_tp_price)
            ],
            CONFIRM_LAYERS: [
                CallbackQueryHandler(cb_layer_execute, pattern="^lconf_execute$")
            ]
        },
        fallbacks=[
            CommandHandler("cancel", cmd_cancel_layer),
            CallbackQueryHandler(cb_cancel_layer, pattern="^layer_cancel$")
        ],
        allow_reentry=True
    )

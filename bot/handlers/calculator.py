import asyncio
from typing import Optional
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
from mt5.risk_calculator import (
    calculate_sl_price_from_risk,
    calculate_lot_from_fixed_risk,
    calculate_layer_risk_stack,
)
from bot.keyboards import get_back_keyboard, get_main_keyboard
from bot.middleware import restricted
from bot.conv_filters import NOT_MENU_BUTTON
from services.logger import logger

# Calculator Conversation States
(
    SELECT_CALC_MODE,
    SELECT_CALC_SYMBOL,
    SELECT_CALC_DIR,
    INPUT_CALC_LOT,
    INPUT_CALC_SL,
    INPUT_CALC_RISK,
    INPUT_CALC_LAYERS,
) = range(7)

POPULAR_SYMBOLS = ["XAUUSD", "BTCUSD", "EURUSD", "GBPUSD", "US30", "NAS100"]

def get_calc_symbol_keyboard() -> InlineKeyboardMarkup:
    """Inline keyboard for quick symbol selection in Risk Calculator."""
    buttons = [
        [
            InlineKeyboardButton("XAUUSD", callback_data="csym_XAUUSD"),
            InlineKeyboardButton("BTCUSD", callback_data="csym_BTCUSD"),
            InlineKeyboardButton("EURUSD", callback_data="csym_EURUSD"),
        ],
        [
            InlineKeyboardButton("GBPUSD", callback_data="csym_GBPUSD"),
            InlineKeyboardButton("US30", callback_data="csym_US30"),
            InlineKeyboardButton("NAS100", callback_data="csym_NAS100"),
        ],
        [InlineKeyboardButton("❌ Cancel", callback_data="calc_cancel")],
    ]
    return InlineKeyboardMarkup(buttons)

def get_calc_mode_keyboard() -> InlineKeyboardMarkup:
    """Inline keyboard choosing calculation mode."""
    buttons = [
        [InlineKeyboardButton("🎯 Fixed Lot ➔ Find SL Price", callback_data="cmode_lot_to_sl")],
        [InlineKeyboardButton("📏 Fixed SL ➔ Find Lot Size", callback_data="cmode_sl_to_lot")],
        [InlineKeyboardButton("🥞 Layer Risk Stack Allocator", callback_data="cmode_layer_stack")],
        [InlineKeyboardButton("❌ Cancel", callback_data="calc_cancel")],
    ]
    return InlineKeyboardMarkup(buttons)

def get_direction_keyboard() -> InlineKeyboardMarkup:
    """Inline keyboard for choosing direction."""
    buttons = [
        [
            InlineKeyboardButton("🟩 BUY ▲", callback_data="cdir_BUY"),
            InlineKeyboardButton("🟥 SELL ▼", callback_data="cdir_SELL"),
        ],
        [InlineKeyboardButton("❌ Cancel", callback_data="calc_cancel")],
    ]
    return InlineKeyboardMarkup(buttons)

@restricted
async def cmd_start_calculator(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Entry point for /calc command or menu button."""
    query = update.callback_query
    is_message = update.message is not None

    if query:
        await query.answer()

    context.user_data["calc"] = {}

    text = (
        "🧮 *Smart Risk & SL Calculator Assistant*\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "Choose a calculation mode:\n\n"
        "1️⃣ *Fixed Lot ➔ Find SL Price*\n"
        "   _Calculate exact SL price level for your lot size & risk amount._\n\n"
        "2️⃣ *Fixed SL ➔ Find Lot Size*\n"
        "   _Calculate exact lot size for your SL price distance & risk amount._\n\n"
        "3️⃣ *Layer Risk Stack Allocator*\n"
        "   _Calculate grid spacing & SL so total combined loss of ALL layers = risk amount._"
    )

    if is_message:
        await update.message.reply_text(text, parse_mode="Markdown", reply_markup=get_calc_mode_keyboard())
    else:
        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=get_calc_mode_keyboard())

    return SELECT_CALC_MODE

async def cb_calc_mode_select(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handles calculator mode selection."""
    query = update.callback_query
    await query.answer()

    mode = query.data.replace("cmode_", "")
    context.user_data["calc"]["mode"] = mode

    text = (
        "🔣 *Select Symbol*\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "Choose a popular symbol or type any symbol name (e.g. `XAUUSDc`, `EURUSD`):"
    )
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=get_calc_symbol_keyboard())
    return SELECT_CALC_SYMBOL

async def cb_calc_symbol_select(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Callback for symbol selection button."""
    query = update.callback_query
    await query.answer()

    sym = query.data.replace("csym_", "")
    context.user_data["calc"]["symbol"] = sym

    text = f"↔️ *Select Direction for {sym}*:"
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=get_direction_keyboard())
    return SELECT_CALC_DIR

async def txt_calc_symbol_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Text handler for custom symbol input."""
    sym = update.message.text.strip()
    sym_info = await asyncio.to_thread(get_symbol_info, sym)

    if not sym_info:
        await update.message.reply_text(
            f"❌ *Symbol `{sym}` not found in MT5.* Please try again:",
            parse_mode="Markdown",
            reply_markup=get_calc_symbol_keyboard()
        )
        return SELECT_CALC_SYMBOL

    context.user_data["calc"]["symbol"] = sym_info["name"]

    await update.message.reply_text(
        f"↔️ *Select Direction for `{sym_info['name']}`*:",
        parse_mode="Markdown",
        reply_markup=get_direction_keyboard()
    )
    return SELECT_CALC_DIR

async def cb_calc_direction_select(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Callback for direction selection."""
    query = update.callback_query
    await query.answer()

    direction = query.data.replace("cdir_", "")
    context.user_data["calc"]["direction"] = direction

    mode = context.user_data["calc"]["mode"]

    if mode == "lot_to_sl":
        await query.edit_message_text(
            "✍️ *Type Lot Size* (e.g., `0.02` or `0.10`):",
            parse_mode="Markdown",
            reply_markup=get_back_keyboard("calc_cancel")
        )
        return INPUT_CALC_LOT

    elif mode == "sl_to_lot":
        await query.edit_message_text(
            "✍️ *Type target SL Price Level* (e.g., `63500.00` or `2390.50`):",
            parse_mode="Markdown",
            reply_markup=get_back_keyboard("calc_cancel")
        )
        return INPUT_CALC_SL

    elif mode == "layer_stack":
        await query.edit_message_text(
            "🔢 *Type Number of Layers in Grid* (e.g., `3`, `5`, `8`):",
            parse_mode="Markdown",
            reply_markup=get_back_keyboard("calc_cancel")
        )
        return INPUT_CALC_LAYERS

    return ConversationHandler.END

async def txt_calc_lot_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Text handler for lot size input."""
    try:
        lot = float(update.message.text.strip())
        if lot <= 0:
            raise ValueError
        context.user_data["calc"]["lot"] = lot
    except ValueError:
        await update.message.reply_text("❌ Lot size must be a positive number (e.g., `0.02`). Try again:")
        return INPUT_CALC_LOT

    await update.message.reply_text(
        "💰 *Type Risk Amount in Dollars ($)* (e.g., `10` or `25.50`):",
        parse_mode="Markdown"
    )
    return INPUT_CALC_RISK

async def txt_calc_sl_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Text handler for SL price input."""
    try:
        sl = float(update.message.text.strip())
        if sl <= 0:
            raise ValueError
        context.user_data["calc"]["sl"] = sl
    except ValueError:
        await update.message.reply_text("❌ SL Price must be a positive number (e.g., `2390.50`). Try again:")
        return INPUT_CALC_SL

    await update.message.reply_text(
        "💰 *Type Risk Amount in Dollars ($)* (e.g., `10` or `25.50`):",
        parse_mode="Markdown"
    )
    return INPUT_CALC_RISK

async def txt_calc_layers_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Text handler for number of layers input."""
    try:
        n = int(update.message.text.strip())
        if n < 1 or n > 20:
            raise ValueError
        context.user_data["calc"]["num_layers"] = n
    except ValueError:
        await update.message.reply_text("❌ Layers count must be between 1 and 20. Try again:")
        return INPUT_CALC_LAYERS

    await update.message.reply_text(
        "💰 *Type Total Target Risk in Dollars ($)* across ALL layers (e.g., `10` or `50`):",
        parse_mode="Markdown"
    )
    return INPUT_CALC_RISK

async def txt_calc_risk_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Text handler for risk $ input and final calculation rendering."""
    try:
        risk = float(update.message.text.strip())
        if risk <= 0:
            raise ValueError
        context.user_data["calc"]["risk_amount"] = risk
    except ValueError:
        await update.message.reply_text("❌ Risk amount must be a positive number (e.g., `10`). Try again:")
        return INPUT_CALC_RISK

    cdata = context.user_data["calc"]
    mode = cdata["mode"]
    symbol = cdata["symbol"]
    direction = cdata.get("direction", "BUY")

    if mode == "lot_to_sl":
        lot = cdata["lot"]
        res = await asyncio.to_thread(calculate_sl_price_from_risk, symbol, direction, lot, risk)

        if not res:
            await update.message.reply_text("❌ *Calculation failed.* Check symbol and specs.", parse_mode="Markdown")
            return ConversationHandler.END

        digits = res["digits"]
        dir_emoji = "🟩" if direction == "BUY" else "🟥"
        dir_arrow = "▲" if direction == "BUY" else "▼"

        text = (
            f"🧮 *SL Placement Calculator*\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"𔣣 *Symbol* · `{symbol}`\n"
            f"{dir_emoji} *Direction* · `{direction}` {dir_arrow}  ·  *Lots* `{lot:.2f}`\n"
            f"💰 *Target Risk* · `${risk:,.2f}`\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"📥 *Current Price* · `{res['entry_price']:.{digits}f}`\n"
            f"🔴 *Set Stop Loss at* · `{res['sl_price']:.{digits}f}`\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"💡 _If price hits `{res['sl_price']:.{digits}f}`, loss is EXACTLY `${risk:,.2f}`!_"
        )

    elif mode == "sl_to_lot":
        target_sl = cdata["sl"]
        res = await asyncio.to_thread(calculate_lot_from_fixed_risk, symbol, target_sl, risk)

        if not res:
            await update.message.reply_text("❌ *Calculation failed.* Check SL level and symbol specs.", parse_mode="Markdown")
            return ConversationHandler.END

        digits = res["digits"]
        text = (
            f"🧮 *Lot Size Calculator*\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"𔣣 *Symbol* · `{symbol}`\n"
            f"🔴 *Target SL* · `{target_sl:.{digits}f}`\n"
            f"💰 *Target Risk* · `${risk:,.2f}`\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"📥 *Current Price* · `{res['entry_price']:.{digits}f}`\n"
            f"📏 *Recommended Lot Size* · `{res['recommended_lot']:.2f} Lots`\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"💡 _Using `{res['recommended_lot']:.2f}` lots risks EXACTLY `${risk:,.2f}`!_"
        )

    elif mode == "layer_stack":
        n_layers = cdata["num_layers"]
        res = await asyncio.to_thread(calculate_layer_risk_stack, symbol, direction, n_layers, risk)

        if not res:
            await update.message.reply_text("❌ *Calculation failed.* Check symbol specs.", parse_mode="Markdown")
            return ConversationHandler.END

        digits = res["digits"]
        dir_emoji = "🟩" if direction == "BUY" else "🟥"
        dir_arrow = "▲" if direction == "BUY" else "▼"

        grid_lines = []
        for l in res["layers"]:
            grid_lines.append(
                f"• L{l['layer_num']} ({l['type']}) @ `{l['price']:.{digits}f}` (SL Loss: `${l['loss_at_sl']:,.2f}`)"
            )

        grid_str = "\n".join(grid_lines)

        text = (
            f"🥞 *Layering Risk Stack Calculator*\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"𔣣 *Symbol* · `{symbol}`\n"
            f"{dir_emoji} *Direction* · `{direction}` {dir_arrow}\n"
            f"🔢 *Layers* · `{n_layers} entries`  ·  *Lot/Layer* `{res['lot_per_layer']:.2f}`\n"
            f"💰 *Combined Max Risk* · `${risk:,.2f}`\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🔴 *Set SL for ALL Layers at* · `{res['sl_price']:.{digits}f}`\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"⚙️ *Calculated Grid Execution Plan*:\n"
            f"{grid_str}\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"💡 _Combined Total Loss if ALL {n_layers} layers trigger & hit SL = EXACTLY `${risk:,.2f}`!_"
        )

    await update.message.reply_text(
        text=text,
        parse_mode="Markdown",
        reply_markup=get_back_keyboard("calc_cancel")
    )
    return ConversationHandler.END

async def cb_cancel_calc(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Cancels the calculator flow."""
    query = update.callback_query
    if query:
        await query.answer("Calculator closed.")
        await query.edit_message_text("❌ *Calculator closed.*", parse_mode="Markdown", reply_markup=get_main_keyboard())
    else:
        await update.message.reply_text("❌ *Calculator closed.*", parse_mode="Markdown", reply_markup=get_main_keyboard())
    return ConversationHandler.END

def get_risk_calculator_handler():
    """Returns configured ConversationHandler for Risk Calculator."""
    return ConversationHandler(
        entry_points=[
            CommandHandler("calc", cmd_start_calculator),
            CallbackQueryHandler(cmd_start_calculator, pattern="^menu_calc$"),
            MessageHandler(filters.Regex("^🧮 Risk Calculator$"), cmd_start_calculator),
        ],
        states={
            SELECT_CALC_MODE: [
                CallbackQueryHandler(cb_calc_mode_select, pattern=r"^cmode_.*$")
            ],
            SELECT_CALC_SYMBOL: [
                CallbackQueryHandler(cb_calc_symbol_select, pattern=r"^csym_.*$"),
                MessageHandler(NOT_MENU_BUTTON, txt_calc_symbol_input)
            ],
            SELECT_CALC_DIR: [
                CallbackQueryHandler(cb_calc_direction_select, pattern=r"^cdir_.*$")
            ],
            INPUT_CALC_LOT: [
                MessageHandler(NOT_MENU_BUTTON, txt_calc_lot_input)
            ],
            INPUT_CALC_SL: [
                MessageHandler(NOT_MENU_BUTTON, txt_calc_sl_input)
            ],
            INPUT_CALC_LAYERS: [
                MessageHandler(NOT_MENU_BUTTON, txt_calc_layers_input)
            ],
            INPUT_CALC_RISK: [
                MessageHandler(NOT_MENU_BUTTON, txt_calc_risk_input)
            ]
        },
        fallbacks=[
            CommandHandler("cancel", cb_cancel_calc),
            CallbackQueryHandler(cb_cancel_calc, pattern="^calc_cancel$")
        ],
        allow_reentry=True
    )

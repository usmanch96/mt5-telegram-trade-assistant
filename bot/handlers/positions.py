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
from mt5.monitor import get_active_positions, get_symbol_info
from mt5.executor import close_position, modify_position_stops
from database.models import add_trailing_stop, remove_trailing_stop
from bot.keyboards import (
    get_back_keyboard, 
    get_position_control_keyboard,
    get_stops_adjustment_keyboard,
    get_preset_trail_keyboard
)
from bot.middleware import restricted
from bot.conv_filters import NOT_MENU_BUTTON
from services.logger import logger

# States for the Position Modifications Conversation
WAITING_STOPS, WAITING_TRAIL = range(2)

def format_price(price: float, symbol: str) -> str:
    """Formats a price float with proper symbol decimals without blocking."""
    if price <= 0:
        return "_Not set_"
    s = f"{price:.5f}"
    if "." in s:
        parts = s.split(".")
        decimals = parts[1].rstrip("0")
        if not decimals:
            decimals = "00"
        elif len(decimals) < 2:
            decimals = decimals.ljust(2, "0")
        return f"`{parts[0]}.{decimals}`"
    return f"`{s}`"

def calc_position_sl_tp_pnl(p: dict, sym_info: Optional[dict]):
    """
    Calculates estimated P&L if SL or TP is hit, in account currency.
    Returns (sl_pnl, tp_pnl) floats or None if not set or info missing.
    """
    sl_pnl = None
    tp_pnl = None

    if sym_info and sym_info.get("trade_tick_size", 0) > 0:
        tick_size = sym_info["trade_tick_size"]
        tick_value = sym_info["trade_tick_value"]
        vol = p["volume"]
        entry = p["price_open"]
        pos_type = p["type"]

        if p["sl"] > 0:
            delta = (p["sl"] - entry) if pos_type == "BUY" else (entry - p["sl"])
            sl_pnl = (delta / tick_size) * tick_value * vol

        if p["tp"] > 0:
            delta = (p["tp"] - entry) if pos_type == "BUY" else (entry - p["tp"])
            tp_pnl = (delta / tick_size) * tick_value * vol

    return sl_pnl, tp_pnl

def format_position_short(p: dict, digits: int = 5, sym_info: Optional[dict] = None) -> str:
    """Formats a position into a clean, compact summary block with SL/TP risk & reward."""
    pnl_emoji = "🟢" if p["profit"] >= 0 else "🔴"
    dir_arrow  = "▲" if p["type"] == "BUY" else "▼"
    pnl_sign   = "+" if p["profit"] >= 0 else ""

    sl_pnl, tp_pnl = calc_position_sl_tp_pnl(p, sym_info)

    if p["sl"] > 0:
        if sl_pnl is not None:
            sign = "+" if sl_pnl >= 0 else ""
            sl_str = f"`{p['sl']:.{digits}f}` (`{sign}{sl_pnl:,.2f}`)"
        else:
            sl_str = f"`{p['sl']:.{digits}f}`"
    else:
        sl_str = "_None_"

    if p["tp"] > 0:
        if tp_pnl is not None:
            sign = "+" if tp_pnl >= 0 else ""
            tp_str = f"`{p['tp']:.{digits}f}` (`{sign}{tp_pnl:,.2f}`)"
        else:
            tp_str = f"`{p['tp']:.{digits}f}`"
    else:
        tp_str = "_None_"

    return (
        f"🔸 `#{p['ticket']}` *{p['symbol']}* {dir_arrow} `{p['volume']:.2f}L` {pnl_emoji} `{pnl_sign}{p['profit']:,.2f}`\n"
        f"   🔴 SL {sl_str}  🟢 TP {tp_str}"
    )

def format_position_detail(p: dict, digits: int = 5) -> str:
    """Formats a position into a detailed markdown card."""
    pnl_emoji  = "🟢" if p["profit"] >= 0 else "🔴"
    dir_emoji  = "🟩" if p["type"] == "BUY" else "🟥"
    sl_text    = f"`{p['sl']:.{digits}f}`" if p["sl"] > 0 else "_Not set_"
    tp_text    = f"`{p['tp']:.{digits}f}`" if p["tp"] > 0 else "_Not set_"
    pnl_sign   = "+" if p["profit"] >= 0 else ""

    return (
        f"💼 *Position \u00b7 #{p['ticket']}*\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🔣 *Symbol* · `{p['symbol']}`\n"
        f"{dir_emoji} *Direction* · `{p['type']}`  ·  *Lots* `{p['volume']:.2f}`\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📥 *Entry* · `{p['price_open']:.{digits}f}`\n"
        f"📲 *Current* · `{p['price_current']:.{digits}f}`\n"
        f"🔴 *Stop Loss* · {sl_text}\n"
        f"🟢 *Take Profit* · {tp_text}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🔄 *Swap* · `{p['swap']:.2f}`\n"
        f"{pnl_emoji} *Net P&L* · `{pnl_sign}{p['profit']:,.2f}`\n"
        f"💬 *Comment* · `{p['comment'] or 'None'}`\n"
    )

@restricted
async def cb_active_positions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Displays a list of all active trade positions with selection buttons."""
    query = update.callback_query

    is_message = update.message is not None
    msg_obj = update.message if is_message else query.message

    if query:
        await query.answer()

    positions = await asyncio.to_thread(get_active_positions)

    if not positions:
        text = (
            "💼 *Active Positions*\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "📭 No open positions on this account."
        )
        if is_message:
            await update.message.reply_text(text, parse_mode="Markdown", reply_markup=get_back_keyboard())
        else:
            await query.edit_message_text(text, parse_mode="Markdown", reply_markup=get_back_keyboard())
        return

    # Fetch symbol info for all open symbols
    unique_symbols = list(set(p["symbol"] for p in positions))
    sym_map = {}
    for s in unique_symbols:
        info = await asyncio.to_thread(get_symbol_info, s)
        if info:
            sym_map[s] = info

    summary_lines = ["💼 *Active Positions*", "━━━━━━━━━━━━━━━━━━━━"]
    total_pnl = 0.0
    total_sl_pnl = 0.0
    total_tp_pnl = 0.0
    has_any_sl = False
    has_any_tp = False

    keyboard_buttons = []
    current_row = []

    for pos in positions:
        s_info = sym_map.get(pos["symbol"])
        digits = s_info["digits"] if s_info else 5

        sl_pnl, tp_pnl = calc_position_sl_tp_pnl(pos, s_info)

        total_pnl += pos["profit"]
        if sl_pnl is not None:
            total_sl_pnl += sl_pnl
            has_any_sl = True
        if tp_pnl is not None:
            total_tp_pnl += tp_pnl
            has_any_tp = True

        summary_lines.append(format_position_short(pos, digits=digits, sym_info=s_info))

        btn_text = f"⚙️ #{pos['ticket']} {pos['symbol']}"
        btn = InlineKeyboardButton(btn_text, callback_data=f"pos_view_{pos['ticket']}")
        current_row.append(btn)

        if len(current_row) == 2:
            keyboard_buttons.append(current_row)
            current_row = []

    if current_row:
        keyboard_buttons.append(current_row)

    pnl_emoji = "🟢" if total_pnl >= 0 else "🔴"
    pnl_sign  = "+" if total_pnl >= 0 else ""

    sl_total_sign = "+" if total_sl_pnl >= 0 else ""
    tp_total_sign = "+" if total_tp_pnl >= 0 else ""

    summary_lines.append("━━━━━━━━━━━━━━━━━━━━")
    summary_lines.append(f"📊 *Float P&L* · {pnl_emoji} `{pnl_sign}{total_pnl:,.2f}`")

    if has_any_sl:
        sl_emoji = "🟢" if total_sl_pnl >= 0 else "🔴"
        summary_lines.append(f"🔴 *Total SL Risk* · {sl_emoji} `{sl_total_sign}{total_sl_pnl:,.2f}`")
    else:
        summary_lines.append("🔴 *Total SL Risk* · _No SL set_")

    if has_any_tp:
        summary_lines.append(f"🟢 *Total TP Target* · 🟢 `{tp_total_sign}{total_tp_pnl:,.2f}`")
    else:
        summary_lines.append("🟢 *Total TP Target* · _No TP set_")

    summary_lines.append("")
    summary_lines.append("👇 _Tap a ticket to manage:_")

    keyboard_buttons.append([InlineKeyboardButton("⬅️ Main Menu", callback_data="menu_main")])

    if is_message:
        await update.message.reply_text(
            text="\n".join(summary_lines),
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard_buttons)
        )
    else:
        await query.edit_message_text(
            text="\n".join(summary_lines),
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard_buttons)
        )

@restricted
async def cb_view_position(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Displays detailed controls for a specific position ticket."""
    query = update.callback_query
    await query.answer()

    try:
        ticket = int(query.data.split("_")[2])
    except (IndexError, ValueError):
        await query.edit_message_text(
            text="❌ Invalid ticket reference.",
            reply_markup=get_back_keyboard("menu_trades")
        )
        return

    positions = await asyncio.to_thread(get_active_positions)
    target_pos = next((p for p in positions if p["ticket"] == ticket), None)

    if not target_pos:
        await query.edit_message_text(
            text=f"❌ Position `#{ticket}` was closed or not found.",
            parse_mode="Markdown",
            reply_markup=get_back_keyboard("menu_trades")
        )
        return

    # Fetch symbol info for correct decimal precision
    sym_info = await asyncio.to_thread(get_symbol_info, target_pos["symbol"])
    digits = sym_info["digits"] if sym_info else 5

    await query.edit_message_text(
        text=format_position_detail(target_pos, digits=digits),
        parse_mode="Markdown",
        reply_markup=get_position_control_keyboard(ticket)
    )

# --- Action Executions (Close, BE, Partials) ---

@restricted
async def cb_close_position_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Closes an active position completely."""
    query = update.callback_query
    await query.answer()
    
    ticket = int(query.data.split("_")[2])
    await query.edit_message_text(f"⚡ *Closing ticket #{ticket}...*", parse_mode="Markdown")
    
    res = await asyncio.to_thread(close_position, ticket)
    
    if res["success"]:
        await asyncio.to_thread(remove_trailing_stop, ticket)
        await query.edit_message_text(f"✅ *Position closed successfully!*\n{res['message']}", parse_mode="Markdown", reply_markup=get_back_keyboard("menu_trades"))
    else:
        await query.edit_message_text(f"❌ *Failed to close position.*\n{res['message']}", parse_mode="Markdown", reply_markup=get_back_keyboard("menu_trades"))

@restricted
async def cb_be_position_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Moves Stop Loss to entry price (Break-Even)."""
    query = update.callback_query
    await query.answer()
    
    ticket = int(query.data.split("_")[2])
    await query.edit_message_text(f"⚡ *Moving stop to Break-Even for ticket #{ticket}...*", parse_mode="Markdown")
    
    positions = await asyncio.to_thread(get_active_positions)
    pos = next((p for p in positions if p["ticket"] == ticket), None)
    
    if not pos:
        await query.edit_message_text(f"❌ Position `#{ticket}` not found to apply Break-Even.", reply_markup=get_back_keyboard("menu_trades"))
        return
        
    entry = pos["price_open"]
    tp = pos["tp"]
    
    res = await asyncio.to_thread(modify_position_stops, ticket, sl=entry, tp=tp)
    
    if res["success"]:
        await query.edit_message_text(f"🛡️ *Stop Loss moved to Break-Even!*\nSL set to `{entry:.5f}`.", parse_mode="Markdown", reply_markup=get_back_keyboard("menu_trades"))
    else:
        await query.edit_message_text(f"❌ *Failed to adjust Stop Loss.*\n{res['message']}", parse_mode="Markdown", reply_markup=get_back_keyboard("menu_trades"))

@restricted
async def cb_partial_close_options(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Shows options for partial closing of a position."""
    query = update.callback_query
    await query.answer()
    
    ticket = int(query.data.split("_")[2])
    
    keyboard = [
        [
            InlineKeyboardButton("25% Close", callback_data=f"pos_partexec_{ticket}_0.25"),
            InlineKeyboardButton("50% Close", callback_data=f"pos_partexec_{ticket}_0.50"),
            InlineKeyboardButton("75% Close", callback_data=f"pos_partexec_{ticket}_0.75")
        ],
        [
            InlineKeyboardButton("⬅️ Cancel", callback_data=f"pos_view_{ticket}")
        ]
    ]
    
    await query.edit_message_text(
        text=f"🥞 *Partial Close Position #{ticket}*\nChoose volume percentage to close:",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

@restricted
async def cb_partial_close_execute(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Executes the partial close transaction."""
    query = update.callback_query
    await query.answer()
    
    data_parts = query.data.split("_")
    ticket = int(data_parts[2])
    ratio = float(data_parts[3])
    
    await query.edit_message_text(f"⚡ *Executing partial close of {int(ratio * 100)}% for ticket #{ticket}...*", parse_mode="Markdown")
    
    positions = await asyncio.to_thread(get_active_positions)
    pos = next((p for p in positions if p["ticket"] == ticket), None)
    
    if not pos:
        await query.edit_message_text(f"❌ Position `#{ticket}` was already closed.", reply_markup=get_back_keyboard("menu_trades"))
        return
        
    symbol = pos["symbol"]
    current_vol = pos["volume"]
    
    sym_info = await asyncio.to_thread(get_symbol_info, symbol)
    if not sym_info:
        await query.edit_message_text("❌ Could not load symbol info constraints.", reply_markup=get_back_keyboard("menu_trades"))
        return
        
    volume_step = sym_info["volume_step"]
    volume_min = sym_info["volume_min"]
    
    raw_vol = current_vol * ratio
    close_vol = max(volume_min, round(raw_vol / volume_step) * volume_step)
    close_vol = round(close_vol, 2)
    
    if close_vol >= current_vol:
        close_vol = current_vol
        
    res = await asyncio.to_thread(close_position, ticket, close_vol)
    
    if res["success"]:
        if close_vol == current_vol:
            await asyncio.to_thread(remove_trailing_stop, ticket)
            
        await query.edit_message_text(
            text=f"🥞 *Partial Close Completed!*\n\nClosed `{close_vol:.2f}` lots of ticket `#{ticket}`. Remaining volume: `{current_vol - close_vol:.2f}` lots.",
            parse_mode="Markdown",
            reply_markup=get_back_keyboard("menu_trades")
        )
    else:
        await query.edit_message_text(f"❌ *Failed to partial close position.*\n{res['message']}", parse_mode="Markdown", reply_markup=get_back_keyboard("menu_trades"))

# --- Stop Loss and Take Profit Adjustments menus ---

@restricted
async def cb_edit_stops_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Displays the interactive button-based stops adjustment keyboard."""
    query = update.callback_query
    await query.answer()
    
    ticket = int(query.data.split("_")[3])
    
    positions = await asyncio.to_thread(get_active_positions)
    pos = next((p for p in positions if p["ticket"] == ticket), None)
    
    if not pos:
        await query.edit_message_text("❌ Trade no longer active.", reply_markup=get_back_keyboard("menu_trades"))
        return
        
    sl_lbl = f"`{pos['sl']:.5f}`" if pos["sl"] > 0 else "_Not set_"
    tp_lbl = f"`{pos['tp']:.5f}`" if pos["tp"] > 0 else "_Not set_"
    
    text = (
        f"✏️ *Adjust Stops: #{ticket}* ({pos['symbol']})\n\n"
        f"📥 *Open Price:* `{pos['price_open']:.5f}` | *Current:* `{pos['price_current']:.5f}`\n"
        f"🔴 *Stop Loss:* {sl_lbl}\n"
        f"🟢 *Take Profit:* {tp_lbl}\n\n"
        f"👇 *Click buttons below to incrementally shift stops in the profitable (+ pips) or risk (- pips) directions:* "
    )
    
    await query.edit_message_text(
        text=text,
        parse_mode="Markdown",
        reply_markup=get_stops_adjustment_keyboard(ticket)
    )

@restricted
async def cb_adjust_stops_execute(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Executes relative stops adjustments (SL/TP +/- pips) via button clicks."""
    query = update.callback_query
    await query.answer()
    
    parts = query.data.split("_")
    ticket = int(parts[2])
    target = parts[3] # sl, tp
    action = parts[4] # 10, -10, 20, -20, clear, custom
    
    positions = await asyncio.to_thread(get_active_positions)
    pos = next((p for p in positions if p["ticket"] == ticket), None)
    
    if not pos:
        await query.edit_message_text("❌ Position no longer active.", reply_markup=get_back_keyboard("menu_trades"))
        return
        
    symbol = pos["symbol"]
    direction = pos["type"]
    entry = pos["price_open"]
    current_sl = pos["sl"]
    current_tp = pos["tp"]
    
    # Custom redirect
    if action == "custom":
        context.user_data["edit_ticket"] = ticket
        await query.edit_message_text(
            text=(
                f"✏️ *Modify Stops for Ticket #{ticket}*\n\n"
                f"Please type the new Stop Loss (SL) and Take Profit (TP) prices separated by a space.\n"
                f"Example: `2420.50 2480.00`\n\n"
                f"• Enter `0` to clear a stop (e.g. `0 2480.00` to set TP only).\n"
                f"• Write `/cancel` to abort."
            ),
            parse_mode="Markdown"
        )
        # We manually transition their state in the conversation handler
        # Since this callback is inside the modification wizard, we return state
        # Wait, since this is a general callback handler, returning won't affect it unless it is the entry point
        # Let's start the ConversationHandler directly by telling the bot to start listening.
        # Actually, our get_position_modification_handler entry point can capture this!
        # The entry point pattern matches r"^pos_edit_\d+$". Wait, this pattern is pos_adj_\d+_custom.
        # Let's make sure the entry point supports pos_adj_\d+_custom too! That's a perfect solution.
        return
        
    # Get symbol properties
    sym_info = await asyncio.to_thread(get_symbol_info, symbol)
    if not sym_info:
        await query.edit_message_text("❌ Failed to query symbol information.", reply_markup=get_back_keyboard("menu_trades"))
        return
        
    digits = sym_info["digits"]
    point = sym_info["point"]
    tick_size = sym_info["trade_tick_size"]
    
    is_forex = (digits == 3 or digits == 5) and ("USD" in symbol or "EUR" in symbol or "GBP" in symbol or "JPY" in symbol or "AUD" in symbol or "CAD" in symbol or "NZD" in symbol or "CHF" in symbol)
    pip_size = 10 * point if is_forex else 10 * tick_size
    
    # Apply logic: Positive shifts stops in profitable direction (+ pips), Negative shifts stops in risk direction (- pips)
    if target == "sl":
        new_tp = current_tp
        if action == "clear":
            new_sl = 0.0
        else:
            pips = float(action)
            # Use current SL if exists, else use entry price as baseline
            sl_base = current_sl if current_sl > 0 else entry
            # Profitable SL direction: BUY shifts SL UP (+), SELL shifts SL DOWN (-)
            offset = pips * pip_size
            new_sl = sl_base + offset if direction == "BUY" else sl_base - offset
            new_sl = round(new_sl, digits)
            
    else: # tp
        new_sl = current_sl
        if action == "clear":
            new_tp = 0.0
        else:
            pips = float(action)
            # Use current TP if exists, else entry price
            tp_base = current_tp if current_tp > 0 else entry
            # Profitable TP direction: BUY shifts TP UP (+), SELL shifts TP DOWN (-)
            offset = pips * pip_size
            new_tp = tp_base + offset if direction == "BUY" else tp_base - offset
            new_tp = round(new_tp, digits)
            
    # Send transaction
    res = await asyncio.to_thread(modify_position_stops, ticket, new_sl, new_tp)
    
    if res["success"]:
        # Re-fetch pos to display updated menu
        positions = await asyncio.to_thread(get_active_positions)
        updated_pos = next((p for p in positions if p["ticket"] == ticket), None)
        
        sl_lbl = f"`{updated_pos['sl']:.5f}`" if updated_pos and updated_pos["sl"] > 0 else "_Not set_"
        tp_lbl = f"`{updated_pos['tp']:.5f}`" if updated_pos and updated_pos["tp"] > 0 else "_Not set_"
        
        success_text = (
            f"✅ *Stops Updated successfully!*\n"
            f"🎫 *Ticket:* `#{ticket}`\n\n"
            f"🔴 *Stop Loss:* {sl_lbl}\n"
            f"🟢 *Take Profit:* {tp_lbl}\n\n"
            f"👇 *Continue adjusting or click back:* "
        )
        
        await query.edit_message_text(
            text=success_text,
            parse_mode="Markdown",
            reply_markup=get_stops_adjustment_keyboard(ticket)
        )
    else:
        await query.edit_message_text(
            text=f"❌ *Stops adjustment failed.*\n{res['message']}",
            parse_mode="Markdown",
            reply_markup=get_stops_adjustment_keyboard(ticket)
        )

# --- Trailing Stop Adjustment menus ---

@restricted
async def cb_trailing_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Displays the trailing stop preset selection screen."""
    query = update.callback_query
    await query.answer()
    
    ticket = int(query.data.split("_")[3])
    
    await query.edit_message_text(
        text=(
            f"📈 *Set Trailing Stop for Ticket #{ticket}*\n\n"
            f"Choose a preset trailing distance in standard pips, or click Custom to type:\n"
            f"_(The bot will trail the SL automatically once the position goes positive)_"
        ),
        parse_mode="Markdown",
        reply_markup=get_preset_trail_keyboard(ticket)
    )

@restricted
async def cb_trailing_preset_execute(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Saves trailing stop configurations directly from preset button clicks."""
    query = update.callback_query
    await query.answer()
    
    parts = query.data.split("_")
    ticket = int(parts[2])
    pips = float(parts[3])
    
    positions = await asyncio.to_thread(get_active_positions)
    pos = next((p for p in positions if p["ticket"] == ticket), None)
    
    if not pos:
        await query.edit_message_text("❌ Position no longer active.", reply_markup=get_back_keyboard("menu_trades"))
        return
        
    if pips == 0:
        await asyncio.to_thread(remove_trailing_stop, ticket)
        await query.edit_message_text(
            text=f"⏹️ Trailing stop deactivated for ticket `#{ticket}`.",
            parse_mode="Markdown",
            reply_markup=get_back_keyboard("menu_trades")
        )
    else:
        min_profit_pips = pips
        await asyncio.to_thread(add_trailing_stop, ticket, pos["symbol"], pips, min_profit_pips)
        await query.edit_message_text(
            text=(
                f"📈 *Trailing Stop Set successfully!*\n\n"
                f"🎫 *Ticket:* `#{ticket}` ({pos['symbol']})\n"
                f"📐 *Distance:* `{pips} Pips`\n"
                f"🛡️ *Activation Profit:* `{min_profit_pips} Pips` in profit\n\n"
                f"Trailing stop will be managed in the background."
            ),
            parse_mode="Markdown",
            reply_markup=get_back_keyboard("menu_trades")
        )

# --- Stateful wizard text inputs overrides ---

@restricted
async def start_edit_stops(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Triggered by Custom stops button. Starts text prompt flow."""
    query = update.callback_query
    await query.answer()
    
    ticket = int(query.data.split("_")[2])
    context.user_data["edit_ticket"] = ticket
    
    await query.edit_message_text(
        text=(
            f"✏️ *Custom Stops for Ticket #{ticket}*\n\n"
            f"Please type the new Stop Loss (SL) and Take Profit (TP) prices separated by a space.\n"
            f"Example: `2420.50 2480.00`\n\n"
            f"• Enter `0` to clear a stop (e.g. `0 2480.00` to set TP only).\n"
            f"• Write `/cancel` to abort."
        ),
        parse_mode="Markdown"
    )
    return WAITING_STOPS

async def txt_edit_stops_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Processes custom typed SL/TP with direction validation checks."""
    text = update.message.text.strip()
    ticket = context.user_data.get("edit_ticket")
    
    parts = text.split()
    if len(parts) != 2:
        await update.message.reply_text("❌ Invalid format. Please enter two prices separated by a space (e.g. `2415.00 2490.50`):")
        return WAITING_STOPS
        
    try:
        sl = float(parts[0])
        tp = float(parts[1])
        if sl < 0 or tp < 0:
            raise ValueError()
    except ValueError:
        await update.message.reply_text("❌ Valid positive numbers required. Try again (e.g. `2415.00 2490.50` or `0 0` to clear):")
        return WAITING_STOPS
        
    # Get active position details to run validations
    positions = await asyncio.to_thread(get_active_positions)
    pos = next((p for p in positions if p["ticket"] == ticket), None)
    
    if not pos:
        await update.message.reply_text("❌ Position no longer active.", reply_markup=get_back_keyboard("menu_trades"))
        return ConversationHandler.END
        
    symbol = pos["symbol"]
    direction = pos["type"] # BUY or SELL
    current_price = pos["price_current"]
    
    # 1. Validate Stop Loss direction
    if sl > 0:
        if direction == "BUY" and sl >= current_price:
            await update.message.reply_text(
                f"❌ *Invalid BUY Stop Loss.*\n"
                f"The Stop Loss price (`{sl}`) must be **below** the current market price (`{current_price}`).\n"
                f"Please enter the prices again:",
                parse_mode="Markdown"
            )
            return WAITING_STOPS
        elif direction == "SELL" and sl <= current_price:
            await update.message.reply_text(
                f"❌ *Invalid SELL Stop Loss.*\n"
                f"The Stop Loss price (`{sl}`) must be **above** the current market price (`{current_price}`).\n"
                f"Please enter the prices again:",
                parse_mode="Markdown"
            )
            return WAITING_STOPS
            
    # 2. Validate Take Profit direction
    if tp > 0:
        if direction == "BUY" and tp <= current_price:
            await update.message.reply_text(
                f"❌ *Invalid BUY Take Profit.*\n"
                f"The Take Profit price (`{tp}`) must be **above** the current market price (`{current_price}`).\n"
                f"Please enter the prices again:",
                parse_mode="Markdown"
            )
            return WAITING_STOPS
        elif direction == "SELL" and tp >= current_price:
            await update.message.reply_text(
                f"❌ *Invalid SELL Take Profit.*\n"
                f"The Take Profit price (`{tp}`) must be **below** the current market price (`{current_price}`).\n"
                f"Please enter the prices again:",
                parse_mode="Markdown"
            )
            return WAITING_STOPS
            
    await update.message.reply_text(f"⚡ *Modifying stops for ticket #{ticket}...*")
    res = await asyncio.to_thread(modify_position_stops, ticket, sl, tp)
    
    if res["success"]:
        # If the trailing stop exists, but we cleared SL, we should clean database
        if sl == 0:
            await asyncio.to_thread(remove_trailing_stop, ticket)
            
        sl_desc = f"`{sl}`" if sl > 0 else "_None_"
        tp_desc = f"`{tp}`" if tp > 0 else "_None_"
        await update.message.reply_text(
            f"✅ *Stops modified successfully!*\nNew SL: {sl_desc}\nNew TP: {tp_desc}",
            parse_mode="Markdown",
            reply_markup=get_back_keyboard("menu_trades")
        )
    else:
        await update.message.reply_text(
            f"❌ *Failed to modify stops.*\n{res['message']}",
            parse_mode="Markdown",
            reply_markup=get_back_keyboard("menu_trades")
        )
    return ConversationHandler.END

@restricted
async def start_trailing_stop_wizard(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Triggered by Custom trailing stop button. Starts text prompt flow."""
    query = update.callback_query
    await query.answer()
    
    ticket = int(query.data.split("_")[2])
    context.user_data["trail_ticket"] = ticket
    
    await query.edit_message_text(
        text=(
            f"📈 *Custom Trailing Stop for Ticket #{ticket}*\n\n"
            f"Please type the custom trailing stop distance in standard pips (e.g. `35`):\n"
            f"• Write `/cancel` to abort."
        ),
        parse_mode="Markdown"
    )
    return WAITING_TRAIL

async def txt_trailing_stop_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Saves custom trailing stops."""
    text = update.message.text.strip()
    ticket = context.user_data.get("trail_ticket")
    
    try:
        pips = float(text)
        if pips < 0:
            raise ValueError()
    except ValueError:
        await update.message.reply_text("❌ Please enter a positive number of pips (e.g. `35.5`):")
        return WAITING_TRAIL
        
    positions = await asyncio.to_thread(get_active_positions)
    pos = next((p for p in positions if p["ticket"] == ticket), None)
    
    if not pos:
        await update.message.reply_text("❌ Position no longer active.", reply_markup=get_back_keyboard("menu_trades"))
        return ConversationHandler.END
        
    if pips == 0:
        await asyncio.to_thread(remove_trailing_stop, ticket)
        await update.message.reply_text(f"⏹️ Trailing stop deactivated.", reply_markup=get_back_keyboard("menu_trades"))
    else:
        min_profit_pips = pips
        await asyncio.to_thread(add_trailing_stop, ticket, pos["symbol"], pips, min_profit_pips)
        await update.message.reply_text(
            f"📈 *Trailing Stop Set successfully!*\n\n🎫 *Ticket:* `#{ticket}`\n📐 *Distance:* `{pips} Pips`",
            parse_mode="Markdown",
            reply_markup=get_back_keyboard("menu_trades")
        )
    return ConversationHandler.END

async def cb_cancel_modification(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Cancels position modification wizard."""
    query = update.callback_query
    await query.answer("Modification aborted.")
    await query.edit_message_text("❌ *Modification cancelled.*", parse_mode="Markdown", reply_markup=get_back_keyboard("menu_trades"))
    return ConversationHandler.END

async def cmd_cancel_modification(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Command cancel handling."""
    await update.message.reply_text("❌ *Modification cancelled.*", parse_mode="Markdown", reply_markup=get_back_keyboard("menu_trades"))
    return ConversationHandler.END

# --- Handler Config ---

def get_position_modification_handler():
    """Returns the ConversationHandler configuration for custom text modifications overrides."""
    return ConversationHandler(
        entry_points=[
            CallbackQueryHandler(start_edit_stops, pattern=r"^pos_adj_\d+_custom$"),
            CallbackQueryHandler(start_trailing_stop_wizard, pattern=r"^trail_custom_\d+$")
        ],
        states={
            WAITING_STOPS: [
                MessageHandler(NOT_MENU_BUTTON, txt_edit_stops_input)
            ],
            WAITING_TRAIL: [
                MessageHandler(NOT_MENU_BUTTON, txt_trailing_stop_input)
            ]
        },
        fallbacks=[
            CommandHandler("cancel", cmd_cancel_modification),
            CallbackQueryHandler(cb_cancel_modification, pattern="^menu_trades$")
        ],
        allow_reentry=True
    )

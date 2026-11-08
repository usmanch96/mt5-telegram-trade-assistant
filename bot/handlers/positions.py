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
import MetaTrader5 as mt5
from mt5.monitor import get_active_positions, get_symbol_info, get_account_details
from mt5.executor import close_position, modify_position_stops
from mt5.risk_calculator import get_symbol_pip_size
from bot.keyboards import (
    get_back_keyboard, 
    get_position_control_keyboard,
    get_stops_adjustment_keyboard
)
from bot.middleware import restricted
from bot.conv_filters import NOT_MENU_BUTTON

# States for the Position Modifications Conversation
WAITING_STOPS = 0

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

def calc_position_sl_tp_pnl(p: dict, sym_info: Optional[dict], is_cent_account: bool = False):
    """
    Calculates estimated P&L if SL or TP is hit, in account currency.
    Returns (sl_pnl, tp_pnl) floats or None if not set.
    """
    sl_pnl = None
    tp_pnl = None

    if not sym_info:
        digits = 5
        point = 0.00001
        tick_size = 0.0001
        tick_value = 10.0
    else:
        digits = sym_info.get("digits", 5)
        point = sym_info.get("point", 0.00001)
        tick_size = sym_info.get("trade_tick_size", 0.0)
        tick_value = sym_info.get("trade_tick_value", 0.0)

        if tick_size <= 0:
            tick_size = 10 * point if (digits == 3 or digits == 5) else (point if point > 0 else 0.01)

        if tick_value <= 0:
            if digits == 3 or digits == 5:
                tick_value = 10.0
                tick_size = 10 * point if point > 0 else 0.0001
            else:
                tick_value = tick_size if tick_size > 0 else 1.0

    # Exness Cent account / symbol scaling (e.g. XAUUSDc)
    symbol = p.get("symbol", "")
    if is_cent_account or (symbol and symbol.endswith("c") and len(symbol) > 4):
        if tick_value > 0 and (tick_value / (tick_size or 1)) > 1000:
            tick_value = tick_value / 100.0

    vol = p["volume"]
    entry = p["price_open"]
    pos_type = p["type"]

    if p["sl"] > 0 and tick_size > 0:
        delta = (p["sl"] - entry) if pos_type == "BUY" else (entry - p["sl"])
        sl_pnl = (delta / tick_size) * tick_value * vol

    if p["tp"] > 0 and tick_size > 0:
        delta = (p["tp"] - entry) if pos_type == "BUY" else (entry - p["tp"])
        tp_pnl = (delta / tick_size) * tick_value * vol

    return sl_pnl, tp_pnl

def format_position_short(p: dict, digits: int = 5, sym_info: Optional[dict] = None, is_cent: bool = False) -> str:
    """Formats a position into a clean, compact summary block with SL/TP risk & reward."""
    pnl_emoji = "🟢" if p["profit"] >= 0 else "🔴"
    dir_arrow  = "▲" if p["type"] == "BUY" else "▼"
    pnl_sign   = "+" if p["profit"] >= 0 else ""

    sym_upper = p["symbol"].upper()
    if any(x in sym_upper for x in ["XAU", "XAG", "BTC", "US30", "NAS100", "SPX"]):
        digits = min(digits, 2)

    sl_pnl, tp_pnl = calc_position_sl_tp_pnl(p, sym_info, is_cent_account=is_cent)

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
        f"💼 *Position · #{p['ticket']}*\n"
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

    if query:
        await query.answer()

    positions = await asyncio.to_thread(get_active_positions)

    if not positions:
        text = (
            "💼 *Active Positions*\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "📭 No open positions on this account."
        )
        markup = get_back_keyboard("menu_main")
        if query:
            try:
                await query.edit_message_text(text, parse_mode="Markdown", reply_markup=markup)
                context.user_data["active_card_msg_id"] = query.message.message_id
            except Exception as e:
                if "message is not modified" not in str(e).lower():
                    raise
            return

        if update.message:
            try:
                await update.message.delete()
            except Exception:
                pass
            chat_id = update.effective_chat.id
            old_card_id = context.user_data.get("active_card_msg_id")
            if old_card_id:
                try:
                    await context.bot.edit_message_text(
                        chat_id=chat_id,
                        message_id=old_card_id,
                        text=text,
                        parse_mode="Markdown",
                        reply_markup=markup
                    )
                    return
                except Exception as e:
                    if "message is not modified" in str(e).lower():
                        return
            msg = await context.bot.send_message(chat_id=chat_id, text=text, parse_mode="Markdown", reply_markup=markup)
            context.user_data["active_card_msg_id"] = msg.message_id
            return

    # Fetch account info to check for Cent accounts
    acc = await asyncio.to_thread(get_account_details)
    is_cent = False
    if acc:
        curr = acc.get("currency", "").upper()
        if "USDC" in curr or "USCC" in curr or "EURC" in curr or curr.endswith("C"):
            is_cent = True

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
        if any(x in pos["symbol"].upper() for x in ["XAU", "XAG", "BTC", "US30", "NAS100", "SPX"]):
            digits = min(digits, 2)

        sl_pnl, tp_pnl = calc_position_sl_tp_pnl(pos, s_info, is_cent_account=is_cent)

        total_pnl += pos["profit"]
        if pos["sl"] > 0:
            has_any_sl = True
            if sl_pnl is not None:
                total_sl_pnl += sl_pnl

        if pos["tp"] > 0:
            has_any_tp = True
            if tp_pnl is not None:
                total_tp_pnl += tp_pnl

        summary_lines.append(format_position_short(pos, digits=digits, sym_info=s_info, is_cent=is_cent))

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

    if query:
        try:
            await query.edit_message_text(
                text="\n".join(summary_lines),
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup(keyboard_buttons)
            )
            context.user_data["active_card_msg_id"] = query.message.message_id
        except Exception as e:
            if "message is not modified" not in str(e).lower():
                raise
        return

    if update.message:
        try:
            await update.message.delete()
        except Exception:
            pass
        chat_id = update.effective_chat.id
        old_card_id = context.user_data.get("active_card_msg_id")
        if old_card_id:
            try:
                await context.bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=old_card_id,
                    text="\n".join(summary_lines),
                    parse_mode="Markdown",
                    reply_markup=InlineKeyboardMarkup(keyboard_buttons)
                )
                return
            except Exception as e:
                if "message is not modified" in str(e).lower():
                    return
        msg = await context.bot.send_message(
            chat_id=chat_id,
            text="\n".join(summary_lines),
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard_buttons)
        )
        context.user_data["active_card_msg_id"] = msg.message_id

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
        await query.edit_message_text(f"✅ *Position closed successfully!*\n{res['message']}", parse_mode="Markdown", reply_markup=get_back_keyboard("menu_trades"))
    else:
        await query.edit_message_text(f"❌ *Failed to close position.*\n{res['message']}", parse_mode="Markdown", reply_markup=get_back_keyboard("menu_trades"))

@restricted
async def cb_be_position_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Moves Stop Loss to entry price (Break-Even) with safety distance validation."""
    query = update.callback_query
    await query.answer()
    
    ticket = int(query.data.split("_")[2])
    
    positions = await asyncio.to_thread(get_active_positions)
    pos = next((p for p in positions if p["ticket"] == ticket), None)
    
    if not pos:
        await query.edit_message_text(f"❌ Position `#{ticket}` not found to apply Break-Even.", reply_markup=get_back_keyboard("menu_trades"))
        return
        
    entry = pos["price_open"]
    tp = pos["tp"]
    direction = pos["type"]
    current_price = pos["price_current"]
    symbol = pos["symbol"]
    
    sym_info = await asyncio.to_thread(get_symbol_info, symbol)
    digits = sym_info["digits"] if sym_info else 5
    point = sym_info["point"] if sym_info else 0.00001
    
    tick = await asyncio.to_thread(mt5.symbol_info_tick, symbol)
    stops_level = sym_info.get("trade_stops_level", 0) if sym_info else 0
    min_dist = max(stops_level * point, 5 * point)
    
    if direction == "BUY":
        bid_price = tick.bid if tick else current_price
        if bid_price <= entry + min_dist:
            await query.edit_message_text(
                f"⚠️ *Cannot move to Break-Even yet.*\n\n"
                f"Position `#{ticket}` (`{symbol}`) is not in sufficient profit.\n"
                f"• Entry: `{entry:.{digits}f}`\n"
                f"• Current Bid: `{bid_price:.{digits}f}`\n"
                f"• Required Min Bid: `>{entry + min_dist:.{digits}f}`\n\n"
                f"Wait for price to advance further before moving to BE.",
                parse_mode="Markdown",
                reply_markup=get_back_keyboard("menu_trades")
            )
            return
    else:  # SELL
        ask_price = tick.ask if tick else current_price
        if ask_price >= entry - min_dist:
            await query.edit_message_text(
                f"⚠️ *Cannot move to Break-Even yet.*\n\n"
                f"Position `#{ticket}` (`{symbol}`) is not in sufficient profit.\n"
                f"• Entry: `{entry:.{digits}f}`\n"
                f"• Current Ask: `{ask_price:.{digits}f}`\n"
                f"• Required Max Ask: `<{entry - min_dist:.{digits}f}`\n\n"
                f"Wait for price to advance further before moving to BE.",
                parse_mode="Markdown",
                reply_markup=get_back_keyboard("menu_trades")
            )
            return
            
    await query.edit_message_text(f"⚡ *Moving stop to Break-Even for ticket #{ticket}...*", parse_mode="Markdown")
    res = await asyncio.to_thread(modify_position_stops, ticket, sl=entry, tp=tp)
    
    if res["success"]:
        await query.edit_message_text(
            f"🛡️ *Stop Loss moved to Break-Even!*\n\n"
            f"🎫 `#{ticket}` · *{symbol}*\n"
            f"SL set to entry `{entry:.{digits}f}`.",
            parse_mode="Markdown",
            reply_markup=get_back_keyboard("menu_trades")
        )
    else:
        await query.edit_message_text(
            f"❌ *Failed to adjust Stop Loss.*\n{res['message']}",
            parse_mode="Markdown",
            reply_markup=get_back_keyboard("menu_trades")
        )

@restricted
async def cb_partial_close_options(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Shows options for partial closing of a position."""
    query = update.callback_query
    await query.answer()
    
    ticket = int(query.data.split("_")[2])

    positions = await asyncio.to_thread(get_active_positions)
    pos = next((p for p in positions if p["ticket"] == ticket), None)
    if not pos:
        await query.edit_message_text(f"❌ Position `#{ticket}` was already closed.", reply_markup=get_back_keyboard("menu_trades"))
        return

    sym_info = await asyncio.to_thread(get_symbol_info, pos["symbol"])
    v_min = sym_info["volume_min"] if sym_info else 0.01

    if pos["volume"] <= v_min:
        await query.edit_message_text(
            f"⚠️ *Partial close not available.*\n\n"
            f"Position `#{ticket}` volume is `{pos['volume']:.2f}` lots, which is the broker's minimum contract size (`{v_min}` lots) and cannot be subdivided.\n\n"
            f"To exit this position, use **Close Trade** to close the full position.",
            parse_mode="Markdown",
            reply_markup=get_back_keyboard(f"pos_view_{ticket}")
        )
        return
    
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
        text=f"🥞 *Partial Close Position #{ticket}* ({pos['symbol']} · `{pos['volume']:.2f}` Lots)\nChoose volume percentage to close:",
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
    
    rem_vol = round(current_vol - close_vol, 2)
    if 0 < rem_vol < volume_min:
        close_vol = round(current_vol - volume_min, 2)
        if close_vol < volume_min:
            await query.edit_message_text(
                f"❌ Cannot close partially: Remaining volume would be less than broker minimum ({volume_min} lots).",
                reply_markup=get_back_keyboard(f"pos_view_{ticket}")
            )
            return

    if close_vol >= current_vol:
        close_vol = current_vol
        
    res = await asyncio.to_thread(close_position, ticket, close_vol)
    
    if res["success"]:
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
        return
        
    sym_info = await asyncio.to_thread(get_symbol_info, symbol)
    if not sym_info:
        await query.edit_message_text("❌ Failed to query symbol information.", reply_markup=get_back_keyboard("menu_trades"))
        return
        
    digits = sym_info["digits"]
    pip_size = get_symbol_pip_size(symbol, sym_info)
    
    if target == "sl":
        new_tp = current_tp
        if action == "clear":
            new_sl = 0.0
        else:
            pips = float(action)
            sl_base = current_sl if current_sl > 0 else entry
            offset = pips * pip_size
            new_sl = sl_base + offset if direction == "BUY" else sl_base - offset
            new_sl = round(new_sl, digits)
            
    else: # tp
        new_sl = current_sl
        if action == "clear":
            new_tp = 0.0
        else:
            pips = float(action)
            tp_base = current_tp if current_tp > 0 else entry
            offset = pips * pip_size
            new_tp = tp_base + offset if direction == "BUY" else tp_base - offset
            new_tp = round(new_tp, digits)
            
    res = await asyncio.to_thread(modify_position_stops, ticket, new_sl, new_tp)
    
    if res["success"]:
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

# --- Stateful wizard text inputs overrides ---

@restricted
async def start_edit_stops(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Triggered by Custom stops button. Starts smart price entry prompt flow."""
    query = update.callback_query
    await query.answer()
    
    ticket = int(query.data.split("_")[2])
    context.user_data["edit_ticket"] = ticket
    context.user_data["active_card_msg_id"] = query.message.message_id
    
    positions = await asyncio.to_thread(get_active_positions)
    pos = next((p for p in positions if p["ticket"] == ticket), None)
    if not pos:
        await query.edit_message_text(
            text=f"❌ Position `#{ticket}` is no longer active.",
            reply_markup=get_back_keyboard("menu_trades")
        )
        return ConversationHandler.END
        
    symbol = pos["symbol"]
    sym_info = await asyncio.to_thread(get_symbol_info, symbol)
    digits = sym_info["digits"] if sym_info else 5
    curr = pos["price_current"]
    sl_curr = f"`{pos['sl']:.{digits}f}`" if pos["sl"] > 0 else "_None_"
    tp_curr = f"`{pos['tp']:.{digits}f}`" if pos["tp"] > 0 else "_None_"
    
    text = (
        f"✏️ *Smart Stops for Ticket #{ticket}* · `{symbol}`\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📊 *Type:* `{pos['type']}` {pos['volume']} lots\n"
        f"🎯 *Current Market:* `{curr:.{digits}f}`\n"
        f"🔴 *Current SL:* {sl_curr}\n"
        f"🟢 *Current TP:* {tp_curr}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"✍️ *Quick Type & Send (Fastest):*\n"
        f"• Both: `2740.50 2790.00` (auto-aligned)\n"
        f"• SL only: `2740.50` or `sl 2740.50`\n"
        f"• TP only: `2790.00` or `tp 2790.00`\n"
        f"• Clear stops: `0` to remove\n\n"
        f"👇 _Send your price above, or tap Cancel:_"
    )
    
    cancel_kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("❌ Cancel", callback_data=f"pos_view_{ticket}")]
    ])
    
    await query.edit_message_text(text=text, parse_mode="Markdown", reply_markup=cancel_kb)
    return WAITING_STOPS

async def txt_edit_stops_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Processes smart typed SL/TP with auto-ordering, risk calculation, and in-place feedback."""
    from bot.handlers.batch import parse_quick_price_input

    text = update.message.text.strip()
    ticket = context.user_data.get("edit_ticket")
    chat_id = update.effective_chat.id
    old_card_id = context.user_data.get("active_card_msg_id")

    # Clean up user's typed message bubble to keep chat 100% clean
    try:
        await update.message.delete()
    except Exception:
        pass

    positions = await asyncio.to_thread(get_active_positions)
    pos = next((p for p in positions if p["ticket"] == ticket), None)
    if not pos:
        err_msg = "❌ Position no longer active."
        if old_card_id:
            try:
                await context.bot.edit_message_text(chat_id=chat_id, message_id=old_card_id, text=err_msg, reply_markup=get_back_keyboard("menu_trades"))
                return ConversationHandler.END
            except Exception:
                pass
        await update.message.reply_text(err_msg, reply_markup=get_back_keyboard("menu_trades"))
        return ConversationHandler.END

    sl_val, tp_val, _, err = parse_quick_price_input(text, [pos])
    if err:
        prompt_kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("❌ Cancel", callback_data=f"pos_view_{ticket}")]
        ])
        err_text = f"{err}\n\n👇 *Try again (e.g. `2740 2790` or `sl 2740`):*"
        if old_card_id:
            try:
                await context.bot.edit_message_text(chat_id=chat_id, message_id=old_card_id, text=err_text, parse_mode="Markdown", reply_markup=prompt_kb)
                return WAITING_STOPS
            except Exception:
                pass
        await update.message.reply_text(err_text, parse_mode="Markdown", reply_markup=prompt_kb)
        return WAITING_STOPS

    final_sl = sl_val if sl_val is not None else pos["sl"]
    final_tp = tp_val if tp_val is not None else pos["tp"]

    # Execute stops modification
    res = await asyncio.to_thread(modify_position_stops, ticket, final_sl, final_tp)
    
    # Calculate fresh risk metrics for feedback
    updated_pos = dict(pos)
    updated_pos["sl"] = final_sl
    updated_pos["tp"] = final_tp
    sym_info = await asyncio.to_thread(get_symbol_info, pos["symbol"])
    digits = sym_info["digits"] if sym_info else 5
    sl_pnl, tp_pnl = calc_position_sl_tp_pnl(updated_pos, sym_info)

    sl_sign = "+" if (sl_pnl or 0) > 0 else ""
    tp_sign = "+" if (tp_pnl or 0) > 0 else ""
    sl_str = f"`{final_sl:.{digits}f}` ({sl_sign}{sl_pnl:,.2f})" if (final_sl > 0 and sl_pnl is not None) else (f"`{final_sl:.{digits}f}`" if final_sl > 0 else "_None_")
    tp_str = f"`{final_tp:.{digits}f}` ({tp_sign}{tp_pnl:,.2f})" if (final_tp > 0 and tp_pnl is not None) else (f"`{final_tp:.{digits}f}`" if final_tp > 0 else "_None_")

    done_kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("✏️ Adjust More", callback_data=f"pos_edit_menu_{ticket}")],
        [InlineKeyboardButton("⬅️ Back to Positions", callback_data="menu_trades")]
    ])

    if res["success"]:
        res_text = (
            f"✅ *Stops Updated Successfully!*\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🎫 *Ticket:* `#{ticket}` · *{pos['symbol']}*\n"
            f"🔴 *Stop Loss:* {sl_str}\n"
            f"🟢 *Take Profit:* {tp_str}\n"
            f"━━━━━━━━━━━━━━━━━━━━"
        )
    else:
        res_text = f"❌ *Failed to modify stops.*\n{res['message']}"

    if old_card_id:
        try:
            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=old_card_id,
                text=res_text,
                parse_mode="Markdown",
                reply_markup=done_kb
            )
            return ConversationHandler.END
        except Exception:
            pass

    await update.message.reply_text(res_text, parse_mode="Markdown", reply_markup=done_kb)
    return ConversationHandler.END

async def cb_cancel_to_view(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Cancels text input and returns directly to position view card."""
    await cb_view_position(update, context)
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
            CallbackQueryHandler(start_edit_stops, pattern=r"^pos_adj_\d+_custom$")
        ],
        states={
            WAITING_STOPS: [
                MessageHandler(NOT_MENU_BUTTON, txt_edit_stops_input)
            ]
        },
        fallbacks=[
            CommandHandler("cancel", cmd_cancel_modification),
            CallbackQueryHandler(cb_cancel_to_view, pattern=r"^pos_view_\d+$"),
            CallbackQueryHandler(cb_cancel_modification, pattern="^menu_trades$")
        ],
        allow_reentry=True
    )

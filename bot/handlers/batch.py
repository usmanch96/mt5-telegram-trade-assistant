import asyncio
import time
from typing import Optional
from telegram import Update
from telegram.ext import (
    ContextTypes,
    ConversationHandler,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
)
from mt5.monitor import get_active_positions, get_symbol_info, get_account_details
from mt5.executor import modify_position_stops
from mt5.risk_calculator import get_symbol_pip_size
from bot.handlers.positions import calc_position_sl_tp_pnl
from bot.keyboards import (
    get_back_keyboard, 
    get_main_keyboard,
    get_quick_stops_keyboard,
    get_quick_guided_sl_keyboard,
    get_quick_guided_tp_keyboard,
    get_quick_single_sl_keyboard,
    get_quick_single_tp_keyboard
)
from bot.middleware import restricted
from bot.conv_filters import NOT_MENU_BUTTON
from services.logger import logger

# Global symbol shortcuts mapping shorthand user inputs to standard broker symbols
_QUICK_SYMBOL_ALIASES = {
    "gold": "XAUUSD", "xau": "XAUUSD", "silver": "XAGUSD", "xag": "XAGUSD",
    "btc": "BTCUSD", "btcusd": "BTCUSD", "eth": "ETHUSD", "ethusd": "ETHUSD",
    "us30": "US30", "nas100": "NAS100", "nas": "NAS100", "spx": "SPX500"
}

# Conversation States for Quick Batch SL/TP
(
    WAITING_QUICK_STOPS_INPUT,
    WAITING_QUICK_GUIDED_SL,
    WAITING_QUICK_GUIDED_TP,
    WAITING_QUICK_ONLY_SL,
    WAITING_QUICK_ONLY_TP
) = range(5)


# --- Portfolio Risk Computation ---

async def compute_portfolio_risk_summary_lines(positions: list = None) -> list:
    """
    Computes current Float P&L, Total SL Risk, and Total TP Target across active positions.
    Returns 3 formatted strings:
      [
        "📊 *Float P&L* · 🟢 `+$124.50`",
        "🔴 *Total SL Risk* · 🔴 `-$85.00`",
        "🟢 *Total TP Target* · 🟢 `+$350.00`"
      ]
    """
    if positions is None:
        positions = await asyncio.to_thread(get_active_positions)

    if not positions:
        return [
            "📊 *Float P&L* · `$0.00`",
            "🔴 *Total SL Risk* · _No open positions_",
            "🟢 *Total TP Target* · _No open positions_"
        ]

    acc = await asyncio.to_thread(get_account_details)
    is_cent = False
    if acc:
        curr = acc.get("currency", "").upper()
        if "USDC" in curr or "USCC" in curr or "EURC" in curr or curr.endswith("C"):
            is_cent = True

    unique_symbols = list(set(p["symbol"] for p in positions))
    sym_map = {}
    for s in unique_symbols:
        info = await asyncio.to_thread(get_symbol_info, s)
        if info:
            sym_map[s] = info

    total_pnl = 0.0
    total_sl_pnl = 0.0
    total_tp_pnl = 0.0
    has_any_sl = False
    has_any_tp = False

    for pos in positions:
        total_pnl += pos.get("profit", 0.0)
        s_info = sym_map.get(pos["symbol"])
        sl_pnl, tp_pnl = calc_position_sl_tp_pnl(pos, s_info, is_cent_account=is_cent)

        if pos.get("sl", 0) > 0:
            has_any_sl = True
            if sl_pnl is not None:
                total_sl_pnl += sl_pnl

        if pos.get("tp", 0) > 0:
            has_any_tp = True
            if tp_pnl is not None:
                total_tp_pnl += tp_pnl

    pnl_emoji = "🟢" if total_pnl >= 0 else "🔴"
    pnl_sign = "+" if total_pnl >= 0 else ""
    pnl_line = f"📊 *Float P&L* · {pnl_emoji} `{pnl_sign}{total_pnl:,.2f}`"

    if has_any_sl:
        sl_emoji = "🟢" if total_sl_pnl >= 0 else "🔴"
        sl_sign = "+" if total_sl_pnl >= 0 else ""
        sl_line = f"🔴 *Total SL Risk* · {sl_emoji} `{sl_sign}{total_sl_pnl:,.2f}`"
    else:
        sl_line = "🔴 *Total SL Risk* · _No SL set_"

    if has_any_tp:
        tp_sign = "+" if total_tp_pnl >= 0 else ""
        tp_line = f"🟢 *Total TP Target* · 🟢 `{tp_sign}{total_tp_pnl:,.2f}`"
    else:
        tp_line = "🟢 *Total TP Target* · _No TP set_"

    return [pnl_line, sl_line, tp_line]


# --- Quick Batch SL/TP Execution Engine ---

async def apply_quick_stops_values(positions: list, sl_val: Optional[float], tp_val: Optional[float]) -> str:
    """Modifies SL and/or TP across active positions with timing and clear risk reporting."""
    start_time = time.perf_counter()
    
    symbols = list(set(p["symbol"] for p in positions))
    target_positions = positions
    target_symbol_desc = "All Symbols"
    
    # If multiple symbols exist, identify which symbol this price belongs to (if not 0/None)
    if len(symbols) > 1:
        matched_sym = None
        test_val = (sl_val if (sl_val is not None and sl_val > 0) else (tp_val if (tp_val is not None and tp_val > 0) else None))
        if test_val is not None:
            for s in symbols:
                s_info = await asyncio.to_thread(get_symbol_info, s)
                if s_info:
                    ref_price = s_info["bid"]
                    if ref_price > 0 and abs(test_val - ref_price) / ref_price < 0.50:
                        matched_sym = s
                        break
        if matched_sym:
            target_positions = [p for p in positions if p["symbol"] == matched_sym]
            target_symbol_desc = f"{matched_sym} ({len(target_positions)} positions)"
        else:
            target_symbol_desc = f"All {len(positions)} positions"
    elif len(symbols) == 1:
        target_symbol_desc = f"{symbols[0]} ({len(positions)} positions)"
        
    success_count = 0
    fail_count = 0
    
    for pos in target_positions:
        ticket = pos["ticket"]
        new_sl = sl_val if sl_val is not None else pos["sl"]
        new_tp = tp_val if tp_val is not None else pos["tp"]
        
        s_info = await asyncio.to_thread(get_symbol_info, pos["symbol"])
        digits = s_info["digits"] if s_info else 2
        new_sl = round(new_sl, digits) if new_sl > 0 else 0.0
        new_tp = round(new_tp, digits) if new_tp > 0 else 0.0
        
        res = await asyncio.to_thread(modify_position_stops, ticket, sl=new_sl, tp=new_tp)
        if res["success"]:
            success_count += 1
        else:
            fail_count += 1
            logger.error(f"Quick stop modify failed for ticket {ticket}: {res['message']}")
            
    elapsed = time.perf_counter() - start_time
    
    sl_str = f"`{sl_val}`" if (sl_val is not None and sl_val > 0) else ("_Cleared_" if sl_val == 0 else "_Unchanged_")
    tp_str = f"`{tp_val}`" if (tp_val is not None and tp_val > 0) else ("_Cleared_" if tp_val == 0 else "_Unchanged_")
    
    risk_summary_lines = await compute_portfolio_risk_summary_lines()
    risk_block = "\n".join(risk_summary_lines)

    return (
        f"⚡ *Quick Stops Applied!*\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🔣 *Target:* `{target_symbol_desc}`\n"
        f"🔴 *Stop Loss:* {sl_str}\n"
        f"🟢 *Take Profit:* {tp_str}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"{risk_block}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"✅ *Successful:* `{success_count}/{len(target_positions)}`\n"
        f"⏱️ *Executed in:* `{elapsed:.2f}s`"
    )

def parse_quick_price_input(text: str, positions: list, default_symbol: str = "ALL"):
    """
    Parses flexible quick stop price input with optional pair specifier.
    Supports:
      '2740.50 2790.00'
      'XAUUSD 2740.50 2790.00'
      'gold 2740.50 2790.00'
      'eurusd sl 1.0820'
      'sl 2740.50'
    Returns (sl_val, tp_val, target_positions, error_message).
    """
    clean_text = text.strip().replace(",", " ").replace("=", " ").replace(":", " ")
    parts = clean_text.split()
    if not parts:
        return None, None, positions, "❌ Empty input. Please type prices (e.g. `2740.50 2790.00`)."
        
    active_symbols = list(set(p["symbol"] for p in positions))
    alias_map = _QUICK_SYMBOL_ALIASES

    target_symbol = default_symbol
    remaining_parts = []
    
    for token in parts:
        token_upper = token.upper()
        token_lower = token.lower()
        if token_lower in ("sl", "tp"):
            remaining_parts.append(token)
            continue

        matched = None
        for s in active_symbols:
            if s.upper() == token_upper or s.upper().startswith(token_upper):
                matched = s
                break
        if matched:
            target_symbol = matched
            continue

        if token_lower in alias_map:
            alias_target = alias_map[token_lower]
            for s in active_symbols:
                if alias_target in s.upper():
                    matched = s
                    break
            target_symbol = matched if matched else alias_target
            continue

        # If token is an explicit currency pair or instrument ticker (e.g. GBPUSD, US30)
        if len(token) >= 4 and not token.replace(".", "").replace("-", "").isdigit() and not any(char in token for char in "=:,"):
            if len(token) == 6 and token.isalpha():
                target_symbol = token_upper
                continue

        remaining_parts.append(token)

    if target_symbol != "ALL":
        target_positions = [p for p in positions if p["symbol"] == target_symbol]
        if not target_positions:
            return None, None, positions, f"❌ No open positions found for `{target_symbol}`."
    else:
        target_positions = positions

    lower_parts = [p.lower() for p in remaining_parts]
    sl_val = None
    tp_val = None
    
    # Check for explicit keywords 'sl' or 'tp'
    if "sl" in lower_parts or "tp" in lower_parts:
        idx = 0
        while idx < len(lower_parts):
            token = lower_parts[idx]
            if token == "sl" and idx + 1 < len(lower_parts):
                try:
                    sl_val = float(lower_parts[idx + 1])
                    idx += 2
                    continue
                except ValueError:
                    pass
            elif token == "tp" and idx + 1 < len(lower_parts):
                try:
                    tp_val = float(lower_parts[idx + 1])
                    idx += 2
                    continue
                except ValueError:
                    pass
            idx += 1
            
        if sl_val is None and tp_val is None:
            return None, None, target_positions, "❌ Could not parse price numbers after 'sl' or 'tp'."
            
        return sl_val, tp_val, target_positions, None
        
    # Pure numbers without keywords
    numbers = []
    for p in remaining_parts:
        try:
            numbers.append(float(p))
        except ValueError:
            pass
            
    if not numbers:
        return None, None, target_positions, (
            "❌ No valid price numbers found.\n"
            "• Examples: `2740.50 2790.00` or `sl 2740.50` or `tp 2790.00`"
        )
        
    if len(numbers) >= 2:
        val1 = numbers[0]
        val2 = numbers[1]
        
        pos_type = target_positions[0]["type"] if target_positions else "BUY"
        
        if val1 == 0:
            sl_val = 0.0
            tp_val = val2
        elif val2 == 0:
            sl_val = val1
            tp_val = 0.0
        else:
            lower_val = min(val1, val2)
            higher_val = max(val1, val2)
            if pos_type == "BUY":
                sl_val = lower_val
                tp_val = higher_val
            else:
                sl_val = higher_val
                tp_val = lower_val
                
        return sl_val, tp_val, target_positions, None
        
    elif len(numbers) == 1:
        val = numbers[0]
        if val == 0:
            return 0.0, 0.0, target_positions, None
            
        pos_type = target_positions[0]["type"] if target_positions else "BUY"
        curr_price = target_positions[0]["price_current"] if target_positions else val
        
        if pos_type == "BUY":
            if val < curr_price:
                sl_val = val
            else:
                tp_val = val
        else: # SELL
            if val > curr_price:
                sl_val = val
            else:
                tp_val = val
                
        return sl_val, tp_val, target_positions, None


async def render_quick_stops_screen(message, positions: list, is_edit: bool = False, selected_symbol: str = "ALL", context: Optional[ContextTypes.DEFAULT_TYPE] = None) -> int:
    """Renders the fast, interactive Quick Batch SL/TP dashboard screen with 1-tap pair selection."""
    symbols = sorted(list(set(p["symbol"] for p in positions)))
    
    if selected_symbol != "ALL":
        target_positions = [p for p in positions if p["symbol"] == selected_symbol]
    else:
        target_positions = positions
        
    if selected_symbol != "ALL" and target_positions:
        s_info = await asyncio.to_thread(get_symbol_info, selected_symbol)
        digits = s_info["digits"] if s_info else 2
        ask = s_info["ask"] if s_info else 0.0
        bid = s_info["bid"] if s_info else 0.0
        avg_entry = sum(p["price_open"] for p in target_positions) / len(target_positions)
        current_sl = target_positions[0]["sl"]
        current_tp = target_positions[0]["tp"]
        sl_lbl = f"`{current_sl:.{digits}f}`" if current_sl > 0 else "_None_"
        tp_lbl = f"`{current_tp:.{digits}f}`" if current_tp > 0 else "_None_"
        
        focus_header = (
            f"🎯 *Selected Pair:* `{selected_symbol}` (`{len(target_positions)}` positions)\n"
            f"📥 *Avg Entry:* `{avg_entry:.{digits}f}`\n"
            f"📲 *Live Market:* Bid `{bid:.{digits}f}` · Ask `{ask:.{digits}f}`\n"
            f"🔴 Current SL: {sl_lbl}  🟢 Current TP: {tp_lbl}"
        )
    else:
        sym_lines = []
        for s in symbols:
            sym_positions = [p for p in positions if p["symbol"] == s]
            sym_info = await asyncio.to_thread(get_symbol_info, s)
            digits = sym_info["digits"] if sym_info else 2
            ask = sym_info["ask"] if sym_info else 0.0
            bid = sym_info["bid"] if sym_info else 0.0
            avg_entry = sum(p["price_open"] for p in sym_positions) / len(sym_positions)
            sym_lines.append(
                f"• *{s}* (`{len(sym_positions)}` trades) · Avg `{avg_entry:.{digits}f}` | Bid `{bid:.{digits}f}`"
            )
        focus_header = (
            f"🌐 *Target:* `All Pairs` (`{len(positions)}` positions across `{len(symbols)}` symbols)\n"
            + "\n".join(sym_lines)
        )
        
    pair_hint = ""
    if len(symbols) > 1:
        pair_hint = "💡 _Tap a pair button below to switch target pair instantly._\n\n"
        
    text = (
        f"⚡ *Quick Batch SL/TP (Price Entry)*\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"{focus_header}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"{pair_hint}"
        f"✍️ *Quick Type & Send (Fastest):*\n"
        f"• Both: `2740.50 2790.00` (SL & TP)\n"
        f"• Specific Pair: `XAUUSD 2740.50 2790.00`\n"
        f"• SL only: `2740.50` or `sl 2740.50`\n"
        f"• TP only: `2790.00` or `tp 2790.00`\n\n"
        f"👇 *Or use buttons for guided step-by-step entry:*"
    )
    
    reply_kb = get_quick_stops_keyboard(positions, selected_symbol)
    if is_edit and hasattr(message, "edit_text"):
        try:
            await message.edit_text(text, parse_mode="Markdown", reply_markup=reply_kb)
            if context:
                context.user_data["active_card_msg_id"] = message.message_id
        except Exception as e:
            if "message is not modified" not in str(e).lower():
                raise
    else:
        # Check if we have an active card on screen to edit in place!
        if context and context.user_data.get("active_card_msg_id"):
            old_card_id = context.user_data.get("active_card_msg_id")
            try:
                await context.bot.edit_message_text(
                    chat_id=message.chat.id,
                    message_id=old_card_id,
                    text=text,
                    parse_mode="Markdown",
                    reply_markup=reply_kb
                )
                return WAITING_QUICK_STOPS_INPUT
            except Exception as e:
                if "message is not modified" in str(e).lower():
                    return WAITING_QUICK_STOPS_INPUT
        msg = await message.reply_text(text, parse_mode="Markdown", reply_markup=reply_kb)
        if context:
            context.user_data["active_card_msg_id"] = msg.message_id
        
    return WAITING_QUICK_STOPS_INPUT

async def cb_quick_pair_toggle(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Toggles active pair filter on Quick Stops dashboard with 1 tap."""
    query = update.callback_query
    await query.answer()
    
    target_sym = query.data.replace("qpair_", "")
    context.user_data["quick_target_symbol"] = target_sym
    
    positions = await asyncio.to_thread(get_active_positions)
    if not positions:
        await query.edit_message_text("📭 No active positions.", reply_markup=get_back_keyboard("menu_main"))
        return ConversationHandler.END
        
    return await render_quick_stops_screen(query.message, positions, is_edit=True, selected_symbol=target_sym, context=context)

async def cb_quick_mode_select(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Processes mode selection buttons from the quick stops dashboard."""
    query = update.callback_query
    await query.answer()
    
    data = query.data # qstop_mode_both, qstop_mode_sl_only, qstop_mode_tp_only, qstop_mode_be, qstop_mode_clear_all
    
    positions = await asyncio.to_thread(get_active_positions)
    if not positions:
        await query.edit_message_text("📭 No active positions found to update.", reply_markup=get_back_keyboard("menu_main"))
        return ConversationHandler.END
        
    target_sym = context.user_data.get("quick_target_symbol", "ALL")
    if target_sym and target_sym != "ALL":
        target_positions = [p for p in positions if p["symbol"] == target_sym]
        if not target_positions:
            target_positions = positions
    else:
        target_positions = positions

    context.user_data["b_positions_snapshot"] = target_positions
    context.user_data["active_card_msg_id"] = query.message.message_id
    
    if data == "qstop_mode_both":
        return await render_quick_guided_sl_prompt(query.message, target_positions, is_edit=True, context=context)
    elif data == "qstop_mode_sl_only":
        return await render_quick_single_sl_prompt(query.message, target_positions, is_edit=True, context=context)
    elif data == "qstop_mode_tp_only":
        return await render_quick_single_tp_prompt(query.message, target_positions, is_edit=True, context=context)
    elif data == "qstop_mode_be":
        # Instant Break-Even
        sym_desc = f"`{target_sym}`" if target_sym != "ALL" else "all positions"
        await query.edit_message_text(f"⚡ *Moving winning trades ({sym_desc}) to Break-Even...*", parse_mode="Markdown")
        start_time = time.perf_counter()
        success = 0
        skipped = 0
        for pos in target_positions:
            sym_info = await asyncio.to_thread(get_symbol_info, pos["symbol"])
            digits = sym_info["digits"] if sym_info else 5
            point = sym_info["point"] if sym_info else 0.00001
            min_dist = max(sym_info.get("trade_stops_level", 0) * point, 5 * point)
            entry = pos["price_open"]
            curr = pos["price_current"]
            
            if pos["type"] == "BUY" and curr <= entry + min_dist:
                skipped += 1
                continue
            elif pos["type"] == "SELL" and curr >= entry - min_dist:
                skipped += 1
                continue
                
            res = await asyncio.to_thread(modify_position_stops, pos["ticket"], sl=entry, tp=pos["tp"])
            if res["success"]:
                success += 1
                
        elapsed = time.perf_counter() - start_time
        risk_summary_lines = await compute_portfolio_risk_summary_lines()
        risk_block = "\n".join(risk_summary_lines)
        
        msg = (
            f"🛡️ *Break-Even Applied!*\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🔣 *Target:* `{target_sym}`\n"
            f"✅ *Moved to BE:* `{success}/{len(target_positions)} trades`\n"
        )
        if skipped > 0:
            msg += f"⚠️ *Skipped:* `{skipped}` (not in sufficient profit yet)\n"
        msg += (
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"{risk_block}\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"⏱️ *Executed in:* `{elapsed:.2f}s`"
        )
        
        await query.edit_message_text(msg, parse_mode="Markdown", reply_markup=get_back_keyboard("menu_main"))
        return ConversationHandler.END
        
    elif data == "qstop_mode_clear_all":
        sym_desc = f"`{target_sym}`" if target_sym != "ALL" else "all positions"
        await query.edit_message_text(f"⚡ *Clearing Stop Loss & Take Profit for {sym_desc}...*", parse_mode="Markdown")
        res_text = await apply_quick_stops_values(target_positions, sl_val=0.0, tp_val=0.0)
        await query.edit_message_text(res_text, parse_mode="Markdown", reply_markup=get_back_keyboard("menu_main"))
        return ConversationHandler.END
        
    return WAITING_QUICK_STOPS_INPUT

# --- Guided Step-by-Step Flow ---

async def render_quick_guided_sl_prompt(message, positions: list, is_edit: bool = False, context: Optional[ContextTypes.DEFAULT_TYPE] = None) -> int:
    """Prompts for Stop Loss price in guided mode."""
    sym = positions[0]["symbol"] if positions else "Pairs"
    sym_info = await asyncio.to_thread(get_symbol_info, sym)
    digits = sym_info["digits"] if sym_info else 2
    bid = sym_info["bid"] if sym_info else 0.0
    ask = sym_info["ask"] if sym_info else 0.0
    
    text = (
        f"🔴 *Step 1/2: Enter Stop Loss (SL) Price*\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🔣 *Symbol:* `{sym}` ({len(positions)} positions)\n"
        f"📲 *Current Market:* Bid `{bid:.{digits}f}` | Ask `{ask:.{digits}f}`\n\n"
        f"✍️ *Type the new SL price* (e.g. `{bid * 0.995:.{digits}f}`):\n"
        f"_(Or click Skip below to leave SL unchanged)_"
    )
    kb = get_quick_guided_sl_keyboard()
    if is_edit and hasattr(message, "edit_text"):
        await message.edit_text(text, parse_mode="Markdown", reply_markup=kb)
        if context:
            context.user_data["active_card_msg_id"] = message.message_id
    elif context and context.user_data.get("active_card_msg_id"):
        chat_id = message.chat_id if hasattr(message, "chat_id") else message.chat.id
        await context.bot.edit_message_text(
            chat_id=chat_id,
            message_id=context.user_data["active_card_msg_id"],
            text=text,
            parse_mode="Markdown",
            reply_markup=kb
        )
    else:
        msg = await message.reply_text(text, parse_mode="Markdown", reply_markup=kb)
        if context:
            context.user_data["active_card_msg_id"] = msg.message_id
    return WAITING_QUICK_GUIDED_SL

async def cb_quick_guided_sl_btn(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handles Skip or Remove button in guided SL step."""
    query = update.callback_query
    await query.answer()
    
    choice = query.data # qsl_skip, qsl_clear
    context.user_data["quick_sl"] = 0.0 if choice == "qsl_clear" else None
    context.user_data["active_card_msg_id"] = query.message.message_id
    
    positions = context.user_data.get("b_positions_snapshot", [])
    return await render_quick_guided_tp_prompt(query.message, positions, context.user_data["quick_sl"], is_edit=True, context=context)

async def txt_quick_guided_sl(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handles typed SL price in guided mode."""
    # Clean up user's typed message bubble to keep chat 100% clean
    try:
        await update.message.delete()
    except Exception:
        pass

    text = update.message.text.strip()
    old_card_id = context.user_data.get("active_card_msg_id")
    chat_id = update.effective_chat.id

    try:
        val = float(text)
        if val < 0:
            raise ValueError
        context.user_data["quick_sl"] = val
    except ValueError:
        err_text = (
            f"❌ *Invalid SL Price*\n"
            f"Please enter a valid positive SL price number (e.g. `2735.50`):\n\n"
            f"👇 *Try again or tap Skip / Cancel:*"
        )
        if old_card_id:
            try:
                await context.bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=old_card_id,
                    text=err_text,
                    parse_mode="Markdown",
                    reply_markup=get_quick_guided_sl_keyboard()
                )
                return WAITING_QUICK_GUIDED_SL
            except Exception:
                pass
        await update.message.reply_text(err_text, parse_mode="Markdown", reply_markup=get_quick_guided_sl_keyboard())
        return WAITING_QUICK_GUIDED_SL
        
    positions = context.user_data.get("b_positions_snapshot", [])
    return await render_quick_guided_tp_prompt(update.message, positions, val, is_edit=False, context=context)

async def render_quick_guided_tp_prompt(message, positions: list, sl_val: Optional[float], is_edit: bool = False, context: Optional[ContextTypes.DEFAULT_TYPE] = None) -> int:
    """Prompts for Take Profit price in guided mode."""
    sym = positions[0]["symbol"] if positions else "Pairs"
    sym_info = await asyncio.to_thread(get_symbol_info, sym)
    digits = sym_info["digits"] if sym_info else 2
    bid = sym_info["bid"] if sym_info else 0.0
    ask = sym_info["ask"] if sym_info else 0.0
    
    sl_desc = f"`{sl_val:.{digits}f}`" if (sl_val is not None and sl_val > 0) else ("_Cleared_" if sl_val == 0 else "_Unchanged_")
    
    text = (
        f"🟢 *Step 2/2: Enter Take Profit (TP) Price*\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🔴 *SL Level:* {sl_desc}\n"
        f"🔣 *Symbol:* `{sym}` ({len(positions)} positions)\n"
        f"📲 *Current Market:* Bid `{bid:.{digits}f}` | Ask `{ask:.{digits}f}`\n\n"
        f"✍️ *Type the new TP price* (e.g. `{ask * 1.005:.{digits}f}`):\n"
        f"_(Or click Skip below to leave TP unchanged)_"
    )
    kb = get_quick_guided_tp_keyboard()
    if is_edit and hasattr(message, "edit_text"):
        await message.edit_text(text, parse_mode="Markdown", reply_markup=kb)
        if context:
            context.user_data["active_card_msg_id"] = message.message_id
    elif context and context.user_data.get("active_card_msg_id"):
        chat_id = message.chat_id if hasattr(message, "chat_id") else message.chat.id
        await context.bot.edit_message_text(
            chat_id=chat_id,
            message_id=context.user_data["active_card_msg_id"],
            text=text,
            parse_mode="Markdown",
            reply_markup=kb
        )
    else:
        msg = await message.reply_text(text, parse_mode="Markdown", reply_markup=kb)
        if context:
            context.user_data["active_card_msg_id"] = msg.message_id
    return WAITING_QUICK_GUIDED_TP

async def cb_quick_guided_tp_btn(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handles Skip or Remove button in guided TP step and executes."""
    query = update.callback_query
    await query.answer()
    
    choice = query.data # qtp_skip, qtp_clear
    tp_val = 0.0 if choice == "qtp_clear" else None
    sl_val = context.user_data.get("quick_sl")
    context.user_data["active_card_msg_id"] = query.message.message_id
    
    positions = await asyncio.to_thread(get_active_positions)
    if not positions:
        await query.edit_message_text("📭 Positions closed before stops were applied.", reply_markup=get_back_keyboard("menu_main"))
        return ConversationHandler.END
        
    target_sym = context.user_data.get("quick_target_symbol", "ALL")
    if target_sym and target_sym != "ALL":
        target_positions = [p for p in positions if p["symbol"] == target_sym]
        if not target_positions:
            target_positions = positions
    else:
        target_positions = positions
        
    res_text = await apply_quick_stops_values(target_positions, sl_val, tp_val)
    await query.edit_message_text(res_text, parse_mode="Markdown", reply_markup=get_back_keyboard("menu_main"))
    return ConversationHandler.END

async def txt_quick_guided_tp(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handles typed TP price in guided mode and executes immediately in-place."""
    # Clean up user's typed message bubble to keep chat 100% clean
    try:
        await update.message.delete()
    except Exception:
        pass

    text = update.message.text.strip()
    old_card_id = context.user_data.get("active_card_msg_id")
    chat_id = update.effective_chat.id

    try:
        tp_val = float(text)
        if tp_val < 0:
            raise ValueError
    except ValueError:
        err_text = (
            f"❌ *Invalid TP Price*\n"
            f"Please enter a valid positive TP price number (e.g. `2790.00`):\n\n"
            f"👇 *Try again or tap Skip / Cancel:*"
        )
        if old_card_id:
            try:
                await context.bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=old_card_id,
                    text=err_text,
                    parse_mode="Markdown",
                    reply_markup=get_quick_guided_tp_keyboard()
                )
                return WAITING_QUICK_GUIDED_TP
            except Exception:
                pass
        await update.message.reply_text(err_text, parse_mode="Markdown", reply_markup=get_quick_guided_tp_keyboard())
        return WAITING_QUICK_GUIDED_TP
        
    sl_val = context.user_data.get("quick_sl")
    positions = await asyncio.to_thread(get_active_positions)
    if not positions:
        err_msg = "📭 Positions closed before stops were applied."
        if old_card_id:
            try:
                await context.bot.edit_message_text(chat_id=chat_id, message_id=old_card_id, text=err_msg, reply_markup=get_back_keyboard("menu_main"))
                return ConversationHandler.END
            except Exception:
                pass
        await update.message.reply_text(err_msg, reply_markup=get_back_keyboard("menu_main"))
        return ConversationHandler.END
        
    target_sym = context.user_data.get("quick_target_symbol", "ALL")
    if target_sym and target_sym != "ALL":
        target_positions = [p for p in positions if p["symbol"] == target_sym]
        if not target_positions:
            target_positions = positions
    else:
        target_positions = positions
        
    res_text = await apply_quick_stops_values(target_positions, sl_val, tp_val)
    if old_card_id:
        try:
            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=old_card_id,
                text=res_text,
                parse_mode="Markdown",
                reply_markup=get_back_keyboard("menu_main")
            )
            return ConversationHandler.END
        except Exception:
            pass
    await update.message.reply_text(res_text, parse_mode="Markdown", reply_markup=get_back_keyboard("menu_main"))
    return ConversationHandler.END

# --- Single SL-Only / TP-Only Modes ---

async def render_quick_single_sl_prompt(message, positions: list, is_edit: bool = False, context: Optional[ContextTypes.DEFAULT_TYPE] = None) -> int:
    """Prompts for SL price only."""
    sym = positions[0]["symbol"] if positions else "Pairs"
    sym_info = await asyncio.to_thread(get_symbol_info, sym)
    digits = sym_info["digits"] if sym_info else 2
    bid = sym_info["bid"] if sym_info else 0.0
    ask = sym_info["ask"] if sym_info else 0.0
    
    text = (
        f"🔴 *Quick Set: Stop Loss (SL) Price*\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🔣 *Symbol:* `{sym}` ({len(positions)} positions)\n"
        f"📲 *Current Market:* Bid `{bid:.{digits}f}` | Ask `{ask:.{digits}f}`\n\n"
        f"✍️ *Type target SL price level* for all positions (e.g. `{bid * 0.995:.{digits}f}`):"
    )
    kb = get_quick_single_sl_keyboard()
    if is_edit and hasattr(message, "edit_text"):
        await message.edit_text(text, parse_mode="Markdown", reply_markup=kb)
        if context:
            context.user_data["active_card_msg_id"] = message.message_id
    elif context and context.user_data.get("active_card_msg_id"):
        chat_id = message.chat_id if hasattr(message, "chat_id") else message.chat.id
        await context.bot.edit_message_text(
            chat_id=chat_id,
            message_id=context.user_data["active_card_msg_id"],
            text=text,
            parse_mode="Markdown",
            reply_markup=kb
        )
    else:
        msg = await message.reply_text(text, parse_mode="Markdown", reply_markup=kb)
        if context:
            context.user_data["active_card_msg_id"] = msg.message_id
    return WAITING_QUICK_ONLY_SL

async def cb_quick_single_sl_btn(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handles Clear SL button in SL-only mode."""
    query = update.callback_query
    await query.answer()
    context.user_data["active_card_msg_id"] = query.message.message_id
    
    positions = await asyncio.to_thread(get_active_positions)
    if not positions:
        await query.edit_message_text("📭 No active positions.", reply_markup=get_back_keyboard("menu_main"))
        return ConversationHandler.END
        
    target_sym = context.user_data.get("quick_target_symbol", "ALL")
    if target_sym and target_sym != "ALL":
        target_positions = [p for p in positions if p["symbol"] == target_sym]
        if not target_positions:
            target_positions = positions
    else:
        target_positions = positions
        
    res_text = await apply_quick_stops_values(target_positions, sl_val=0.0, tp_val=None)
    await query.edit_message_text(res_text, parse_mode="Markdown", reply_markup=get_back_keyboard("menu_main"))
    return ConversationHandler.END

async def txt_quick_single_sl(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handles typed SL price in SL-only mode in-place."""
    # Clean up user's typed message bubble to keep chat 100% clean
    try:
        await update.message.delete()
    except Exception:
        pass

    text = update.message.text.strip()
    old_card_id = context.user_data.get("active_card_msg_id")
    chat_id = update.effective_chat.id

    try:
        val = float(text)
        if val < 0:
            raise ValueError
    except ValueError:
        err_text = (
            f"❌ *Invalid SL Price*\n"
            f"Please enter a valid positive SL price (e.g. `2735.00`):\n\n"
            f"👇 *Try again or tap Cancel:*"
        )
        if old_card_id:
            try:
                await context.bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=old_card_id,
                    text=err_text,
                    parse_mode="Markdown",
                    reply_markup=get_quick_single_sl_keyboard()
                )
                return WAITING_QUICK_ONLY_SL
            except Exception:
                pass
        await update.message.reply_text(err_text, parse_mode="Markdown", reply_markup=get_quick_single_sl_keyboard())
        return WAITING_QUICK_ONLY_SL
        
    positions = await asyncio.to_thread(get_active_positions)
    if not positions:
        err_msg = "📭 No active positions."
        if old_card_id:
            try:
                await context.bot.edit_message_text(chat_id=chat_id, message_id=old_card_id, text=err_msg, reply_markup=get_back_keyboard("menu_main"))
                return ConversationHandler.END
            except Exception:
                pass
        await update.message.reply_text(err_msg, reply_markup=get_back_keyboard("menu_main"))
        return ConversationHandler.END
        
    target_sym = context.user_data.get("quick_target_symbol", "ALL")
    if target_sym and target_sym != "ALL":
        target_positions = [p for p in positions if p["symbol"] == target_sym]
        if not target_positions:
            target_positions = positions
    else:
        target_positions = positions
        
    res_text = await apply_quick_stops_values(target_positions, sl_val=val, tp_val=None)
    if old_card_id:
        try:
            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=old_card_id,
                text=res_text,
                parse_mode="Markdown",
                reply_markup=get_back_keyboard("menu_main")
            )
            return ConversationHandler.END
        except Exception:
            pass
    await update.message.reply_text(res_text, parse_mode="Markdown", reply_markup=get_back_keyboard("menu_main"))
    return ConversationHandler.END

async def render_quick_single_tp_prompt(message, positions: list, is_edit: bool = False, context: Optional[ContextTypes.DEFAULT_TYPE] = None) -> int:
    """Prompts for TP price only."""
    sym = positions[0]["symbol"] if positions else "Pairs"
    sym_info = await asyncio.to_thread(get_symbol_info, sym)
    digits = sym_info["digits"] if sym_info else 2
    bid = sym_info["bid"] if sym_info else 0.0
    ask = sym_info["ask"] if sym_info else 0.0
    
    text = (
        f"🟢 *Quick Set: Take Profit (TP) Price*\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🔣 *Symbol:* `{sym}` ({len(positions)} positions)\n"
        f"📲 *Current Market:* Bid `{bid:.{digits}f}` | Ask `{ask:.{digits}f}`\n\n"
        f"✍️ *Type target TP price level* for all positions (e.g. `{ask * 1.005:.{digits}f}`):"
    )
    kb = get_quick_single_tp_keyboard()
    if is_edit and hasattr(message, "edit_text"):
        await message.edit_text(text, parse_mode="Markdown", reply_markup=kb)
        if context:
            context.user_data["active_card_msg_id"] = message.message_id
    elif context and context.user_data.get("active_card_msg_id"):
        chat_id = message.chat_id if hasattr(message, "chat_id") else message.chat.id
        await context.bot.edit_message_text(
            chat_id=chat_id,
            message_id=context.user_data["active_card_msg_id"],
            text=text,
            parse_mode="Markdown",
            reply_markup=kb
        )
    else:
        msg = await message.reply_text(text, parse_mode="Markdown", reply_markup=kb)
        if context:
            context.user_data["active_card_msg_id"] = msg.message_id
    return WAITING_QUICK_ONLY_TP

async def cb_quick_single_tp_btn(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handles Clear TP button in TP-only mode."""
    query = update.callback_query
    await query.answer()
    context.user_data["active_card_msg_id"] = query.message.message_id
    
    positions = await asyncio.to_thread(get_active_positions)
    if not positions:
        await query.edit_message_text("📭 No active positions.", reply_markup=get_back_keyboard("menu_main"))
        return ConversationHandler.END
        
    target_sym = context.user_data.get("quick_target_symbol", "ALL")
    if target_sym and target_sym != "ALL":
        target_positions = [p for p in positions if p["symbol"] == target_sym]
        if not target_positions:
            target_positions = positions
    else:
        target_positions = positions
        
    res_text = await apply_quick_stops_values(target_positions, sl_val=None, tp_val=0.0)
    await query.edit_message_text(res_text, parse_mode="Markdown", reply_markup=get_back_keyboard("menu_main"))
    return ConversationHandler.END

async def txt_quick_single_tp(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handles typed TP price in TP-only mode in-place."""
    # Clean up user's typed message bubble to keep chat 100% clean
    try:
        await update.message.delete()
    except Exception:
        pass

    text = update.message.text.strip()
    old_card_id = context.user_data.get("active_card_msg_id")
    chat_id = update.effective_chat.id

    try:
        val = float(text)
        if val < 0:
            raise ValueError
    except ValueError:
        err_text = (
            f"❌ *Invalid TP Price*\n"
            f"Please enter a valid positive TP price (e.g. `2790.00`):\n\n"
            f"👇 *Try again or tap Cancel:*"
        )
        if old_card_id:
            try:
                await context.bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=old_card_id,
                    text=err_text,
                    parse_mode="Markdown",
                    reply_markup=get_quick_single_tp_keyboard()
                )
                return WAITING_QUICK_ONLY_TP
            except Exception:
                pass
        await update.message.reply_text(err_text, parse_mode="Markdown", reply_markup=get_quick_single_tp_keyboard())
        return WAITING_QUICK_ONLY_TP
        
    positions = await asyncio.to_thread(get_active_positions)
    if not positions:
        err_msg = "📭 No active positions."
        if old_card_id:
            try:
                await context.bot.edit_message_text(chat_id=chat_id, message_id=old_card_id, text=err_msg, reply_markup=get_back_keyboard("menu_main"))
                return ConversationHandler.END
            except Exception:
                pass
        await update.message.reply_text(err_msg, reply_markup=get_back_keyboard("menu_main"))
        return ConversationHandler.END
        
    target_sym = context.user_data.get("quick_target_symbol", "ALL")
    if target_sym and target_sym != "ALL":
        target_positions = [p for p in positions if p["symbol"] == target_sym]
        if not target_positions:
            target_positions = positions
    else:
        target_positions = positions
        
    res_text = await apply_quick_stops_values(target_positions, sl_val=None, tp_val=val)
    if old_card_id:
        try:
            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=old_card_id,
                text=res_text,
                parse_mode="Markdown",
                reply_markup=get_back_keyboard("menu_main")
            )
            return ConversationHandler.END
        except Exception:
            pass
    await update.message.reply_text(res_text, parse_mode="Markdown", reply_markup=get_back_keyboard("menu_main"))
    return ConversationHandler.END

# --- Direct Chat Input (Fast Mode) ---

async def txt_quick_stops_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Processes typed <SL> <TP> or <SL> input directly from dashboard in-place."""
    # Clean up user's typed message bubble to keep chat 100% clean
    try:
        await update.message.delete()
    except Exception:
        pass

    text = update.message.text.strip()
    old_card_id = context.user_data.get("active_card_msg_id")
    chat_id = update.effective_chat.id
    
    positions = await asyncio.to_thread(get_active_positions)
    if not positions:
        err_msg = "📭 No active positions found to update."
        if old_card_id:
            try:
                await context.bot.edit_message_text(chat_id=chat_id, message_id=old_card_id, text=err_msg, reply_markup=get_back_keyboard("menu_main"))
                return ConversationHandler.END
            except Exception:
                pass
        await update.message.reply_text(err_msg, reply_markup=get_back_keyboard("menu_main"))
        return ConversationHandler.END
        
    target_sym = context.user_data.get("quick_target_symbol", "ALL")
    sl_val, tp_val, target_positions, err = parse_quick_price_input(text, positions, default_symbol=target_sym)
    if err:
        err_prompt = (
            f"{err}\n\n"
            f"👇 *Try again (e.g. `2740.50 2790.00` or `sl 2740.50`):*"
        )
        if old_card_id:
            try:
                await context.bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=old_card_id,
                    text=err_prompt,
                    parse_mode="Markdown",
                    reply_markup=get_quick_stops_keyboard(positions, target_sym)
                )
                return WAITING_QUICK_STOPS_INPUT
            except Exception:
                pass
        await update.message.reply_text(err_prompt, parse_mode="Markdown", reply_markup=get_quick_stops_keyboard(positions, target_sym))
        return WAITING_QUICK_STOPS_INPUT
        
    res_text = await apply_quick_stops_values(target_positions, sl_val, tp_val)
    if old_card_id:
        try:
            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=old_card_id,
                text=res_text,
                parse_mode="Markdown",
                reply_markup=get_back_keyboard("menu_main")
            )
            return ConversationHandler.END
        except Exception:
            pass
    await update.message.reply_text(
        text=res_text,
        parse_mode="Markdown",
        reply_markup=get_back_keyboard("menu_main")
    )
    return ConversationHandler.END

@restricted
async def cb_start_quick_stops(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Triggered from main menu inline button '⚡ Quick Batch SL/TP'."""
    query = update.callback_query
    await query.answer()
    
    positions = await asyncio.to_thread(get_active_positions)
    if not positions:
        await query.edit_message_text(
            text="📭 *No active positions found* on this account to set stops.",
            parse_mode="Markdown",
            reply_markup=get_back_keyboard("menu_main")
        )
        return ConversationHandler.END
        
    context.user_data["quick_target_symbol"] = "ALL"
    return await render_quick_stops_screen(query.message, positions, is_edit=True, selected_symbol="ALL", context=context)

@restricted
async def cmd_quick_stops(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    Direct command handler for /quickstops [SL] [TP] or /faststops.
    Usage:
      /quickstops 2740.50 2790.00
      /quickstops gold 2740.50 2790.00
      /quickstops 2740.50
      /quickstops be
      /quickstops be gold
      /quickstops clear sl
      /quickstops  (opens quick interactive screen)
    """
    positions = await asyncio.to_thread(get_active_positions)
    
    target_message = update.message if update.message else (update.callback_query.message if update.callback_query else None)
    if not target_message:
        return ConversationHandler.END

    if not positions:
        if update.callback_query:
            await update.callback_query.answer()
            await update.callback_query.edit_message_text(
                "📭 *No active positions found* on this account to set stops.",
                parse_mode="Markdown",
                reply_markup=get_back_keyboard("menu_main")
            )
        else:
            try:
                await update.message.delete()
            except Exception:
                pass
            old_card_id = context.user_data.get("active_card_msg_id")
            if old_card_id:
                try:
                    await context.bot.edit_message_text(
                        chat_id=update.effective_chat.id,
                        message_id=old_card_id,
                        text="📭 *No active positions found* on this account to set stops.",
                        parse_mode="Markdown",
                        reply_markup=get_back_keyboard("menu_main")
                    )
                    return ConversationHandler.END
                except Exception:
                    pass
            await target_message.reply_text("📭 No active positions found to update.")
        return ConversationHandler.END
        
    args = getattr(context, "args", None)
    if not args:
        context.user_data["quick_target_symbol"] = "ALL"
        is_edit = update.callback_query is not None
        if update.message:
            try:
                await update.message.delete()
            except Exception:
                pass
        return await render_quick_stops_screen(target_message, positions, is_edit=is_edit, selected_symbol="ALL", context=context)
        
    first_arg = args[0].lower()
    active_symbols = list(set(p["symbol"] for p in positions))
    alias_map = _QUICK_SYMBOL_ALIASES
    
    if first_arg == "be":
        target_positions = positions
        target_name = "All Pairs"
        buffer_pips = 0.0
        
        for a in args[1:]:
            a_upper = a.upper()
            a_lower = a.lower()
            matched = None
            for s in active_symbols:
                if s.upper() == a_upper or s.upper().startswith(a_upper):
                    matched = s
                    break
            if not matched and a_lower in alias_map:
                alias_target = alias_map[a_lower]
                for s in active_symbols:
                    if alias_target in s.upper():
                        matched = s
                        break
            if matched:
                target_positions = [p for p in positions if p["symbol"] == matched]
                target_name = matched
            else:
                try:
                    buffer_pips = float(a)
                except ValueError:
                    pass

        start_time = time.perf_counter()
        success = 0
        skipped = 0
        for pos in target_positions:
            sym_info = await asyncio.to_thread(get_symbol_info, pos["symbol"])
            digits = sym_info["digits"] if sym_info else 5
            point = sym_info["point"] if sym_info else 0.00001
            pip_size = get_symbol_pip_size(pos["symbol"], sym_info) if sym_info else 0.0001
            min_dist = max(sym_info.get("trade_stops_level", 0) * point, 5 * point)
            
            if pos["type"] == "BUY":
                target_sl = round(pos["price_open"] + (buffer_pips * pip_size), digits)
                if pos["price_current"] <= target_sl + min_dist:
                    skipped += 1
                    continue
            else:
                target_sl = round(pos["price_open"] - (buffer_pips * pip_size), digits)
                if pos["price_current"] >= target_sl - min_dist:
                    skipped += 1
                    continue
            res = await asyncio.to_thread(modify_position_stops, pos["ticket"], sl=target_sl, tp=pos["tp"])
            if res["success"]:
                success += 1
                
        elapsed = time.perf_counter() - start_time
        risk_summary_lines = await compute_portfolio_risk_summary_lines()
        risk_block = "\n".join(risk_summary_lines)
        
        await target_message.reply_text(
            f"🛡️ *Break-Even Applied!*\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🔣 *Target:* `{target_name}`\n"
            f"✅ *Updated:* `{success}/{len(target_positions)}`\n"
            f"⚠️ *Skipped (not in profit):* `{skipped}`\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"{risk_block}\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"⏱️ *Time:* `{elapsed:.2f}s`",
            parse_mode="Markdown"
        )
        return ConversationHandler.END
        
    if first_arg == "clear":
        target_positions = positions
        target_name = "All Pairs"
        sub = "all"
        for a in args[1:]:
            a_upper = a.upper()
            a_lower = a.lower()
            matched = None
            for s in active_symbols:
                if s.upper() == a_upper or s.upper().startswith(a_upper):
                    matched = s
                    break
            if not matched and a_lower in alias_map:
                alias_target = alias_map[a_lower]
                for s in active_symbols:
                    if alias_target in s.upper():
                        matched = s
                        break
            if matched:
                target_positions = [p for p in positions if p["symbol"] == matched]
                target_name = matched
            elif a_lower in ("sl", "tp", "all"):
                sub = a_lower

        start_time = time.perf_counter()
        success = 0
        for pos in target_positions:
            new_sl = 0.0 if sub in ("sl", "all") else pos["sl"]
            new_tp = 0.0 if sub in ("tp", "all") else pos["tp"]
            res = await asyncio.to_thread(modify_position_stops, pos["ticket"], sl=new_sl, tp=new_tp)
            if res["success"]:
                success += 1
        elapsed = time.perf_counter() - start_time
        risk_summary_lines = await compute_portfolio_risk_summary_lines()
        risk_block = "\n".join(risk_summary_lines)
        
        await target_message.reply_text(
            f"❌ *Stops Cleared ({sub.upper()})!*\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🔣 *Target:* `{target_name}`\n"
            f"✅ *Updated:* `{success}/{len(target_positions)}`\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"{risk_block}\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"⏱️ *Time:* `{elapsed:.2f}s`",
            parse_mode="Markdown"
        )
        return ConversationHandler.END
        
    input_str = " ".join(args)
    sl_val, tp_val, target_positions, err = parse_quick_price_input(input_str, positions)
    if err:
        await target_message.reply_text(err)
        return ConversationHandler.END
        
    res_text = await apply_quick_stops_values(target_positions, sl_val, tp_val)
    await target_message.reply_text(res_text, parse_mode="Markdown")
    return ConversationHandler.END

async def cb_cancel_batch(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Cancels quick wizard and returns to main menu."""
    query = update.callback_query
    if query:
        await query.answer("Operation cancelled.")
        await query.edit_message_text(
            "❌ *Quick SL/TP operation cancelled.*",
            parse_mode="Markdown",
            reply_markup=get_back_keyboard("menu_main")
        )
    return ConversationHandler.END

async def cmd_cancel_batch(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Abort quick stops conversation via text command."""
    if update.message:
        try:
            await update.message.delete()
        except Exception:
            pass
        old_card_id = context.user_data.get("active_card_msg_id")
        if old_card_id:
            try:
                await context.bot.edit_message_text(
                    chat_id=update.effective_chat.id,
                    message_id=old_card_id,
                    text="❌ *Quick SL/TP operation cancelled.*",
                    parse_mode="Markdown",
                    reply_markup=get_back_keyboard("menu_main")
                )
                return ConversationHandler.END
            except Exception:
                pass
        await update.message.reply_text(
            "❌ *Quick SL/TP operation cancelled.*",
            parse_mode="Markdown",
            reply_markup=get_back_keyboard("menu_main")
        )
    return ConversationHandler.END

async def cb_cancel_to_home_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Cancels quick wizard and displays the home menu."""
    from bot.handlers.account import cb_main_menu
    query = update.callback_query
    if query:
        await query.answer()
        await cb_main_menu(update, context)
    return ConversationHandler.END

# --- Handler Config ---

def get_batch_conv_handler():
    """Returns configured ConversationHandler for Quick Batch SL/TP."""
    return ConversationHandler(
        entry_points=[
            CallbackQueryHandler(cb_start_quick_stops, pattern="^baction_quick_stops$"),
            CommandHandler("quickstops", cmd_quick_stops),
            CommandHandler("faststops", cmd_quick_stops),
            MessageHandler(filters.Regex(r"^⚡ Quick (Batch )?SL/TP$"), cmd_quick_stops),
        ],
        states={
            WAITING_QUICK_STOPS_INPUT: [
                CallbackQueryHandler(cb_quick_pair_toggle, pattern=r"^qpair_.*$"),
                CallbackQueryHandler(cb_quick_mode_select, pattern=r"^qstop_mode_.*$"),
                MessageHandler(NOT_MENU_BUTTON, txt_quick_stops_input)
            ],
            WAITING_QUICK_GUIDED_SL: [
                CallbackQueryHandler(cb_quick_guided_sl_btn, pattern=r"^qsl_.*$"),
                MessageHandler(NOT_MENU_BUTTON, txt_quick_guided_sl)
            ],
            WAITING_QUICK_GUIDED_TP: [
                CallbackQueryHandler(cb_quick_guided_tp_btn, pattern=r"^qtp_.*$"),
                MessageHandler(NOT_MENU_BUTTON, txt_quick_guided_tp)
            ],
            WAITING_QUICK_ONLY_SL: [
                CallbackQueryHandler(cb_quick_single_sl_btn, pattern=r"^qsl_clear_only$"),
                MessageHandler(NOT_MENU_BUTTON, txt_quick_single_sl)
            ],
            WAITING_QUICK_ONLY_TP: [
                CallbackQueryHandler(cb_quick_single_tp_btn, pattern=r"^qtp_clear_only$"),
                MessageHandler(NOT_MENU_BUTTON, txt_quick_single_tp)
            ]
        },
        fallbacks=[
            CommandHandler("cancel", cmd_cancel_batch),
            CallbackQueryHandler(cb_cancel_batch, pattern="^batch_cancel$"),
            CallbackQueryHandler(cb_cancel_to_home_menu, pattern="^batch_back_home$")
        ],
        allow_reentry=True
    )

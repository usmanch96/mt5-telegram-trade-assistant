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
from mt5.monitor import get_active_positions, get_symbol_info, get_active_orders
from mt5.executor import close_position, modify_position_stops, cancel_pending_order
from database.models import remove_trailing_stop, add_trailing_stop
from bot.keyboards import get_batch_keyboard, get_back_keyboard, get_main_keyboard, get_batch_trail_keyboard
from bot.middleware import restricted
from bot.conv_filters import NOT_MENU_BUTTON
from services.logger import logger

# Conversation States for Batch Actions
(
    WAITING_BATCH_SL,
    WAITING_BATCH_TP,
    SELECT_BATCH_SYMBOL,
    SELECT_BATCH_LOT,
    WAITING_BATCH_TRAIL
) = range(5)

@restricted
async def cb_batch_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Displays the batch emergency controls panel."""
    query = update.callback_query

    text = (
        "⚡ *Batch Control Panel*\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "🚨 *Emergency* — instant close actions across all positions\n"
        "⚙️ *Management* — bulk stop and trailing adjustments\n"
        "📋 *Pending* — cancel queued limit/stop orders\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "⚠️ _All actions execute immediately. Double-check before tapping._"
    )

    if query:
        await query.answer()
        await query.edit_message_text(
            text=text,
            parse_mode="Markdown",
            reply_markup=get_batch_keyboard()
        )
    elif update.message:
        await update.message.reply_text(
            text=text,
            parse_mode="Markdown",
            reply_markup=get_batch_keyboard()
        )


# --- Dynamic Keyboard Generators ---

def get_batch_sl_keyboard() -> InlineKeyboardMarkup:
    """Returns option buttons for Stop Loss entry."""
    keyboard = [
        [
            InlineKeyboardButton("⏭️ Keep Unchanged", callback_data="bsl_keep"),
            InlineKeyboardButton("❌ Remove SL", callback_data="bsl_clear")
        ],
        [
            InlineKeyboardButton("❌ Cancel", callback_data="batch_cancel")
        ]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_batch_tp_keyboard() -> InlineKeyboardMarkup:
    """Returns option buttons for Take Profit entry."""
    keyboard = [
        [
            InlineKeyboardButton("⏭️ Keep Unchanged", callback_data="btp_keep"),
            InlineKeyboardButton("❌ Remove TP", callback_data="btp_clear")
        ],
        [
            InlineKeyboardButton("❌ Cancel", callback_data="batch_cancel")
        ]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_dynamic_symbol_keyboard(positions: list) -> InlineKeyboardMarkup:
    """Generates an inline keyboard listing only active open symbols."""
    symbols = sorted(list(set(p["symbol"] for p in positions)))
    
    keyboard = [[InlineKeyboardButton("🌐 All Symbols", callback_data="bsym_all")]]
    row = []
    for sym in symbols:
        row.append(InlineKeyboardButton(sym, callback_data=f"bsym_{sym}"))
        if len(row) == 2:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)
        
    keyboard.append([InlineKeyboardButton("❌ Cancel", callback_data="batch_cancel")])
    return InlineKeyboardMarkup(keyboard)

def get_dynamic_lot_keyboard(positions: list, selected_symbol: str) -> InlineKeyboardMarkup:
    """Generates an inline keyboard listing only active open lot sizes."""
    # Filter positions by selected symbol if applicable
    if selected_symbol != "ALL":
        positions = [p for p in positions if p["symbol"] == selected_symbol]
        
    lots = sorted(list(set(p["volume"] for p in positions)))
    
    keyboard = [[InlineKeyboardButton("🌐 All Lot Sizes", callback_data="blot_all")]]
    row = []
    for lot in lots:
        label = f"🔹 {lot:.2f} Lots"
        row.append(InlineKeyboardButton(label, callback_data=f"blot_{lot:.2f}"))
        if len(row) == 2:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)
        
    keyboard.append([InlineKeyboardButton("❌ Cancel", callback_data="batch_cancel")])
    return InlineKeyboardMarkup(keyboard)

# --- Batch Conversation Wizard handlers ---

@restricted
async def start_batch_action_flow(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Triggered when a user clicks a batch command button."""
    query = update.callback_query
    await query.answer()
    
    action = query.data.split("_", 1)[1] # close_all, close_profit, close_loss, be_all, edit_stops, set_trail, cancel_pending
    context.user_data["b_action"] = action
    
    # Pre-fetch active positions or pending orders depending on the action
    if action == "cancel_pending":
        positions = await asyncio.to_thread(get_active_orders)
        snapshot_name = "pending orders"
    else:
        positions = await asyncio.to_thread(get_active_positions)
        snapshot_name = "active positions"
        
    if not positions:
        await query.edit_message_text(
            text=f"📭 *No {snapshot_name} found* on this account to execute batch commands.",
            parse_mode="Markdown",
            reply_markup=get_back_keyboard("menu_batch")
        )
        return ConversationHandler.END
        
    context.user_data["b_positions_snapshot"] = positions
    
    if action == "edit_stops":
        await query.edit_message_text(
            text=(
                "🔴 *Batch Edit: Stop Loss (SL)*\n\n"
                "Please type the new absolute Stop Loss price (e.g. `2415.50`), or choose an option below:"
            ),
            parse_mode="Markdown",
            reply_markup=get_batch_sl_keyboard()
        )
        return WAITING_BATCH_SL
    elif action == "set_trail":
        await query.edit_message_text(
            text=(
                "📈 *Batch Set Trailing Stop (Pips)*\n\n"
                "Please select a preset trailing stop distance in pips, or click Custom to type:\n"
                "*(Note: 10 pips = $1.00 Gold price movement)*"
            ),
            parse_mode="Markdown",
            reply_markup=get_batch_trail_keyboard()
        )
        return WAITING_BATCH_TRAIL
    else:
        # Move directly to Symbol filter select
        await query.edit_message_text(
            text="🔣 *Filter by Symbol (Pair)?*\nSelect which active pair or commodity to target:",
            parse_mode="Markdown",
            reply_markup=get_dynamic_symbol_keyboard(positions)
        )
        return SELECT_BATCH_SYMBOL

async def cb_batch_sl_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Processes SL selection button and prompts for TP."""
    query = update.callback_query
    await query.answer()
    
    choice = query.data.split("_")[1] # keep, clear
    context.user_data["b_sl_input"] = choice
    
    await query.edit_message_text(
        text=(
            "🟢 *Batch Edit: Take Profit (TP)*\n\n"
            "Please type the new absolute Take Profit price (e.g. `2480.00`), or choose an option below:"
        ),
        parse_mode="Markdown",
        reply_markup=get_batch_tp_keyboard()
    )
    return WAITING_BATCH_TP

async def txt_batch_sl_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Processes typed SL price and prompts for TP."""
    text = update.message.text.strip()
    try:
        val = float(text)
        if val <= 0:
            raise ValueError()
    except ValueError:
        await update.message.reply_text("❌ Invalid SL price. Enter a valid positive price level:")
        return WAITING_BATCH_SL
        
    context.user_data["b_sl_input"] = val
    
    await update.message.reply_text(
        text=(
            "🟢 *Batch Edit: Take Profit (TP)*\n\n"
            "Please type the new absolute Take Profit price (e.g. `2480.00`), or choose an option below:"
        ),
        parse_mode="Markdown",
        reply_markup=get_batch_tp_keyboard()
    )
    return WAITING_BATCH_TP

async def cb_batch_tp_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Processes TP selection button and prompts for Symbol filter."""
    query = update.callback_query
    await query.answer()
    
    choice = query.data.split("_")[1] # keep, clear
    context.user_data["b_tp_input"] = choice
    
    positions = context.user_data.get("b_positions_snapshot")
    
    await query.edit_message_text(
        text="🔣 *Filter by Symbol (Pair)?*\nSelect which active pair or commodity to target:",
        parse_mode="Markdown",
        reply_markup=get_dynamic_symbol_keyboard(positions)
    )
    return SELECT_BATCH_SYMBOL

async def txt_batch_tp_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Processes typed TP price and prompts for Symbol filter."""
    text = update.message.text.strip()
    try:
        val = float(text)
        if val <= 0:
            raise ValueError()
    except ValueError:
        await update.message.reply_text("❌ Invalid TP price. Enter a valid positive price level:")
        return WAITING_BATCH_TP
        
    context.user_data["b_tp_input"] = val
    
    positions = context.user_data.get("b_positions_snapshot")
    
    await update.message.reply_text(
        text="🔣 *Filter by Symbol (Pair)?*\nSelect which active pair or commodity to target:",
        parse_mode="Markdown",
        reply_markup=get_dynamic_symbol_keyboard(positions)
    )
    return SELECT_BATCH_SYMBOL

# --- Batch Trailing Handlers ---

async def cb_batch_trail_select(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Processes trailing stop preset button selection."""
    query = update.callback_query
    await query.answer()
    
    parts = query.data.split("_")
    if parts[1] == "custom":
        await query.edit_message_text(
            text="✍ *Type custom trailing stop distance in Pips:*",
            parse_mode="Markdown"
        )
        return WAITING_BATCH_TRAIL
        
    val = int(parts[2])
    context.user_data["b_trail_input"] = val
    
    positions = context.user_data.get("b_positions_snapshot")
    
    await query.edit_message_text(
        text="🔣 *Filter by Symbol (Pair)?*\nSelect which active pair or commodity to target:",
        parse_mode="Markdown",
        reply_markup=get_dynamic_symbol_keyboard(positions)
    )
    return SELECT_BATCH_SYMBOL

async def txt_batch_trail_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Processes typed trailing distance."""
    text = update.message.text.strip()
    try:
        val = int(text)
        if val < 0:
            raise ValueError()
    except ValueError:
        await update.message.reply_text("❌ Distance must be a positive whole number of pips (or `0` to disable):")
        return WAITING_BATCH_TRAIL
        
    context.user_data["b_trail_input"] = val
    positions = context.user_data.get("b_positions_snapshot")
    
    await update.message.reply_text(
        text="🔣 *Filter by Symbol (Pair)?*\nSelect which active pair or commodity to target:",
        parse_mode="Markdown",
        reply_markup=get_dynamic_symbol_keyboard(positions)
    )
    return SELECT_BATCH_SYMBOL

async def cb_batch_symbol_select(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Processes symbol selection button."""
    query = update.callback_query
    await query.answer()
    
    selection = query.data.split("_", 1)[1] # all or symbol_name
    context.user_data["b_symbol_filter"] = "ALL" if selection == "all" else selection
    
    positions = context.user_data.get("b_positions_snapshot")
    
    await query.edit_message_text(
        text="🎯 *Filter by Lot Size?*\nSelect which active trade size you want to target:",
        parse_mode="Markdown",
        reply_markup=get_dynamic_lot_keyboard(positions, context.user_data["b_symbol_filter"])
    )
    return SELECT_BATCH_LOT

async def cb_batch_lot_select(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Processes lot size selection button and executes the batch command."""
    query = update.callback_query
    await query.answer()
    
    selection = query.data.split("_", 1)[1] # all or float_string
    
    if selection == "all":
        context.user_data["b_lot_filter"] = None
    else:
        context.user_data["b_lot_filter"] = float(selection)
        
    await execute_batch_action(query.message, context)
    return ConversationHandler.END

# --- Core Batch Execution Logic ---

async def execute_batch_action(message, context: ContextTypes.DEFAULT_TYPE):
    """Executes the batch action filtering dynamically by lot size and symbols."""
    action = context.user_data.get("b_action")
    symbol_filter = context.user_data.get("b_symbol_filter")
    lot_filter = context.user_data.get("b_lot_filter")
    
    await message.reply_text("⚡ *Processing emergency batch request...*")
    
    # Query fresh positions or orders list depending on the action
    if action == "cancel_pending":
        positions = await asyncio.to_thread(get_active_orders)
        target_name = "pending orders"
    else:
        positions = await asyncio.to_thread(get_active_positions)
        target_name = "active positions"
        
    if not positions:
        await message.reply_text(f"📭 No {target_name} found to modify.", reply_markup=get_back_keyboard("menu_batch"))
        return
        
    # 1. Filter by Symbol
    filtered_positions = positions
    if symbol_filter and symbol_filter != "ALL":
        filtered_positions = [p for p in filtered_positions if p["symbol"] == symbol_filter]
        
    # 2. Filter by Lot Size
    if lot_filter is not None:
        filtered_positions = [p for p in filtered_positions if abs(p["volume"] - lot_filter) < 0.0001]
        
    # 3. Filter by Action criteria
    if action == "close_profit":
        filtered_positions = [p for p in filtered_positions if p["profit"] > 0]
    elif action == "close_loss":
        filtered_positions = [p for p in filtered_positions if p["profit"] < 0]
    elif action == "be_all":
        filtered_positions = [p for p in filtered_positions if p["profit"] > 0]
        
    if not filtered_positions:
        await message.reply_text(
            text=f"📭 No {target_name} matched the filters for this batch command.",
            reply_markup=get_back_keyboard("menu_batch")
        )
        return
        
    total_trades = len(filtered_positions)
    progress_msg = await message.reply_text(f"⚡ *Processing {total_trades} items... Please wait.*")
    
    success_count = 0
    fail_count = 0
    
    # 4. Loop execution
    for pos in filtered_positions:
        ticket = pos["ticket"]
        
        if action.startswith("close"):
            res = await asyncio.to_thread(close_position, ticket)
            if res["success"]:
                success_count += 1
                await asyncio.to_thread(remove_trailing_stop, ticket)
            else:
                fail_count += 1
                logger.error(f"Batch close failed for ticket {ticket}: {res['message']}")
                
        elif action == "be_all":
            entry = pos["price_open"]
            tp = pos["tp"]
            res = await asyncio.to_thread(modify_position_stops, ticket, sl=entry, tp=tp)
            if res["success"]:
                success_count += 1
            else:
                fail_count += 1
                logger.error(f"Batch BE failed for ticket {ticket}: {res['message']}")
                
        elif action == "edit_stops":
            b_sl = context.user_data.get("b_sl_input")
            b_tp = context.user_data.get("b_tp_input")
            
            if b_sl == "keep":
                sl_price = pos["sl"]
            elif b_sl == "clear":
                sl_price = 0.0
            else:
                sl_price = float(b_sl)
                
            if b_tp == "keep":
                tp_price = pos["tp"]
            elif b_tp == "clear":
                tp_price = 0.0
            else:
                tp_price = float(b_tp)
                
            res = await asyncio.to_thread(modify_position_stops, ticket, sl_price, tp_price)
            if res["success"]:
                success_count += 1
            else:
                fail_count += 1
                logger.error(f"Batch SL/TP modify failed for ticket {ticket}: {res['message']}")
                
        elif action == "set_trail":
            b_trail = context.user_data.get("b_trail_input")
            if b_trail == 0:
                # Remove trailing stop
                res_db = await asyncio.to_thread(remove_trailing_stop, ticket)
                if res_db:
                    success_count += 1
                else:
                    fail_count += 1
            else:
                # Add/Update trailing stop
                res_db = await asyncio.to_thread(add_trailing_stop, ticket, pos["symbol"], b_trail)
                if res_db:
                    success_count += 1
                else:
                    fail_count += 1
                    
        elif action == "cancel_pending":
            res = await asyncio.to_thread(cancel_pending_order, ticket)
            if res["success"]:
                success_count += 1
            else:
                fail_count += 1
                logger.error(f"Batch cancel pending failed for ticket {ticket}: {res['message']}")
                
    # 5. Summary Report
    action_labels = {
        "close_all": "CLOSED (ALL)",
        "close_profit": "CLOSED (PROFIT)",
        "close_loss": "CLOSED (LOSS)",
        "be_all": "MOVED TO BE",
        "edit_stops": "MODIFIED SL/TP STOPS",
        "set_trail": "SET BATCH TRAILING STOPS",
        "cancel_pending": "CANCELLED PENDING ORDERS"
    }
    
    report_action = action_labels.get(action, "PROCESSED")
    report_emoji = "🚨" if action.startswith("close") or action == "cancel_pending" else "🛡️"
    sym_label = f"`{symbol_filter}`" if symbol_filter != "ALL" else "`All Pairs`"
    lot_label = f"`{lot_filter:.2f} Lots`" if lot_filter else "`All Sizes`"
    
    report_text = (
        f"{report_emoji} *Batch Operation Summary*\n\n"
        f"📋 *Action:* `{report_action}`\n"
        f"🔣 *Target Symbol:* {sym_label}\n"
        f"🎯 *Target Lot Sizes:* {lot_label}\n"
        f"✅ *Successful:* `{success_count}/{total_trades}`\n"
        f"❌ *Failed:* `{fail_count}/{total_trades}`\n\n"
        f"Batch execution complete."
    )
    
    await progress_msg.delete()
    await message.reply_text(
        text=report_text,
        parse_mode="Markdown",
        reply_markup=get_back_keyboard("menu_batch")
    )

async def cb_cancel_batch(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Cancels batch wizard and returns to batch panel."""
    query = update.callback_query
    await query.answer("Operation aborted.")
    await query.edit_message_text("❌ *Batch operation cancelled.*", parse_mode="Markdown", reply_markup=get_back_keyboard("menu_batch"))
    return ConversationHandler.END

async def cmd_cancel_batch(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Abort batch conversation via text command."""
    await update.message.reply_text("❌ *Batch operation cancelled.*", parse_mode="Markdown", reply_markup=get_main_keyboard())
    return ConversationHandler.END

# --- Handler Config ---

def get_batch_conv_handler():
    """Returns configured ConversationHandler for stateful batch actions."""
    return ConversationHandler(
        entry_points=[
            CallbackQueryHandler(start_batch_action_flow, pattern=r"^baction_.*$")
        ],
        states={
            WAITING_BATCH_SL: [
                CallbackQueryHandler(cb_batch_sl_button, pattern=r"^bsl_.*$"),
                MessageHandler(NOT_MENU_BUTTON, txt_batch_sl_input)
            ],
            WAITING_BATCH_TP: [
                CallbackQueryHandler(cb_batch_tp_button, pattern=r"^btp_.*$"),
                MessageHandler(NOT_MENU_BUTTON, txt_batch_tp_input)
            ],
            WAITING_BATCH_TRAIL: [
                CallbackQueryHandler(cb_batch_trail_select, pattern=r"^btrail_.*$"),
                MessageHandler(NOT_MENU_BUTTON, txt_batch_trail_input)
            ],
            SELECT_BATCH_SYMBOL: [
                CallbackQueryHandler(cb_batch_symbol_select, pattern=r"^bsym_.*$")
            ],
            SELECT_BATCH_LOT: [
                CallbackQueryHandler(cb_batch_lot_select, pattern=r"^blot_.*$")
            ]
        },
        fallbacks=[
            CommandHandler("cancel", cmd_cancel_batch),
            CallbackQueryHandler(cb_cancel_batch, pattern="^batch_cancel$")
        ],
        allow_reentry=True
    )

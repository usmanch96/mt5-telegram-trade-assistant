from telegram import InlineKeyboardButton, InlineKeyboardMarkup

def get_main_keyboard() -> InlineKeyboardMarkup:
    """Returns the primary unified main menu inline keyboard layout."""
    keyboard = [
        [
            InlineKeyboardButton("💼 Active Positions", callback_data="menu_trades"),
            InlineKeyboardButton("⚡ Quick Batch SL/TP", callback_data="baction_quick_stops")
        ],
        [
            InlineKeyboardButton("🔄 Refresh Status", callback_data="menu_refresh")
        ]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_back_keyboard(back_callback: str = "menu_main") -> InlineKeyboardMarkup:
    """Returns a keyboard with a single 'Back' button."""
    keyboard = [[InlineKeyboardButton("⬅️ Back", callback_data=back_callback)]]
    return InlineKeyboardMarkup(keyboard)

def get_position_control_keyboard(ticket: int) -> InlineKeyboardMarkup:
    """Returns action buttons for a single open position card."""
    keyboard = [
        [
            InlineKeyboardButton("❌ Close Trade", callback_data=f"pos_close_{ticket}"),
            InlineKeyboardButton("🥞 Partial Close", callback_data=f"pos_partial_{ticket}")
        ],
        [
            InlineKeyboardButton("🛡️ Move to BE", callback_data=f"pos_be_{ticket}"),
            InlineKeyboardButton("✏️ Edit SL/TP", callback_data=f"pos_edit_menu_{ticket}")
        ],
        [
            InlineKeyboardButton("⬅️ Back to Positions", callback_data="menu_trades")
        ]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_quick_stops_keyboard(positions: list = None, selected_symbol: str = "ALL") -> InlineKeyboardMarkup:
    """Returns quick price entry actions for batch SL/TP with optional fast pair toggle."""
    keyboard = []
    
    # 1. Quick Pair Selector Row (if 2 or more distinct symbols exist)
    if positions:
        symbols = sorted(list(set(p["symbol"] for p in positions)))
        if len(symbols) > 1:
            pair_row = []
            all_lbl = f"✅ All ({len(positions)})" if selected_symbol == "ALL" else f"🌐 All ({len(positions)})"
            pair_row.append(InlineKeyboardButton(all_lbl, callback_data="qpair_ALL"))
            
            for s in symbols:
                count = sum(1 for p in positions if p["symbol"] == s)
                btn_lbl = f"✅ {s} ({count})" if selected_symbol == s else f"{s} ({count})"
                pair_row.append(InlineKeyboardButton(btn_lbl, callback_data=f"qpair_{s}"))
                if len(pair_row) == 3:
                    keyboard.append(pair_row)
                    pair_row = []
            if pair_row:
                keyboard.append(pair_row)

    # 2. Main Price Entry Actions
    keyboard.extend([
        [
            InlineKeyboardButton("⚡ Set Both SL & TP (Step-by-Step)", callback_data="qstop_mode_both")
        ],
        [
            InlineKeyboardButton("🔴 Set SL Price Only", callback_data="qstop_mode_sl_only"),
            InlineKeyboardButton("🟢 Set TP Price Only", callback_data="qstop_mode_tp_only")
        ],
        [
            InlineKeyboardButton("🛡️ Move to BE", callback_data="qstop_mode_be"),
            InlineKeyboardButton("❌ Clear Stops", callback_data="qstop_mode_clear_all")
        ],
        [
            InlineKeyboardButton("🏠 Main Menu", callback_data="batch_back_home")
        ]
    ])
    return InlineKeyboardMarkup(keyboard)

def get_quick_guided_sl_keyboard() -> InlineKeyboardMarkup:
    """Buttons for guided SL price entry step."""
    keyboard = [
        [
            InlineKeyboardButton("⏭️ Skip SL (Keep Current)", callback_data="qsl_skip"),
            InlineKeyboardButton("❌ Remove SL", callback_data="qsl_clear")
        ],
        [InlineKeyboardButton("❌ Cancel", callback_data="batch_cancel")]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_quick_guided_tp_keyboard() -> InlineKeyboardMarkup:
    """Buttons for guided TP price entry step."""
    keyboard = [
        [
            InlineKeyboardButton("⏭️ Skip TP (Keep Current)", callback_data="qtp_skip"),
            InlineKeyboardButton("❌ Remove TP", callback_data="qtp_clear")
        ],
        [InlineKeyboardButton("❌ Cancel", callback_data="batch_cancel")]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_quick_single_sl_keyboard() -> InlineKeyboardMarkup:
    """Buttons for SL-only price entry."""
    keyboard = [
        [InlineKeyboardButton("❌ Remove SL (Set None)", callback_data="qsl_clear_only")],
        [InlineKeyboardButton("❌ Cancel", callback_data="batch_cancel")]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_quick_single_tp_keyboard() -> InlineKeyboardMarkup:
    """Buttons for TP-only price entry."""
    keyboard = [
        [InlineKeyboardButton("❌ Remove TP (Set None)", callback_data="qtp_clear_only")],
        [InlineKeyboardButton("❌ Cancel", callback_data="batch_cancel")]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_stops_adjustment_keyboard(ticket: int) -> InlineKeyboardMarkup:
    """Stops editing adjustments keyboard allowing clicks instead of typing values."""
    keyboard = [
        [
            InlineKeyboardButton("SL +10 pips", callback_data=f"pos_adj_{ticket}_sl_10"),
            InlineKeyboardButton("SL -10 pips", callback_data=f"pos_adj_{ticket}_sl_-10")
        ],
        [
            InlineKeyboardButton("TP +20 pips", callback_data=f"pos_adj_{ticket}_tp_20"),
            InlineKeyboardButton("TP -20 pips", callback_data=f"pos_adj_{ticket}_tp_-20")
        ],
        [
            InlineKeyboardButton("❌ Remove SL", callback_data=f"pos_adj_{ticket}_sl_clear"),
            InlineKeyboardButton("❌ Remove TP", callback_data=f"pos_adj_{ticket}_tp_clear")
        ],
        [
            InlineKeyboardButton("✍️ Type Custom SL/TP", callback_data=f"pos_adj_{ticket}_custom")
        ],
        [
            InlineKeyboardButton("⬅️ Back to Trade", callback_data=f"pos_view_{ticket}")
        ]
    ]
    return InlineKeyboardMarkup(keyboard)

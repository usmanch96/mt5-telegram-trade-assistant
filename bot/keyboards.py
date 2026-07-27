from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup

def get_persistent_menu_keyboard() -> ReplyKeyboardMarkup:
    """Returns the persistent reply menu keyboard at the bottom of the device screen."""
    keyboard = [
        ["📊 Dashboard", "💼 Active Positions"],
        ["🚀 Open Trade", "🥞 Layering Trade"],
        ["⚡ Batch Controls", "🔔 Price Alerts"],
        ["🧮 Risk Calculator", "🔄 Refresh Status"]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

def get_main_keyboard() -> InlineKeyboardMarkup:
    """Returns the primary main menu inline keyboard layout."""
    keyboard = [
        [
            InlineKeyboardButton("📊 Account Status", callback_data="menu_account"),
            InlineKeyboardButton("💼 Active Positions", callback_data="menu_trades")
        ],
        [
            InlineKeyboardButton("🚀 Open Trade", callback_data="menu_open_trade"),
            InlineKeyboardButton("🥞 Progressive Layering", callback_data="menu_layering")
        ],
        [
            InlineKeyboardButton("⚡ Batch Controls", callback_data="menu_batch"),
            InlineKeyboardButton("🔔 Price Alerts", callback_data="menu_alerts")
        ],
        [
            InlineKeyboardButton("🧮 Smart Risk Calculator", callback_data="menu_calc")
        ]
    ]
    return InlineKeyboardMarkup(keyboard)


def get_back_keyboard(back_callback: str = "menu_main") -> InlineKeyboardMarkup:
    """Returns a keyboard with a single 'Back to Menu' button."""
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
            InlineKeyboardButton("📈 Set Trailing Stop", callback_data=f"pos_trail_menu_{ticket}")
        ],
        [
            InlineKeyboardButton("⬅️ Back to Positions", callback_data="menu_trades")
        ]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_batch_keyboard() -> InlineKeyboardMarkup:
    """Returns options for batch trade management, grouped by risk level."""
    keyboard = [
        # Section: EMERGENCY
        [InlineKeyboardButton("━━━━ 🚨 EMERGENCY ━━━━", callback_data="noop")],
        [InlineKeyboardButton("🚨 CLOSE ALL POSITIONS", callback_data="baction_close_all")],
        [
            InlineKeyboardButton("🟢 Close Profit Only", callback_data="baction_close_profit"),
            InlineKeyboardButton("🔴 Close Loss Only", callback_data="baction_close_loss")
        ],
        # Section: MANAGEMENT
        [InlineKeyboardButton("━━━━ ⚙️ MANAGEMENT ━━━━", callback_data="noop")],
        [InlineKeyboardButton("🛡️ Move All to Break-Even", callback_data="baction_be_all")],
        [
            InlineKeyboardButton("✏️ Batch Edit SL/TP", callback_data="baction_edit_stops"),
            InlineKeyboardButton("📈 Batch Trailing", callback_data="baction_set_trail")
        ],
        # Section: PENDING
        [InlineKeyboardButton("━━━━ 📋 PENDING ━━━━", callback_data="noop")],
        [InlineKeyboardButton("❌ Cancel Pending Orders", callback_data="baction_cancel_pending")],
        # Back
        [InlineKeyboardButton("⬅️ Back", callback_data="menu_main")]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_batch_trail_keyboard() -> InlineKeyboardMarkup:
    """Preset trailing stop distances for batch controls."""
    keyboard = [
        [
            InlineKeyboardButton("50 Pips ($5.0)", callback_data="btrail_preset_50"),
            InlineKeyboardButton("100 Pips ($10.0)", callback_data="btrail_preset_100"),
            InlineKeyboardButton("150 Pips ($15.0)", callback_data="btrail_preset_150")
        ],
        [
            InlineKeyboardButton("200 Pips ($20.0)", callback_data="btrail_preset_200"),
            InlineKeyboardButton("300 Pips ($30.0)", callback_data="btrail_preset_300")
        ],
        [
            InlineKeyboardButton("⏹️ Disable Trailing", callback_data="btrail_preset_0"),
            InlineKeyboardButton("✍️ Custom Pips", callback_data="btrail_custom")
        ],
        [
            InlineKeyboardButton("❌ Cancel", callback_data="batch_cancel")
        ]
    ]
    return InlineKeyboardMarkup(keyboard)


def get_alert_keyboard() -> InlineKeyboardMarkup:
    """Returns price alert settings buttons."""
    keyboard = [
        [
            InlineKeyboardButton("➕ Add Price Alert", callback_data="alert_add"),
            InlineKeyboardButton("📋 View Active Alerts", callback_data="alert_list")
        ],
        [
            InlineKeyboardButton("⬅️ Back", callback_data="menu_main")
        ]
    ]
    return InlineKeyboardMarkup(keyboard)

# --- Button Presets for Trade & Position Adjustments ---

def get_preset_lots_keyboard() -> InlineKeyboardMarkup:
    """Preset lot sizes for trade wizard."""
    keyboard = [
        [
            InlineKeyboardButton("0.01 Lots", callback_data="lots_0.01"),
            InlineKeyboardButton("0.02 Lots", callback_data="lots_0.02"),
            InlineKeyboardButton("0.05 Lots", callback_data="lots_0.05")
        ],
        [
            InlineKeyboardButton("0.10 Lots", callback_data="lots_0.10"),
            InlineKeyboardButton("0.50 Lots", callback_data="lots_0.50"),
            InlineKeyboardButton("1.00 Lots", callback_data="lots_1.00")
        ],
        [
            InlineKeyboardButton("✍️ Custom Lot Size", callback_data="lots_custom")
        ],
        [
            InlineKeyboardButton("❌ Cancel", callback_data="trade_cancel")
        ]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_preset_risk_keyboard() -> InlineKeyboardMarkup:
    """Preset risk percentages for trade wizard."""
    keyboard = [
        [
            InlineKeyboardButton("0.5% Risk", callback_data="risk_0.5"),
            InlineKeyboardButton("1.0% Risk", callback_data="risk_1.0"),
            InlineKeyboardButton("1.5% Risk", callback_data="risk_1.5")
        ],
        [
            InlineKeyboardButton("2.0% Risk", callback_data="risk_2.0"),
            InlineKeyboardButton("3.0% Risk", callback_data="risk_3.0"),
            InlineKeyboardButton("5.0% Risk", callback_data="risk_5.0")
        ],
        [
            InlineKeyboardButton("✍️ Custom Risk", callback_data="risk_custom")
        ],
        [
            InlineKeyboardButton("❌ Cancel", callback_data="trade_cancel")
        ]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_preset_trail_keyboard(ticket: int) -> InlineKeyboardMarkup:
    """Preset trailing stop distances for position controls (Gold-optimized)."""
    keyboard = [
        [
            InlineKeyboardButton("50 Pips ($5.0)", callback_data=f"trail_set_{ticket}_50"),
            InlineKeyboardButton("100 Pips ($10.0)", callback_data=f"trail_set_{ticket}_100"),
            InlineKeyboardButton("150 Pips ($15.0)", callback_data=f"trail_set_{ticket}_150")
        ],
        [
            InlineKeyboardButton("200 Pips ($20.0)", callback_data=f"trail_set_{ticket}_200"),
            InlineKeyboardButton("300 Pips ($30.0)", callback_data=f"trail_set_{ticket}_300")
        ],
        [
            InlineKeyboardButton("⏹️ Disable Trailing", callback_data=f"trail_set_{ticket}_0"),
            InlineKeyboardButton("✍️ Custom Pips", callback_data=f"trail_custom_{ticket}")
        ],
        [
            InlineKeyboardButton("⬅️ Cancel", callback_data=f"pos_view_{ticket}")
        ]
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

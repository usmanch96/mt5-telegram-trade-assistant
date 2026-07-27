import MetaTrader5 as mt5
from typing import Dict, Any, Optional
from mt5.monitor import get_symbol_info, get_account_details
from services.logger import logger

def calculate_lot_size(
    symbol: str,
    risk_percent: float,
    sl_pips: float
) -> Optional[float]:
    """
    Calculates the exact lot size required to risk a specific percentage of account equity
    given a Stop Loss distance in standard pips (1 pip = 10 points/ticks for Forex).
    
    Returns the aligned lot size (float) or None if parameters are invalid.
    """
    try:
        symbol = symbol.upper()
        
        # 1. Fetch Symbol details
        sym_info = get_symbol_info(symbol)
        if not sym_info:
            logger.error(f"Cannot calculate risk: Symbol {symbol} info not retrieved.")
            return None
            
        # 2. Fetch Account details
        acc_info = get_account_details()
        if not acc_info:
            logger.error("Cannot calculate risk: Account details not retrieved.")
            return None
            
        equity = acc_info["equity"]
        risk_amount = equity * (risk_percent / 100.0)
        
        # Determine points count. Forex standard is 1 pip = 10 points. 
        # For non-forex pairs, we treat the inputted pips directly as points or tick movements.
        # Let's check standard digit conversion (e.g. 5 digits for standard forex)
        digits = sym_info["digits"]
        point = sym_info["point"]
        tick_size = sym_info["trade_tick_size"]
        tick_value = sym_info["trade_tick_value"]
        
        if tick_size == 0 or tick_value == 0:
            logger.error(f"Invalid contract specifications for {symbol}: tick_size={tick_size}, tick_value={tick_value}")
            return None
            
        # Standardize pip conversion:
        # For 3 and 5 digit forex pairs, 1 pip = 10 * point.
        # For other symbols (e.g., indices, commodities like XAUUSD), we treat 1 pip = 1 * tick_size (or 0.1 price units).
        is_forex = (digits == 3 or digits == 5) and ("USD" in symbol or "EUR" in symbol or "GBP" in symbol or "JPY" in symbol or "AUD" in symbol or "CAD" in symbol or "NZD" in symbol or "CHF" in symbol)
        
        if is_forex:
            pip_size = 10 * point
        else:
            # Gold, Cryptos, Indices: treat 1 pip as 10 * tick_size (or standard 0.1 points for gold)
            pip_size = 10 * tick_size
            
        # Total price change for the Stop Loss
        sl_price_delta = sl_pips * pip_size
        
        # Convert SL price delta into ticks/points
        sl_ticks = sl_price_delta / tick_size
        
        if sl_ticks <= 0:
            logger.error(f"Invalid stop loss ticks calculation: {sl_ticks}")
            return None
            
        # Calculate unrounded lot size
        # Loss per 1 Lot = sl_ticks * tick_value
        loss_per_lot = sl_ticks * tick_value
        unrounded_lot = risk_amount / loss_per_lot
        
        # 3. Round to broker lot step configurations
        volume_min = sym_info["volume_min"]
        volume_max = sym_info["volume_max"]
        volume_step = sym_info["volume_step"]
        
        # Round down to the nearest volume step to avoid over-risking
        multiplier = unrounded_lot / volume_step
        rounded_lot = int(multiplier) * volume_step
        
        # Clip to min/max boundaries
        if rounded_lot < volume_min:
            rounded_lot = volume_min
        if rounded_lot > volume_max:
            rounded_lot = volume_max
            
        # Double check that we aren't exceeding available margin or risking too much
        logger.info(
            f"Risk Calc for {symbol}: Equity={equity:.2f}, Risk={risk_amount:.2f}, "
            f"SL={sl_pips} pips ({sl_price_delta:.5f} price delta), "
            f"Loss/Lot={loss_per_lot:.2f}. Result={rounded_lot:.2f} lots."
        )
        return round(rounded_lot, 2)
        
    except Exception as e:
        logger.error(f"Error calculating lot size for {symbol}: {e}", exc_info=True)
        return None

def calculate_lot_size_by_delta(
    symbol: str,
    risk_percent: float,
    sl_price_delta: float
) -> Optional[float]:
    """
    Calculates the exact lot size required to risk a specific percentage of account equity
    given a Stop Loss price delta (absolute distance in price units).
    
    Returns the aligned lot size (float) or None if parameters are invalid.
    """
    try:
        symbol = symbol.upper()
        
        # 1. Fetch Symbol details
        sym_info = get_symbol_info(symbol)
        if not sym_info:
            logger.error(f"Cannot calculate risk: Symbol {symbol} info not retrieved.")
            return None
            
        # 2. Fetch Account details
        acc_info = get_account_details()
        if not acc_info:
            logger.error("Cannot calculate risk: Account details not retrieved.")
            return None
            
        equity = acc_info["equity"]
        risk_amount = equity * (risk_percent / 100.0)
        
        tick_size = sym_info["trade_tick_size"]
        tick_value = sym_info["trade_tick_value"]
        
        if tick_size == 0 or tick_value == 0:
            logger.error(f"Invalid contract specifications for {symbol}: tick_size={tick_size}, tick_value={tick_value}")
            return None
            
        # Convert SL price delta into ticks/points
        sl_ticks = sl_price_delta / tick_size
        
        if sl_ticks <= 0:
            logger.error(f"Invalid stop loss ticks calculation: {sl_ticks}")
            return None
            
        # Calculate unrounded lot size
        loss_per_lot = sl_ticks * tick_value
        unrounded_lot = risk_amount / loss_per_lot
        
        # 3. Round to broker lot step configurations
        volume_min = sym_info["volume_min"]
        volume_max = sym_info["volume_max"]
        volume_step = sym_info["volume_step"]
        
        # Round down to the nearest volume step to avoid over-risking
        multiplier = unrounded_lot / volume_step
        rounded_lot = int(multiplier) * volume_step
        
        # Clip to min/max boundaries
        if rounded_lot < volume_min:
            rounded_lot = volume_min
        if rounded_lot > volume_max:
            rounded_lot = volume_max
            
        logger.info(
            f"Risk Calc (Price Delta) for {symbol}: Equity={equity:.2f}, Risk={risk_amount:.2f}, "
            f"SL Delta={sl_price_delta:.5f}, Loss/Lot={loss_per_lot:.2f}. Result={rounded_lot:.2f} lots."
        )
        return round(rounded_lot, 2)
        
    except Exception as e:
        logger.error(f"Error calculating lot size by delta for {symbol}: {e}", exc_info=True)
        return None

def calculate_sl_price_from_risk(
    symbol: str,
    direction: str,
    volume: float,
    risk_currency_amount: float
) -> Optional[dict]:
    """
    Given a fixed lot size and risk amount in currency ($), calculates the exact SL price.
    Supports both Standard Dollar accounts and Cent accounts (USDC/XAUUSDc).
    """
    try:
        symbol = symbol.strip()
        sym_info = get_symbol_info(symbol)
        if not sym_info:
            return None

        acc_info = get_account_details()
        is_cent = False
        if acc_info:
            curr = acc_info.get("currency", "").upper()
            if "USDC" in curr or "USCC" in curr or "EURC" in curr or curr.endswith("C"):
                is_cent = True

        digits = sym_info["digits"]
        if any(x in symbol.upper() for x in ["XAU", "XAG", "BTC", "US30", "NAS100", "SPX"]):
            digits = min(digits, 2)

        point = sym_info["point"]
        tick_size = sym_info["trade_tick_size"]
        tick_value = sym_info["trade_tick_value"]

        if tick_size <= 0:
            tick_size = 10 * point if (digits == 3 or digits == 5) else (point if point > 0 else 0.01)

        if tick_value <= 0:
            if digits == 3 or digits == 5:
                tick_value = 10.0
                tick_size = 10 * point if point > 0 else 0.0001
            else:
                tick_value = tick_size if tick_size > 0 else 1.0

        # Cent account / symbol suffix scaling (e.g. XAUUSDc)
        if is_cent or (symbol and symbol.endswith("c") and len(symbol) > 4):
            if tick_value > 0 and (tick_value / (tick_size or 1)) > 1000:
                tick_value = tick_value / 100.0

        # Value per 1 price unit delta for given volume
        val_per_price_unit = (tick_value / tick_size) * volume
        if val_per_price_unit <= 0:
            return None

        sl_delta = risk_currency_amount / val_per_price_unit
        current_price = sym_info["ask"] if direction.upper() == "BUY" else sym_info["bid"]

        if direction.upper() == "BUY":
            sl_price = current_price - sl_delta
        else:
            sl_price = current_price + sl_delta

        sl_price = round(sl_price, digits)

        return {
            "symbol": symbol,
            "direction": direction.upper(),
            "entry_price": current_price,
            "sl_price": sl_price,
            "sl_delta": sl_delta,
            "volume": volume,
            "risk_amount": risk_currency_amount,
            "digits": digits
        }
    except Exception as e:
        logger.error(f"Error calculating SL price from risk for {symbol}: {e}", exc_info=True)
        return None

def calculate_lot_from_fixed_risk(
    symbol: str,
    sl_price: float,
    risk_currency_amount: float,
    entry_price: Optional[float] = None
) -> Optional[dict]:
    """
    Given a target SL price and risk amount ($), calculates recommended lot size.
    """
    try:
        symbol = symbol.strip()
        sym_info = get_symbol_info(symbol)
        if not sym_info:
            return None

        acc_info = get_account_details()
        is_cent = False
        if acc_info:
            curr = acc_info.get("currency", "").upper()
            if "USDC" in curr or "USCC" in curr or "EURC" in curr or curr.endswith("C"):
                is_cent = True

        digits = sym_info["digits"]
        if any(x in symbol.upper() for x in ["XAU", "XAG", "BTC", "US30", "NAS100", "SPX"]):
            digits = min(digits, 2)

        point = sym_info["point"]
        tick_size = sym_info["trade_tick_size"]
        tick_value = sym_info["trade_tick_value"]

        if tick_size <= 0:
            tick_size = 10 * point if (digits == 3 or digits == 5) else (point if point > 0 else 0.01)

        if tick_value <= 0:
            if digits == 3 or digits == 5:
                tick_value = 10.0
                tick_size = 10 * point if point > 0 else 0.0001
            else:
                tick_value = tick_size if tick_size > 0 else 1.0

        if is_cent or (symbol and symbol.endswith("c") and len(symbol) > 4):
            if tick_value > 0 and (tick_value / (tick_size or 1)) > 1000:
                tick_value = tick_value / 100.0

        if not entry_price or entry_price <= 0:
            entry_price = sym_info["ask"]

        sl_delta = abs(entry_price - sl_price)
        if sl_delta <= 0:
            return None

        loss_per_lot = (sl_delta / tick_size) * tick_value
        if loss_per_lot <= 0:
            return None

        unrounded_lot = risk_currency_amount / loss_per_lot

        volume_min = sym_info["volume_min"]
        volume_max = sym_info["volume_max"]
        volume_step = sym_info["volume_step"]

        multiplier = unrounded_lot / volume_step
        rounded_lot = int(multiplier) * volume_step
        if rounded_lot < volume_min:
            rounded_lot = volume_min
        if rounded_lot > volume_max:
            rounded_lot = volume_max

        return {
            "symbol": symbol,
            "entry_price": entry_price,
            "sl_price": sl_price,
            "sl_delta": sl_delta,
            "recommended_lot": round(rounded_lot, 2),
            "risk_amount": risk_currency_amount,
            "digits": digits
        }
    except Exception as e:
        logger.error(f"Error calculating lot from fixed risk for {symbol}: {e}", exc_info=True)
        return None

def calculate_layer_risk_stack(
    symbol: str,
    direction: str,
    num_layers: int,
    risk_currency_amount: float,
    lot_per_layer: Optional[float] = None
) -> Optional[dict]:
    """
    Calculates exact per-layer lot size, zone depth, and SL price for an N-layer stack
    so that if ALL N layers trigger and hit SL, total combined loss = risk_currency_amount.
    """
    try:
        symbol = symbol.strip()
        direction = direction.upper()
        sym_info = get_symbol_info(symbol)
        if not sym_info:
            return None

        acc_info = get_account_details()
        is_cent = False
        if acc_info:
            curr = acc_info.get("currency", "").upper()
            if "USDC" in curr or "USCC" in curr or "EURC" in curr or curr.endswith("C"):
                is_cent = True

        digits = sym_info["digits"]
        if any(x in symbol.upper() for x in ["XAU", "XAG", "BTC", "US30", "NAS100", "SPX"]):
            digits = min(digits, 2)

        point = sym_info["point"]
        tick_size = sym_info["trade_tick_size"]
        tick_value = sym_info["trade_tick_value"]

        if tick_size <= 0:
            tick_size = 10 * point if (digits == 3 or digits == 5) else (point if point > 0 else 0.01)

        if tick_value <= 0:
            if digits == 3 or digits == 5:
                tick_value = 10.0
                tick_size = 10 * point if point > 0 else 0.0001
            else:
                tick_value = tick_size if tick_size > 0 else 1.0

        if is_cent or (symbol and symbol.endswith("c") and len(symbol) > 4):
            if tick_value > 0 and (tick_value / (tick_size or 1)) > 1000:
                tick_value = tick_value / 100.0

        v_min = sym_info["volume_min"]
        if not lot_per_layer or lot_per_layer < v_min:
            lot_per_layer = v_min

        current_price = sym_info["ask"] if direction == "BUY" else sym_info["bid"]
        val_per_price_unit_per_layer = (tick_value / tick_size) * lot_per_layer

        if val_per_price_unit_per_layer <= 0 or num_layers <= 0:
            return None

        # Cumulative distance multiplier for N layers: (N + 1) / 2
        cum_dist_factor = (num_layers + 1.0) / 2.0
        zone_depth = risk_currency_amount / (val_per_price_unit_per_layer * cum_dist_factor)

        step = zone_depth / float(num_layers)

        if direction == "BUY":
            sl_price = round(current_price - zone_depth, digits)
        else:
            sl_price = round(current_price + zone_depth, digits)

        layer_entries = []
        for i in range(num_layers):
            if direction == "BUY":
                l_entry = round(current_price - (i * step), digits)
                l_dist = abs(l_entry - sl_price)
            else:
                l_entry = round(current_price + (i * step), digits)
                l_dist = abs(l_entry - sl_price)

            l_loss = (l_dist / tick_size) * tick_value * lot_per_layer
            layer_entries.append({
                "layer_num": i + 1,
                "type": "Market" if i == 0 else "Limit",
                "price": l_entry,
                "loss_at_sl": round(l_loss, 2)
            })

        return {
            "symbol": symbol,
            "direction": direction,
            "entry_price": current_price,
            "num_layers": num_layers,
            "lot_per_layer": lot_per_layer,
            "total_volume": round(lot_per_layer * num_layers, 2),
            "sl_price": sl_price,
            "zone_depth": zone_depth,
            "step": step,
            "risk_amount": risk_currency_amount,
            "digits": digits,
            "layers": layer_entries
        }
    except Exception as e:
        logger.error(f"Error calculating layer risk stack for {symbol}: {e}", exc_info=True)
        return None


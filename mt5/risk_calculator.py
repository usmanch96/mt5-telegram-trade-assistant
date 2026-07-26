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

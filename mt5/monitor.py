import MetaTrader5 as mt5
from typing import Dict, Any, List, Optional
from mt5.connector import check_connection
from services.logger import logger

def get_account_details() -> Optional[Dict[str, Any]]:
    """
    Retrieves current account status.
    Returns a dictionary of account statistics or None if request fails.
    """
    if not check_connection():
        logger.error("Cannot retrieve account details: MT5 not connected.")
        return None
        
    try:
        acc_info = mt5.account_info()
        if acc_info is None:
            logger.error(f"Failed to fetch account info. Error: {mt5.last_error()}")
            return None
            
        return {
            "login": acc_info.login,
            "name": acc_info.name,
            "trade_mode": acc_info.trade_mode,
            "leverage": acc_info.leverage,
            "currency": acc_info.currency,
            "balance": acc_info.balance,
            "equity": acc_info.equity,
            "profit": acc_info.profit,
            "margin": acc_info.margin,
            "margin_free": acc_info.margin_free,
            "margin_level": acc_info.margin_level,
            "company": acc_info.company
        }
    except Exception as e:
        logger.error(f"Exception while getting account details: {e}")
        return None

def get_active_positions(symbol: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    Retrieves all currently active trade positions.
    Optionally filters by a specific symbol.
    """
    if not check_connection():
        logger.error("Cannot retrieve positions: MT5 not connected.")
        return []
        
    try:
        if symbol:
            raw_positions = mt5.positions_get(symbol=symbol.upper())
        else:
            raw_positions = mt5.positions_get()
            
        if raw_positions is None:
            err = mt5.last_error()
            if err[0] != 1:  # 1 means no positions found (not actually an error)
                logger.error(f"Failed to get active positions. Error: {err}")
            return []
            
        positions = []
        for pos in raw_positions:
            pos_type = "BUY" if pos.type == mt5.POSITION_TYPE_BUY else "SELL"
            positions.append({
                "ticket": pos.ticket,
                "symbol": pos.symbol,
                "volume": pos.volume,
                "type": pos_type,
                "price_open": pos.price_open,
                "price_current": pos.price_current,
                "sl": pos.sl,
                "tp": pos.tp,
                "profit": pos.profit,
                "swap": pos.swap,
                "comment": pos.comment,
                "time": pos.time
            })
        return positions
    except Exception as e:
        logger.error(f"Exception while getting active positions: {e}")
        return []

def get_symbol_info(symbol: str) -> Optional[Dict[str, Any]]:
    """
    Retrieves information for a specific trading symbol (e.g., pip size, bid/ask, tick size).
    Supports exact symbol names, uppercase, and Cent account suffixes (e.g., XAUUSDc).
    """
    if not check_connection():
        return None

    try:
        sym_clean = symbol.strip()
        base = sym_clean.upper()
        candidates_raw = [
            sym_clean,
            base,
            base + "c",    # Cent account (Exness, etc.)
            base + "m",    # Micro / Mini account
            base + ".r",   # Raw spread account
            base + "r",
            base + ".a",
            base + "pro",
            base + "_i",
        ]
        if base.endswith("C"):
            candidates_raw.append(base[:-1] + "c")
        if base.endswith("M"):
            candidates_raw.append(base[:-1] + "m")

        candidates = list(dict.fromkeys(candidates_raw))

        info = None
        for candidate in candidates:
            if mt5.symbol_select(candidate, True):
                info = mt5.symbol_info(candidate)
                if info is not None:
                    break

        if info is None:
            logger.error(f"Failed to select symbol '{symbol}' in Market Watch.")
            return None

        return {
            "name": info.name,
            "digits": info.digits,
            "point": info.point,
            "trade_tick_size": info.trade_tick_size,
            "trade_tick_value": info.trade_tick_value,
            "ask": info.ask,
            "bid": info.bid,
            "volume_min": info.volume_min,
            "volume_max": info.volume_max,
            "volume_step": info.volume_step,
            "description": info.description
        }
    except Exception as e:
        logger.error(f"Exception getting symbol info for {symbol}: {e}")
        return None

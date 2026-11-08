NON_FOREX_KEYWORDS = (
    "XAU", "GOLD", "XAG", "SILVER", "OIL", "WTI", "BRENT",
    "BTC", "ETH", "US30", "NAS100", "SPX", "GER", "DAX", "JP225", "HK50", "US500", "USTEC"
)

def get_symbol_pip_size(symbol: str, sym_info: dict) -> float:
    """
    Returns the price value of 1 pip for a given symbol:
    - Forex 5-digit & 3-digit pairs: 10 * point (e.g. 0.00010 for EURUSD, 0.010 for USDJPY)
    - Gold (XAUUSD): 0.10 price units ($0.10 price movement)
    - Silver (XAGUSD): 0.01 price units
    - Cryptos / Indices: standardized movement step
    """
    sym_upper = symbol.upper()
    digits = sym_info.get("digits", 5)
    point = sym_info.get("point", 0.00001)
    tick_size = sym_info.get("trade_tick_size", point or 0.00001)
    
    is_non_forex = any(k in sym_upper for k in NON_FOREX_KEYWORDS)
    
    if not is_non_forex and (digits in (3, 5)):
        return 10.0 * point
    elif "XAU" in sym_upper or "GOLD" in sym_upper:
        # Standard gold pip is $0.10
        return 0.10 if (tick_size <= 0.10) else tick_size
    elif "XAG" in sym_upper or "SILVER" in sym_upper:
        return 0.01 if (tick_size <= 0.01) else tick_size
    elif any(k in sym_upper for k in ("BTC", "ETH", "US30", "NAS100", "SPX", "GER", "DAX", "JP225")):
        return max(10.0 * tick_size, 1.0) if tick_size >= 0.1 else (10.0 * tick_size)
    else:
        return 10.0 * tick_size if tick_size > 0 else (10.0 * point)

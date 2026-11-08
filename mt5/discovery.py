import os
import sys
import glob
import re
from typing import List, Dict, Optional
import psutil

# Terminal ANSI Color & Style Configuration
_USE_ANSI = sys.stdout.isatty()
if os.name == "nt":
    os.system("")  # Initialize Windows Virtual Terminal sequences

C_CYAN    = "\033[96m" if _USE_ANSI else ""
C_BLUE    = "\033[94m" if _USE_ANSI else ""
C_GREEN   = "\033[92m" if _USE_ANSI else ""
C_YELLOW  = "\033[93m" if _USE_ANSI else ""
C_RED     = "\033[91m" if _USE_ANSI else ""
C_MAGENTA = "\033[95m" if _USE_ANSI else ""
C_DIM     = "\033[90m" if _USE_ANSI else ""
C_BOLD    = "\033[1m"  if _USE_ANSI else ""
C_RESET   = "\033[0m"  if _USE_ANSI else ""

def _strip_ansi(t: str) -> str:
    """Strips ANSI escape characters from a string."""
    return re.sub(r'\033\[[0-9;]*m', '', t)

def _vis_len(text: str) -> int:
    """Calculates true monospace terminal cell display width, taking 2-column emojis into account."""
    clean = _strip_ansi(text)
    length = 0
    for char in clean:
        code = ord(char)
        if code > 0x2500 and (0x1F300 <= code <= 0x1FAFF or 0x2600 <= code <= 0x27BF):
            length += 2
        elif code in (0xFE0F,):  # Variation selector (zero-width)
            continue
        else:
            length += 1
    return length

def _box_line(text: str, width: int = 72, border_color: str = "") -> str:
    """Wraps text in a rounded-corner box line with exact character padding."""
    vl = _vis_len(text)
    pad = max(0, width - 4 - vl)
    return f"{border_color}│{C_RESET}  {text}{' ' * pad}{border_color}│{C_RESET}"

def scan_installed_terminals() -> List[Dict[str, str]]:
    """
    Scans system drives and running processes for all installed MT5 terminals.
    Returns a list of dicts: [{'name': 'XM Global MT5', 'path': '...', 'running': True/False}]
    """
    terminals = {}
    running_paths = set()

    # 1. Check running processes to find active MT5 terminals only
    for proc in psutil.process_iter(['name', 'exe']):
        try:
            p_name = (proc.info.get('name') or '').lower()
            p_exe = proc.info.get('exe') or ''
            # MetaTrader 5 executable is strictly terminal64.exe (MT4 is terminal.exe, Windows is windowsterminal.exe)
            if p_name == "terminal64.exe" and p_exe:
                if "windowsapps" not in p_exe.lower():
                    running_paths.add(os.path.abspath(p_exe).lower())
        except Exception:
            pass

    # 2. Search common installation directories
    candidate_roots = [
        os.environ.get("ProgramFiles", r"C:\Program Files"),
        os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs")
    ]

    for base in candidate_roots:
        if base and os.path.isdir(base):
            pattern = os.path.join(base, "*", "terminal64.exe")
            for p in glob.glob(pattern):
                abs_p = os.path.abspath(p)
                if "windowsapps" in abs_p.lower():
                    continue
                folder_name = os.path.basename(os.path.dirname(abs_p))
                is_running = abs_p.lower() in running_paths
                terminals[abs_p.lower()] = {
                    "name": folder_name,
                    "path": abs_p,
                    "running": is_running
                }

    # Add any running MT5 terminals that weren't in standard folders (e.g. portable installs)
    for rp in running_paths:
        if rp not in terminals and os.path.exists(rp):
            folder_name = os.path.basename(os.path.dirname(rp))
            terminals[rp] = {
                "name": f"{folder_name} (Active)",
                "path": rp,
                "running": True
            }

    # Sort: running terminals first, then alphabetical by name
    sorted_terms = sorted(terminals.values(), key=lambda t: (not t["running"], t["name"].lower()))
    return sorted_terms

def select_terminal_interactive() -> Optional[str]:
    """
    Presents a modern, polished interactive terminal chooser in the console on startup.
    Returns the chosen terminal executable path, or None for default/auto-attach.
    """
    args = sys.argv[1:]
    if "--auto" in args:
        return None

    for i, a in enumerate(args):
        if a == "--terminal" and i + 1 < len(args):
            val = args[i + 1]
            if os.path.exists(val):
                return val

    # Non-interactive console (automated scripts, background tasks, or tests)
    if not sys.stdin.isatty():
        return None

    terminals = scan_installed_terminals()
    w = 72
    b_top = f"╭{'─' * (w - 2)}╮"
    b_bot = f"╰{'─' * (w - 2)}╯"

    if not terminals:
        print(f"\n{C_YELLOW}{b_top}{C_RESET}")
        print(_box_line(f"{C_BOLD}ℹ️  NO MT5 INSTALLATIONS DETECTED{C_RESET}", w, C_YELLOW))
        print(_box_line(f"{C_DIM}Using default system MT5 connection path...{C_RESET}", w, C_YELLOW))
        print(f"{C_YELLOW}{b_bot}{C_RESET}\n")
        return None

    # Determine default selection (prefer active/running terminal if any)
    default_idx = 1
    for i, term in enumerate(terminals, 1):
        if term["running"]:
            default_idx = i
            break

    # Header Card
    print(f"\n{C_CYAN}{b_top}{C_RESET}")
    print(_box_line(f"{C_BOLD}🚀  METATRADER 5 · MULTI-TERMINAL SELECTOR{C_RESET}", w, C_CYAN))
    print(_box_line(f"{C_DIM}Interactive Broker Selector & Dispatcher{C_RESET}", w, C_CYAN))
    print(f"{C_CYAN}{b_bot}{C_RESET}")

    # Installed terminals list
    print(f"\n {C_BOLD}{C_CYAN}💻 DETECTED BROKER INSTALLATIONS:{C_RESET}")
    print(f" {C_DIM}{'─' * (w - 2)}{C_RESET}")

    for i, term in enumerate(terminals, 1):
        num_badge = f"{C_CYAN}{C_BOLD}[{i}]{C_RESET}"
        name_str = f"{C_BOLD}{term['name']}{C_RESET}"
        status_badge = f"{C_GREEN}{C_BOLD}[ 🟢 ACTIVE ]{C_RESET}" if term["running"] else f"{C_DIM}[ ⚪ STANDBY ]{C_RESET}"
        
        pad = max(2, w - 24 - len(term['name']))
        print(f"  {num_badge}  {name_str}{' ' * pad}{status_badge}")
        print(f"       {C_DIM}📂 {term['path']}{C_RESET}\n")

    auto_opt = len(terminals) + 1
    print(f" {C_DIM}{'─' * (w - 2)}{C_RESET}")
    print(f" {C_BOLD}{C_YELLOW}⚡ QUICK ACTIONS:{C_RESET}")
    print(f"  {C_YELLOW}{C_BOLD}[A]{C_RESET}  {C_BOLD}Auto-Attach{C_RESET}    {C_DIM}Connect to whichever MT5 terminal is open (or [{auto_opt}]){C_RESET}")
    print(f"  {C_RED}{C_BOLD}[Q]{C_RESET}  {C_BOLD}Quit & Exit{C_RESET}    {C_DIM}Close the assistant cleanly (or [0]){C_RESET}")
    print(f" {C_DIM}{'─' * (w - 2)}{C_RESET}\n")

    prompt = f" {C_BOLD}❯ Select option [1-{len(terminals)}, A, Q]{C_RESET} {C_DIM}(Press Enter for [{default_idx}]){C_RESET} {C_CYAN}──>{C_RESET} "
    
    try:
        raw_choice = input(prompt).strip()
    except (EOFError, KeyboardInterrupt):
        print(f"\n\n{C_RED}{b_top}{C_RESET}")
        print(_box_line(f"{C_BOLD}🚪 Exiting MT5 Assistant.{C_RESET} Goodbye!", w, C_RED))
        print(f"{C_RED}{b_bot}{C_RESET}\n")
        sys.exit(0)

    # Clean Quit commands
    if raw_choice.lower() in ("0", "q", "quit", "exit"):
        print(f"\n{C_RED}{b_top}{C_RESET}")
        print(_box_line(f"{C_BOLD}🚪 Exiting MT5 Assistant.{C_RESET} Goodbye!", w, C_RED))
        print(f"{C_RED}{b_bot}{C_RESET}\n")
        sys.exit(0)

    # Empty enter defaults to default_idx
    choice = raw_choice if raw_choice else str(default_idx)

    # Auto-attach options
    if choice.lower() in ("a", "auto", str(auto_opt)):
        print(f"\n{C_YELLOW}{b_top}{C_RESET}")
        print(_box_line(f"{C_BOLD}⚡ AUTO-ATTACH MODE ENABLED{C_RESET}", w, C_YELLOW))
        print(_box_line(f"{C_DIM}Attaching to current active MetaTrader 5 terminal session...{C_RESET}", w, C_YELLOW))
        print(_box_line(f"{C_CYAN}🚀 Initializing MT5 connection and starting Telegram bot...{C_RESET}", w, C_YELLOW))
        print(f"{C_YELLOW}{b_bot}{C_RESET}\n")
        return None

    # Numbered terminal selection
    try:
        choice_num = int(choice)
        if 1 <= choice_num <= len(terminals):
            selected = terminals[choice_num - 1]
            p_len = len(selected['path'])
            p_display = selected['path'] if p_len <= (w - 16) else "..." + selected['path'][-(w - 19):]
            
            print(f"\n{C_GREEN}{b_top}{C_RESET}")
            print(_box_line(f"{C_BOLD}✅ TARGET LOCKED:{C_RESET} {C_BOLD}{selected['name']}{C_RESET}", w, C_GREEN))
            print(_box_line(f"{C_DIM}📍 Path:{C_RESET} {p_display}", w, C_GREEN))
            print(_box_line(f"{C_CYAN}🚀 Initializing MT5 connection and starting Telegram bot...{C_RESET}", w, C_GREEN))
            print(f"{C_GREEN}{b_bot}{C_RESET}\n")
            return selected["path"]
    except ValueError:
        pass

    # Fallback if invalid input was provided
    fallback = terminals[default_idx - 1]
    print(f"\n{C_YELLOW}ℹ️ Invalid input '{raw_choice}'. Defaulting to [{default_idx}] {fallback['name']}.{C_RESET}\n")
    return fallback["path"]

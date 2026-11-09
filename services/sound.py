import os
import queue
import time
import threading
from typing import Optional, Dict, Any, Tuple
from config import settings
from services.logger import logger

try:
    import winsound
except ImportError:
    winsound = None

try:
    import pythoncom
    import win32com.client
    HAS_COM = True
except ImportError:
    HAS_COM = False

# Mapping of events to (wav_file, voice_phrase)
EVENT_SOUND_MAP: Dict[str, Tuple[str, str]] = {
    "ORDER_FILLED": ("ok.wav", "Order filled"),
    "ORDER_CLOSED": ("ok.wav", "Position closed"),
    "PARTIAL_CLOSED": ("ok.wav", "Partial closed"),
    "STOPS_MODIFIED": ("stops.wav", "Stops updated"),
    "SL_UPDATED": ("stops.wav", "Stop loss updated"),
    "TP_UPDATED": ("stops.wav", "Take profit updated"),
    "BREAK_EVEN": ("stops.wav", "Stop loss moved to break even"),
    "TP_HIT": ("stops.wav", "Take profit hit"),
    "SL_HIT": ("alert2.wav", "Stop loss hit"),
    "STOP_OUT": ("alert.wav", "Stop out hit"),
    "TEST": ("ok.wav", "Sound alert test successful"),
}

# Fallback sounds when specific wav is not found
FALLBACK_WAVS = {
    "alert2.wav": ["alert.wav", "timeout.wav", "stops.wav"],
    "stops.wav": ["ok.wav"],
    "ok.wav": ["tick.wav"],
}

_cached_sounds_dir: Optional[str] = None
_sounds_dir_searched: bool = False
_sound_queue: queue.Queue = queue.Queue()
_worker_thread: Optional[threading.Thread] = None
_worker_lock = threading.Lock()


def find_sounds_dir() -> Optional[str]:
    """
    Locates the MT5 Sounds directory containing .wav alert files.
    Caches result for subsequent calls.
    """
    global _cached_sounds_dir, _sounds_dir_searched
    if _sounds_dir_searched:
        return _cached_sounds_dir

    _sounds_dir_searched = True

    # 1. Explicitly configured sounds directory
    if settings.MT5_SOUNDS_DIR and os.path.isdir(settings.MT5_SOUNDS_DIR):
        _cached_sounds_dir = settings.MT5_SOUNDS_DIR
        logger.info(f"Found MT5 Sounds directory from settings: {_cached_sounds_dir}")
        return _cached_sounds_dir

    # 2. Relative to configured MT5_PATH
    if settings.MT5_PATH:
        candidate = os.path.join(os.path.dirname(settings.MT5_PATH), "Sounds")
        if os.path.isdir(candidate):
            _cached_sounds_dir = candidate
            logger.info(f"Found MT5 Sounds directory next to MT5_PATH: {_cached_sounds_dir}")
            return _cached_sounds_dir

    # 3. Known standard MT5 installation paths on Windows
    candidates = [
        r"C:\Program Files\MetaTrader 5 EXNESS\Sounds",
        r"C:\Program Files\MetaTrader 5\Sounds",
        r"C:\Program Files\FxPro Markets MT5\Sounds",
        r"C:\Program Files\XM Global MT5\Sounds",
        r"C:\Program Files (x86)\MetaTrader 5\Sounds",
        r"C:\Program Files (x86)\MetaTrader 5 EXNESS\Sounds",
    ]
    for c in candidates:
        if os.path.isdir(c) and os.path.exists(os.path.join(c, "ok.wav")):
            _cached_sounds_dir = c
            logger.info(f"Discovered standard MT5 Sounds directory: {_cached_sounds_dir}")
            return _cached_sounds_dir

    logger.warning("Could not locate MT5 Sounds directory. Sound alerts will fall back to Windows system chimes.")
    return None


def get_sounds_dir() -> Optional[str]:
    """Returns the cached MT5 Sounds directory, searching if not already found."""
    return find_sounds_dir()


def _resolve_wav_path(wav_name: str) -> Optional[str]:
    """Finds full path to the requested wav file with fallbacks."""
    s_dir = find_sounds_dir()
    if not s_dir:
        return None

    direct_path = os.path.join(s_dir, wav_name)
    if os.path.exists(direct_path):
        return direct_path

    # Try fallback filenames
    fallbacks = FALLBACK_WAVS.get(wav_name, ["ok.wav"])
    for fb in fallbacks:
        fb_path = os.path.join(s_dir, fb)
        if os.path.exists(fb_path):
            return fb_path

    return None


def _play_sync(event: str, wav_name: str, phrase: str) -> None:
    """
    Executes playback of chime and/or voice inside the background worker thread.
    Guaranteed not to throw exceptions.
    """
    mode = getattr(settings, "SOUND_ALERT_MODE", "voice").lower()

    # 1. Chime Playback (MT5 WAV file)
    if mode in ("both", "chime") and winsound is not None:
        try:
            wav_path = _resolve_wav_path(wav_name)
            if wav_path:
                winsound.PlaySound(wav_path, winsound.SND_FILENAME)
            else:
                # Fall back to Windows system asterisk beep
                winsound.MessageBeep(getattr(winsound, "MB_ICONASTERISK", 0x00000040))
        except Exception as e:
            logger.debug(f"Chime playback failed for event {event}: {e}")

    # 2. Voice Speech Announcement (SAPI)
    if mode in ("both", "voice") and phrase and HAS_COM:
        try:
            pythoncom.CoInitialize()
            try:
                voice = win32com.client.Dispatch("SAPI.SpVoice")
                # Voice speaks synchronously within this background worker thread
                voice.Speak(phrase)
            finally:
                pythoncom.CoUninitialize()
        except Exception as e:
            logger.debug(f"Voice announcement failed for event {event}: {e}")


def _worker_loop() -> None:
    """Worker loop that processes audio requests sequentially in the background."""
    while True:
        try:
            item = _sound_queue.get()
            if item is None:
                break
            event, wav_name, phrase = item
            _play_sync(event, wav_name, phrase)
        except Exception as e:
            logger.debug(f"Sound queue worker error: {e}")
        finally:
            _sound_queue.task_done()


def _ensure_worker_running() -> None:
    """Starts the audio worker thread if not already active."""
    global _worker_thread
    with _worker_lock:
        if _worker_thread is None or not _worker_thread.is_alive():
            _worker_thread = threading.Thread(
                target=_worker_loop,
                name="AudioAlertWorker",
                daemon=True
            )
            _worker_thread.start()


_last_sound_time: Dict[str, float] = {}

def play_sound(event: str) -> None:
    """
    Triggers an asynchronous sound alert (chime and/or voice) on the local PC speaker.
    Safe to call from any thread or async event loop; returns immediately.
    """
    if not getattr(settings, "SOUND_ALERTS_ENABLED", True):
        return

    event_upper = event.upper()
    now = time.time()
    last_time = _last_sound_time.get(event_upper, 0.0)
    # Debounce identical sound events within 1.5 seconds (prevents repeating voice 5x in batch operations)
    if now - last_time < 1.5:
        return
    _last_sound_time[event_upper] = now

    sound_def = EVENT_SOUND_MAP.get(event_upper)
    if not sound_def:
        wav_name, phrase = "ok.wav", "Notification"
    else:
        wav_name, phrase = sound_def

    _ensure_worker_running()
    _sound_queue.put((event_upper, wav_name, phrase))


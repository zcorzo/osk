import os
import sys
import platform
import ctypes
import ctypes.wintypes
import threading
import time
import json
import bisect
import urllib.request
import re
from typing import Optional

import webview

# Windows virtual-key codes
VK_CODES = {
    # Letters A–Z
    **{chr(c): c for c in range(0x41, 0x5B)},  # 'A'-'Z' -> 0x41-0x5A

    # Digits 0–9
    '0': 0x30,
    '1': 0x31,
    '2': 0x32,
    '3': 0x33,
    '4': 0x34,
    '5': 0x35,
    '6': 0x36,
    '7': 0x37,
    '8': 0x38,
    '9': 0x39,

    # Control / navigation keys
    'Backspace': 0x08,
    'Tab': 0x09,
    'Enter': 0x0D,
    'Space': 0x20,
    ' ': 0x20,
    'Shift': 0x10,
    'Control': 0x11,
    'Alt': 0x12,
    'CapsLock': 0x14,
    'Escape': 0x1B,

    'PageUp': 0x21,
    'PageDown': 0x22,
    'End': 0x23,
    'Home': 0x24,
    'ArrowLeft': 0x25,
    'ArrowUp': 0x26,
    'ArrowRight': 0x27,
    'ArrowDown': 0x28,
    'Insert': 0x2D,
    'Delete': 0x2E,
    'PrintScreen': 0x2C,

    # Windows / Meta
    'Meta': 0x5B,   # Left Windows key

    # Punctuation on US layout
    '`': 0xC0,      # VK_OEM_3
    '-': 0xBD,      # VK_OEM_MINUS
    '=': 0xBB,      # VK_OEM_PLUS
    '[': 0xDB,      # VK_OEM_4
    ']': 0xDD,      # VK_OEM_6
    '\\': 0xDC,    # VK_OEM_5
    ';': 0xBA,      # VK_OEM_1
    "'": 0xDE,     # VK_OEM_7
    ',': 0xBC,      # VK_OEM_COMMA
    '.': 0xBE,      # VK_OEM_PERIOD
    '/': 0xBF,      # VK_OEM_2

    # For convenience, treat '+' as the same physical key as '='
    '+': 0xBB,
}

KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004
INPUT_KEYBOARD = 1

if platform.system() == 'Windows':
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
else:
    user32 = None
    kernel32 = None

if ctypes.sizeof(ctypes.c_void_p) == 8:
    _LONG_PTR = ctypes.c_longlong
else:
    _LONG_PTR = ctypes.c_long

WINDOW_TITLE = 'Hex Keyboard'
APP_NAME = 'HexKeyboard'
MACRO_COUNT = 7

WORDLIST_URL = 'https://raw.githubusercontent.com/dwyl/english-words/master/words_alpha.txt'
WORDLIST_FILENAME = 'words.txt'
WORDLIST_EXTRA_FILENAMES = ('places.txt', 'custom.txt')

_hwnd_lock = threading.Lock()
_last_target_hwnd: Optional[int] = None
_osk_hwnd: Optional[int] = None

_config_lock = threading.Lock()

_words_lock = threading.Lock()
_words: Optional[list] = None
_base_freq: Optional[dict] = None
_display_map: Optional[dict] = None

_usage_lock = threading.Lock()
_usage = {}

_aspect_ratio_lock = threading.Lock()
_aspect_ratio: Optional[float] = None
_old_wndproc: Optional[int] = None
_new_wndproc = None


def _set_last_target_hwnd(hwnd: Optional[int]):
    global _last_target_hwnd
    with _hwnd_lock:
        _last_target_hwnd = hwnd


def _get_last_target_hwnd() -> Optional[int]:
    with _hwnd_lock:
        return _last_target_hwnd


def _set_osk_hwnd(hwnd: Optional[int]):
    global _osk_hwnd
    with _hwnd_lock:
        _osk_hwnd = hwnd


def _get_osk_hwnd() -> Optional[int]:
    with _hwnd_lock:
        return _osk_hwnd


def _get_foreground_hwnd() -> Optional[int]:
    hwnd = user32.GetForegroundWindow()
    return hwnd or None


def _get_window_thread_id(hwnd: int) -> int:
    pid = ctypes.wintypes.DWORD()
    return user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))


def _focus_window(hwnd: int):
    """Best-effort focus switch to target hwnd."""
    if not hwnd:
        return

    foreground = user32.GetForegroundWindow()
    if foreground == hwnd:
        return

    current_tid = kernel32.GetCurrentThreadId()
    foreground_tid = _get_window_thread_id(foreground) if foreground else 0
    target_tid = _get_window_thread_id(hwnd)

    if foreground_tid and foreground_tid != current_tid:
        user32.AttachThreadInput(current_tid, foreground_tid, True)
    if target_tid and target_tid != current_tid:
        user32.AttachThreadInput(current_tid, target_tid, True)

    user32.ShowWindow(hwnd, 5)  # SW_SHOW
    user32.SetForegroundWindow(hwnd)
    user32.SetFocus(hwnd)

    if target_tid and target_tid != current_tid:
        user32.AttachThreadInput(current_tid, target_tid, False)
    if foreground_tid and foreground_tid != current_tid:
        user32.AttachThreadInput(current_tid, foreground_tid, False)


def _find_window_by_title(title: str, timeout_s: float = 5.0) -> Optional[int]:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        hwnd = user32.FindWindowW(None, title)
        if hwnd:
            return hwnd
        time.sleep(0.05)
    return None


def _track_last_active_window():
    """Poll the active (foreground) window and remember the last non-keyboard window."""
    while True:
        osk = _get_osk_hwnd()
        hwnd = _get_foreground_hwnd()
        if hwnd and hwnd != osk:
            _set_last_target_hwnd(hwnd)
        time.sleep(0.1)


GWL_WNDPROC = -4
WM_SIZING = 0x0214

WMSZ_LEFT = 1
WMSZ_RIGHT = 2
WMSZ_TOP = 3
WMSZ_TOPLEFT = 4
WMSZ_TOPRIGHT = 5
WMSZ_BOTTOM = 6
WMSZ_BOTTOMLEFT = 7
WMSZ_BOTTOMRIGHT = 8

_WNDPROC = getattr(ctypes, 'WINFUNCTYPE', ctypes.CFUNCTYPE)(
    _LONG_PTR,
    ctypes.wintypes.HWND,
    ctypes.wintypes.UINT,
    ctypes.wintypes.WPARAM,
    ctypes.wintypes.LPARAM,
)


def _enforce_window_aspect_ratio(rect: ctypes.wintypes.RECT, edge: int, ratio: float):
    width = rect.right - rect.left
    height = rect.bottom - rect.top
    if width <= 0 or height <= 0:
        return

    if edge in (WMSZ_LEFT, WMSZ_RIGHT):
        new_height = int(round(width / ratio))
        cy = (rect.top + rect.bottom) // 2
        rect.top = cy - (new_height // 2)
        rect.bottom = rect.top + new_height
        return

    if edge in (WMSZ_TOP, WMSZ_BOTTOM):
        new_width = int(round(height * ratio))
        cx = (rect.left + rect.right) // 2
        rect.left = cx - (new_width // 2)
        rect.right = rect.left + new_width
        return

    if edge in (WMSZ_TOPLEFT, WMSZ_TOPRIGHT):
        new_height = int(round(width / ratio))
        rect.top = rect.bottom - new_height
        return

    if edge in (WMSZ_BOTTOMLEFT, WMSZ_BOTTOMRIGHT):
        new_height = int(round(width / ratio))
        rect.bottom = rect.top + new_height
        return


def _install_aspect_ratio_hook(hwnd: int):
    global _aspect_ratio, _old_wndproc, _new_wndproc

    if not user32 or not hwnd:
        return

    get_window_rect = getattr(user32, 'GetWindowRect', None)
    call_window_proc = getattr(user32, 'CallWindowProcW', None)
    get_wndproc_ptr = getattr(user32, 'GetWindowLongPtrW', None)
    get_wndproc_32 = getattr(user32, 'GetWindowLongW', None)
    set_wndproc_ptr = getattr(user32, 'SetWindowLongPtrW', None)
    set_wndproc_32 = getattr(user32, 'SetWindowLongW', None)

    if not get_window_rect or not call_window_proc:
        return

    if not (get_wndproc_ptr or get_wndproc_32):
        return

    if not (set_wndproc_ptr or set_wndproc_32):
        return

    get_window_rect.argtypes = [ctypes.wintypes.HWND, ctypes.POINTER(ctypes.wintypes.RECT)]
    get_window_rect.restype = ctypes.wintypes.BOOL

    call_window_proc.argtypes = [
        _LONG_PTR,
        ctypes.wintypes.HWND,
        ctypes.wintypes.UINT,
        ctypes.wintypes.WPARAM,
        ctypes.wintypes.LPARAM,
    ]
    call_window_proc.restype = _LONG_PTR

    if get_wndproc_ptr:
        get_wndproc_ptr.argtypes = [ctypes.wintypes.HWND, ctypes.c_int]
        get_wndproc_ptr.restype = _LONG_PTR
    if get_wndproc_32:
        get_wndproc_32.argtypes = [ctypes.wintypes.HWND, ctypes.c_int]
        get_wndproc_32.restype = ctypes.c_long

    if set_wndproc_ptr:
        set_wndproc_ptr.argtypes = [ctypes.wintypes.HWND, ctypes.c_int, _LONG_PTR]
        set_wndproc_ptr.restype = _LONG_PTR
    if set_wndproc_32:
        set_wndproc_32.argtypes = [ctypes.wintypes.HWND, ctypes.c_int, ctypes.c_long]
        set_wndproc_32.restype = ctypes.c_long

    rect = ctypes.wintypes.RECT()
    if not get_window_rect(hwnd, ctypes.byref(rect)):
        return

    width = rect.right - rect.left
    height = rect.bottom - rect.top
    if width <= 0 or height <= 0:
        return

    with _aspect_ratio_lock:
        _aspect_ratio = width / height

    if get_wndproc_ptr:
        _old_wndproc = int(get_wndproc_ptr(hwnd, GWL_WNDPROC))
    else:
        _old_wndproc = int(get_wndproc_32(hwnd, GWL_WNDPROC))

    def _proc(h, msg, wparam, lparam):
        if msg == WM_SIZING:
            with _aspect_ratio_lock:
                r = _aspect_ratio

            if r:
                sizing_rect = ctypes.cast(lparam, ctypes.POINTER(ctypes.wintypes.RECT)).contents
                _enforce_window_aspect_ratio(sizing_rect, int(wparam), float(r))
                return 1

        old = _old_wndproc
        if not old:
            return 0

        return call_window_proc(old, h, msg, wparam, lparam)

    _new_wndproc = _WNDPROC(_proc)
    new_addr = ctypes.cast(_new_wndproc, ctypes.c_void_p).value

    if set_wndproc_ptr:
        set_wndproc_ptr(hwnd, GWL_WNDPROC, _LONG_PTR(new_addr))
    else:
        set_wndproc_32(hwnd, GWL_WNDPROC, ctypes.c_long(new_addr))


def _config_dir() -> str:
    appdata = os.getenv('APPDATA')
    if appdata:
        base = appdata
    else:
        base = os.path.expanduser('~')

    path = os.path.join(base, APP_NAME)
    os.makedirs(path, exist_ok=True)
    return path


def _config_path() -> str:
    return os.path.join(_config_dir(), 'config.json')


def _wordlist_path(filename: str) -> str:
    return os.path.join(_config_dir(), filename)


def _download_wordlist_if_missing():
    path = _wordlist_path(WORDLIST_FILENAME)
    if os.path.exists(path):
        return

    tmp = path + '.tmp'
    with urllib.request.urlopen(WORDLIST_URL, timeout=30) as r:
        data = r.read()

    with open(tmp, 'wb') as f:
        f.write(data)

    os.replace(tmp, path)


def _copy_bundled_wordlist_extras_if_missing():
    for filename in WORDLIST_EXTRA_FILENAMES:
        dst = _wordlist_path(filename)
        if os.path.exists(dst):
            continue

        src = resource_path(filename)
        if not os.path.exists(src):
            continue

        tmp = dst + '.tmp'
        with open(src, 'rb') as rf:
            data = rf.read()

        with open(tmp, 'wb') as wf:
            wf.write(data)

        os.replace(tmp, dst)


def _is_allowed_term(term: str) -> bool:
    if not term:
        return False

    if not term[0].isalnum():
        return False

    for c in term:
        if c.isalnum():
            continue
        if c in " .'-":
            continue
        return False

    return True


def _parse_term_line(line: str):
    s = line.strip()
    if not s:
        return None

    if s.startswith('#'):
        return None

    freq = 1
    if '\t' in s:
        left, right = s.rsplit('\t', 1)
        right = right.strip()
        if right.isdigit():
            s = left.strip()
            freq = int(right)

    display = re.sub(r"\s+", " ", s).strip()
    if not display:
        return None

    term = display.lower()

    if not any(c.isalpha() for c in term):
        return None

    if not _is_allowed_term(term):
        return None

    return term, display, max(1, freq)


def _load_wordlist():
    freqs = {}
    display_map = {}
    english_single = set()

    def _is_simple_titlecase(word: str) -> bool:
        return bool(word) and word.isalpha() and word[0].isupper() and word[1:].islower()

    def _update_display_map(term: str, display: str, source: str):
        existing = display_map.get(term)
        if existing is None:
            display_map[term] = display
            return

        # If this term is also a common English word and the override is only a
        # simple TitleCase (e.g. React), keep lowercase.
        if term in english_single and _is_simple_titlecase(display):
            return

        # Prefer a cased (non-lowercase) display form.
        if existing.islower() and not display.islower():
            display_map[term] = display
            return

        # If the user explicitly provided casing, let it win.
        if source == 'user' and not display.islower() and display != existing:
            display_map[term] = display

    # Load the (large) base English word list first.
    base_path = _wordlist_path(WORDLIST_FILENAME)
    if os.path.exists(base_path):
        with open(base_path, 'r', encoding='utf-8', errors='ignore') as f:
            for line in f:
                parsed = _parse_term_line(line)
                if not parsed:
                    continue

                term, display, freq = parsed
                freqs[term] = freqs.get(term, 0) + freq
                display_map[term] = term
                if ' ' not in term:
                    english_single.add(term)

    # First: load bundled display forms (for capitalization), but don't affect weights.
    for filename in WORDLIST_EXTRA_FILENAMES:
        bundled = resource_path(filename)
        if not os.path.exists(bundled):
            continue

        with open(bundled, 'r', encoding='utf-8', errors='ignore') as f:
            for line in f:
                parsed = _parse_term_line(line)
                if not parsed:
                    continue

                term, display, _freq = parsed
                _update_display_map(term, display, 'bundled')

    # Then: load the user's extra dictionaries (weights + display).
    for filename in WORDLIST_EXTRA_FILENAMES:
        path = _wordlist_path(filename)
        if not os.path.exists(path):
            continue

        with open(path, 'r', encoding='utf-8', errors='ignore') as f:
            for line in f:
                parsed = _parse_term_line(line)
                if not parsed:
                    continue

                term, display, freq = parsed
                freqs[term] = freqs.get(term, 0) + freq
                _update_display_map(term, display, 'user')

    words = sorted(freqs.keys())
    return words, freqs, display_map


def _init_wordlist_background():
    global _words, _base_freq, _display_map

    try:
        _download_wordlist_if_missing()
    except Exception:
        pass

    try:
        _copy_bundled_wordlist_extras_if_missing()
    except Exception:
        pass

    try:
        words, freqs, display_map = _load_wordlist()
    except Exception:
        words, freqs, display_map = [], {}, {}

    with _words_lock:
        _words = words
        _base_freq = freqs
        _display_map = display_map


def _load_config() -> dict:
    path = _config_path()
    try:
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}


def _save_config(data: dict):
    path = _config_path()
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def _load_usage() -> dict:
    with _config_lock:
        data = _load_config()
        raw = data.get('usage')

    if not isinstance(raw, dict):
        return {}

    cleaned = {}
    for k, v in raw.items():
        if not isinstance(k, str):
            continue
        if not isinstance(v, (int, float)):
            continue
        cleaned[k] = int(v)

    return cleaned


def _save_usage(usage: dict):
    with _config_lock:
        data = _load_config()
        data['usage'] = usage
        _save_config(data)


def load_macros() -> list:
    with _config_lock:
        data = _load_config()
        raw = data.get('macros')

    if not isinstance(raw, list):
        return ['' for _ in range(MACRO_COUNT)]

    macros = []
    for i in range(MACRO_COUNT):
        v = raw[i] if i < len(raw) else ''
        macros.append(v if isinstance(v, str) else '')
    return macros


def save_macros(macros: list) -> bool:
    if not isinstance(macros, list):
        return False

    cleaned = []
    for i in range(MACRO_COUNT):
        v = macros[i] if i < len(macros) else ''
        cleaned.append(v if isinstance(v, str) else '')

    with _config_lock:
        data = _load_config()
        data['macros'] = cleaned
        _save_config(data)

    return True


def _on_webview_started():
    global _usage

    with _usage_lock:
        _usage = _load_usage()

    # Identify our own window handle and start foreground tracking.
    hwnd = _find_window_by_title(WINDOW_TITLE)
    if hwnd is None:
        hwnd = _get_foreground_hwnd()
    _set_osk_hwnd(hwnd)

    _install_aspect_ratio_hook(hwnd)

    t = threading.Thread(target=_track_last_active_window, daemon=True)
    t.start()

    w = threading.Thread(target=_init_wordlist_background, daemon=True)
    w.start()


if hasattr(ctypes.wintypes, 'ULONG_PTR'):
    _ULONG_PTR = ctypes.wintypes.ULONG_PTR
else:
    _ULONG_PTR = ctypes.c_ulonglong if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_ulong


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ('wVk', ctypes.wintypes.WORD),
        ('wScan', ctypes.wintypes.WORD),
        ('dwFlags', ctypes.wintypes.DWORD),
        ('time', ctypes.wintypes.DWORD),
        ('dwExtraInfo', _ULONG_PTR),
    ]


class _INPUT_UNION(ctypes.Union):
    _fields_ = [
        ('ki', KEYBDINPUT),
    ]


class INPUT(ctypes.Structure):
    _anonymous_ = ('u',)
    _fields_ = [
        ('type', ctypes.wintypes.DWORD),
        ('u', _INPUT_UNION),
    ]


if user32:
    user32.SendInput.argtypes = [ctypes.wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
    user32.SendInput.restype = ctypes.wintypes.UINT

    user32.VkKeyScanW.argtypes = [ctypes.wintypes.WCHAR]
    user32.VkKeyScanW.restype = ctypes.c_short


def _send_unicode_unit(scan_code: int) -> bool:
    if not user32:
        return False

    down_ki = KEYBDINPUT(wVk=0, wScan=scan_code, dwFlags=KEYEVENTF_UNICODE, time=0, dwExtraInfo=0)
    up_ki = KEYBDINPUT(wVk=0, wScan=scan_code, dwFlags=KEYEVENTF_UNICODE | KEYEVENTF_KEYUP, time=0, dwExtraInfo=0)

    down = INPUT(type=INPUT_KEYBOARD, u=_INPUT_UNION(ki=down_ki))
    up = INPUT(type=INPUT_KEYBOARD, u=_INPUT_UNION(ki=up_ki))

    inputs = (INPUT * 2)(down, up)
    sent = user32.SendInput(2, inputs, ctypes.sizeof(INPUT))
    return sent == 2


def type_unicode(text: str) -> bool:
    if not text:
        return False

    data = text.encode('utf-16-le')
    ok = True
    for i in range(0, len(data), 2):
        scan = data[i] | (data[i + 1] << 8)
        ok = _send_unicode_unit(scan) and ok
    return ok


def press_vk(vk_code: int):
    """Send a simple key press (down + up) to Windows."""
    user32.keybd_event(vk_code, 0, 0, 0)
    user32.keybd_event(vk_code, 0, KEYEVENTF_KEYUP, 0)


def press_combo(modifiers, vk: int):
    """Press modifiers, press key, release key, release modifiers."""
    for mod_vk in modifiers:
        user32.keybd_event(mod_vk, 0, 0, 0)

    user32.keybd_event(vk, 0, 0, 0)
    user32.keybd_event(vk, 0, KEYEVENTF_KEYUP, 0)

    for mod_vk in reversed(list(modifiers)):
        user32.keybd_event(mod_vk, 0, KEYEVENTF_KEYUP, 0)


class Api:
    """JS→Python bridge. Exposed to JavaScript as window.pywebview.api."""

    def get_macros(self):
        return load_macros()

    def set_macros(self, macros):
        return save_macros(macros)

    def record_usage(self, term):
        if not isinstance(term, str):
            return False

        parsed = _parse_term_line(term)
        if not parsed:
            return False

        normalized, _, _freq = parsed

        with _usage_lock:
            _usage[normalized] = _usage.get(normalized, 0) + 1
            snapshot = dict(_usage)

        _save_usage(snapshot)
        return True

    def suggest(self, prefix: str, limit: int = 3):
        if not isinstance(prefix, str):
            return []

        p = prefix.strip().lower()
        if not p:
            return []

        with _words_lock:
            words = _words
            freqs = _base_freq
            display_map = _display_map

        if not words:
            return []

        with _usage_lock:
            usage = _usage

        start = bisect.bisect_left(words, p)

        limit = int(limit) if isinstance(limit, (int, float)) else 3
        limit = max(1, min(10, limit))

        scan_limit = 5000
        usage_boost = 1000

        best = []
        for i in range(start, min(start + scan_limit, len(words))):
            w = words[i]
            if not w.startswith(p):
                break

            base = freqs.get(w, 1) if freqs else 1
            score = (usage.get(w, 0) * usage_boost) + base
            best.append((score, w))

        best.sort(key=lambda t: (-t[0], t[1]))
        out = []
        for _, w in best[:limit]:
            out.append(display_map.get(w, w) if display_map else w)
        return out

    def send_key(self, data):
        if isinstance(data, str):
            logical = data.strip()
            modifiers = []
        elif isinstance(data, dict):
            logical = (data.get('key') or '').strip()
            modifiers = data.get('modifiers') or []
        else:
            return

        if not logical:
            return

        target_hwnd = _get_last_target_hwnd()
        osk_hwnd = _get_osk_hwnd()
        if target_hwnd and target_hwnd != osk_hwnd:
            _focus_window(target_hwnd)
            time.sleep(0.01)

        normalized_mods = []
        for m in modifiers:
            m = str(m).strip()
            if m in ('Shift', 'Control', 'Alt', 'Meta'):
                normalized_mods.append(m)

        if len(logical) == 1 and logical.isalpha():
            key = logical.upper()
        else:
            key = logical

        if key == '+':
            shift_vk = VK_CODES['Shift']
            equal_vk = VK_CODES['=']
            press_combo([shift_vk], equal_vk)
            return

        vk = VK_CODES.get(key)
        if vk is None:
            return

        mod_vks = [VK_CODES[m] for m in normalized_mods if m in VK_CODES]
        if mod_vks:
            press_combo(mod_vks, vk)
        else:
            press_vk(vk)

    def send_text(self, text):
        if not isinstance(text, str):
            return

        if not text:
            return

        target_hwnd = _get_last_target_hwnd()
        osk_hwnd = _get_osk_hwnd()
        if target_hwnd and target_hwnd != osk_hwnd:
            _focus_window(target_hwnd)
            time.sleep(0.03)

        for ch in text:
            if ch == '\n':
                press_vk(VK_CODES['Enter'])
                continue
            if ch == '\t':
                press_vk(VK_CODES['Tab'])
                continue

            if ch == ' ':
                press_vk(VK_CODES['Space'])
                continue

            # Prefer VK mapping for normal characters (this matches how send_key works).
            vk_scan = user32.VkKeyScanW(ch) if user32 else -1
            if vk_scan != -1:
                vk = vk_scan & 0xFF
                shift_state = (vk_scan >> 8) & 0xFF

                mods = []
                if shift_state & 0x01:
                    mods.append(VK_CODES['Shift'])
                if shift_state & 0x02:
                    mods.append(VK_CODES['Control'])
                if shift_state & 0x04:
                    mods.append(VK_CODES['Alt'])

                if mods:
                    press_combo(mods, vk)
                else:
                    press_vk(vk)
                continue

            # Fallback: Unicode injection (for characters that don't map to a VK on this layout).
            type_unicode(ch)


def resource_path(relative_path: str) -> str:
    if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
        base_dir = sys._MEIPASS
    else:
        base_dir = os.path.dirname(os.path.abspath(__file__))

    return os.path.join(base_dir, relative_path)


def main():
    if platform.system() != 'Windows':
        print('This prototype only supports Windows for now.')
        return

    html_file = resource_path('keyboard.html')
    if not os.path.exists(html_file):
        print('keyboard.html not found at:', html_file)
        return

    api = Api()

    # Seed the "last target" with whatever window was active before we created ours.
    _set_last_target_hwnd(_get_foreground_hwnd())

    webview.create_window(
        WINDOW_TITLE,
        html_file,
        js_api=api,
        width=800,
        height=600,
        resizable=True,
        on_top=True
    )

    webview.start(_on_webview_started)


if __name__ == '__main__':
    main()
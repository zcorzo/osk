import os
import sys
import platform
import ctypes
import ctypes.wintypes
import threading
import time
import json
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

WINDOW_TITLE = 'Hex Keyboard'
APP_NAME = 'HexKeyboard'
MACRO_COUNT = 7

_hwnd_lock = threading.Lock()
_last_target_hwnd: Optional[int] = None
_osk_hwnd: Optional[int] = None

_config_lock = threading.Lock()


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

    print('Saved macros to:', _config_path())
    return True


def _on_webview_started():
    # Identify our own window handle and start foreground tracking.
    hwnd = _find_window_by_title(WINDOW_TITLE)
    if hwnd is None:
        hwnd = _get_foreground_hwnd()
    _set_osk_hwnd(hwnd)

    t = threading.Thread(target=_track_last_active_window, daemon=True)
    t.start()


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

    def debug(self, message):
        print(f"JS: {message}")
        return True

    def get_macros(self):
        macros = load_macros()
        print('Loaded macros from:', _config_path())
        return macros

    def set_macros(self, macros):
        return save_macros(macros)

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

        print(f"send_key from JS: logical={logical!r}, modifiers={modifiers!r}")

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
            print(f"Unknown key: {logical!r}, no VK mapping yet")
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

        print(f"send_text from JS: text={text!r}")

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
import os
import sys
import platform
import ctypes
import ctypes.wintypes
import threading
import time

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
    'Shift': 0x10,
    'Control': 0x11,
    'Alt': 0x12,
    'CapsLock': 0x14,
    'Escape': 0x1B,

    ' ': 0x20,         # space by character
    'Space': 0x20,     # space by name
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

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

WINDOW_TITLE = 'Hex Keyboard'

_hwnd_lock = threading.Lock()
_last_target_hwnd: int | None = None
_osk_hwnd: int | None = None


def _set_last_target_hwnd(hwnd: int | None):
    global _last_target_hwnd
    with _hwnd_lock:
        _last_target_hwnd = hwnd


def _get_last_target_hwnd() -> int | None:
    with _hwnd_lock:
        return _last_target_hwnd


def _set_osk_hwnd(hwnd: int | None):
    global _osk_hwnd
    with _hwnd_lock:
        _osk_hwnd = hwnd


def _get_osk_hwnd() -> int | None:
    with _hwnd_lock:
        return _osk_hwnd


def _get_foreground_hwnd() -> int | None:
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


def _find_window_by_title(title: str, timeout_s: float = 5.0) -> int | None:
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


def _on_webview_started():
    # Identify our own window handle and start foreground tracking.
    _set_osk_hwnd(_find_window_by_title(WINDOW_TITLE))
    t = threading.Thread(target=_track_last_active_window, daemon=True)
    t.start()


def press_vk(vk_code: int):
    """Send a simple key press (down + up) to Windows."""
    user32.keybd_event(vk_code, 0, 0, 0)
    user32.keybd_event(vk_code, 0, KEYEVENTF_KEYUP, 0)


def press_combo(modifiers, vk: int):
    """
    Press one or more modifiers, then a key, then release in reverse order.
    modifiers: iterable of VK codes for modifiers (Shift, Control, Alt, Meta)
    """
    # Press modifiers down
    for mod_vk in modifiers:
        user32.keybd_event(mod_vk, 0, 0, 0)

    # Press the main key
    user32.keybd_event(vk, 0, 0, 0)
    user32.keybd_event(vk, 0, KEYEVENTF_KEYUP, 0)

    # Release modifiers
    for mod_vk in reversed(list(modifiers)):
        user32.keybd_event(mod_vk, 0, KEYEVENTF_KEYUP, 0)


class Api:
    """
    JS→Python bridge. Exposed to JavaScript as window.pywebview.api.
    """

    def send_key(self, data):
        """
        Called from JS:

        - Either as a plain string: "A"
        - Or as an object: { key: "A", modifiers: ["Shift", "Control"] }
        """
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

        # OSK-like behavior: type into the last active (non-keyboard) window.
        target_hwnd = _get_last_target_hwnd()
        osk_hwnd = _get_osk_hwnd()
        if target_hwnd and target_hwnd != osk_hwnd:
            _focus_window(target_hwnd)
            time.sleep(0.01)

        # OSK-like behavior: type into the last active
        # Normalize modifiers
        normalized_mods = []
        for m in modifiers:
            m = str(m).strip()
            if m in ('Shift', 'Control', 'Alt', 'Meta'):
                normalized_mods.append(m)

        # Normalize the main key
        if len(logical) == 1 and logical.isalpha():
            key = logical.upper()  # VK codes for A-Z
        else:
            key = logical

        # Special-case '+' to send Shift + '=' (VK for '=' is VK_OEM_PLUS)
        if key == '+':
            shift_vk = VK_CODES['Shift']
            equal_vk = VK_CODES['=']
            press_combo([shift_vk], equal_vk)
            return

        vk = VK_CODES.get(key)
        if vk is None:
            print(f"Unknown key: {logical!r}, no VK mapping yet")
            return

        # Convert modifier names to VK codes
        mod_vks = [VK_CODES[m] for m in normalized_mods if m in VK_CODES]

        if mod_vks:
            press_combo(mod_vks, vk)
        else:
            press_vk(vk)


def resource_path(relative_path: str) -> str:
    """
    Resolve path both in development and when bundled by PyInstaller.
    """
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
        on_top=True  # Always on top, but with normal window chrome
    )

    webview.start(_on_webview_started)


if __name__ == '__main__':
    main()
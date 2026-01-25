import os
import sys
import platform
import ctypes

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
    '\\': 0xDC,     # VK_OEM_5
    ';': 0xBA,      # VK_OEM_1
    "'": 0xDE,      # VK_OEM_7
    ',': 0xBC,      # VK_OEM_COMMA
    '.': 0xBE,      # VK_OEM_PERIOD
    '/': 0xBF,      # VK_OEM_2,

    # For convenience, treat '+' as the same physical key as '='
    '+': 0xBB,
}

KEYEVENTF_KEYUP = 0x0002

user32 = ctypes.windll.user32


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
        print("This prototype only supports Windows for now.")
        return

    html_file = resource_path('keyboard.html')
    if not os.path.exists(html_file):
        print("keyboard.html not found at:", html_file)
        return

    api = Api()

    window = webview.create_window(
        'Hex Keyboard',
        html_file,
        js_api=api,
        width=800,
        height=600,
        resizable=True,
        on_top=True  # Always on top, but with normal window chrome
    )

    webview.start()


if __name__ == '__main__':
    main()
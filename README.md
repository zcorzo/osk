# Hex Keyboard Prototype

This is a Windows 11 on-screen keyboard prototype.

- UI is defined in `keyboard.html` (SVG keyboard + autocomplete bar stub).
- The desktop host is `app.py` (Python + pywebview).

## Requirements

- Windows 11
- Python 3.10+ (64-bit recommended)
- Microsoft Edge WebView2 Runtime (usually already present on Windows 11)

## Run (development)

### Recommended: use a virtual environment (avoids dependency conflicts)

From the project folder:

```bash
py -m venv .venv
.\.venv\Scripts\activate
python -m pip install -U pip
pip install -r requirements.txt
python app.py
```

### If you are not using a virtual environment

If you see an `attrs` version conflict (e.g. `trio ... requires attrs>=23.2.0`), run:

```bash
python -m pip install -U attrs
pip install -r requirements.txt
```

You should see a window titled **Hex Keyboard** that is **Always on Top**.

## Quick tests

### 1) UI / click wiring test

- Click any key.
- In the terminal where you ran `python app.py`, you should see lines like:
  - `send_key from JS: logical='A', modifiers=['Shift']`

This confirms the HTML -> Python bridge is working.

### 2) OS keystroke test

- Open Notepad.
- Click into Notepad to place the caret.
- Click keys on the hex keyboard.

Note: the current prototype uses normal window activation; clicking the keyboard window may steal focus from the target app. If you see keys not reaching Notepad consistently, the next step is to implement OSK-like focus behavior (do not activate / return focus to prior window).

## Build an .exe

```bash
pip install pyinstaller
pyinstaller --onefile --noconsole --add-data "keyboard.html;." app.py
```

Output:
- `dist/app.exe`

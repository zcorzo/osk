# Hex Keyboard Prototype

This is a Windows 11 on-screen keyboard prototype.

- UI is defined in `keyboard.html` (SVG keyboard + autocomplete bar + macro key settings).
- The desktop host is `app.py` (Python + pywebview).

## Requirements

- Windows 11
- Python 3.8+ (64-bit recommended)
- Microsoft Edge WebView2 Runtime (usually already present on Windows 11)

## Run (development)

### Recommended: use a virtual environment (avoids dependency conflicts)

If you install into an existing Conda / AI environment, `pip` may print dependency conflict warnings about unrelated packages already installed (e.g. `mistral-common`, `trio`, `jsonschema`). A fresh venv avoids this.

From the project folder:

```bash
py -m venv .venv
.\.venv\Scripts\activate
python -m pip install -U pip
pip install -r requirements.txt
python app.py
```

### Alternative: use a fresh Conda env

```bash
conda create -n hexkbd python=3.11 -y
conda activate hexkbd
python -m pip install -U pip
pip install -r requirements.txt
python app.py
```

You should see a window titled **Hex Keyboard** that is **Always on Top**.

## Macro persistence

Macro assignments are stored in a simple JSON config file:
- `%APPDATA%\HexKeyboard\config.json`

If the file does not exist yet, it will be created when you first save macros.

## Quick tests

### 1) UI / click wiring test

- Click any key.
- The key should visually depress.

The app no longer prints per-key debug logs to the terminal; use Notepad (next test) to confirm keystrokes are emitted.

### 2) OS keystroke test (type into other apps)

- Open Notepad.
- Click into Notepad to place the caret.
- Click keys on the hex keyboard.

The prototype tracks the **last active (non-keyboard)** window and tries to refocus it before emitting each keystroke, so clicks on the keyboard should still type into Notepad.

Notes:
- This may not work when targeting **elevated/admin** windows unless the keyboard app is also elevated.
- If it fails in a specific app, test in Notepad first and report the result.

### 3) Autocomplete test

- In Notepad, type a few letters (e.g. `h`, `e`) using the hex keyboard.
- You should see matching suggestions in the bar above the keyboard.
- Press **ENT** to accept the highlighted suggestion (types the remaining letters + a trailing space).
- You can also click a suggestion to insert it.

### 4) Macro keys (left column)

- Click **Settings**.
- Assign a word/phrase to `M1`..`M7` and click **Save**.
- Click a macro key to type its text.

## Build an .exe

```bash
pip install pyinstaller
pyinstaller --onefile --noconsole --add-data "keyboard.html;." app.py
```

Output:
- `dist/app.exe`

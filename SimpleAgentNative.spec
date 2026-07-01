# PyInstaller spec for the native (PySide6/Qt) Simple Agent app.
# Build with:  uv run pyinstaller SimpleAgentNative.spec --noconfirm
#
#  - --onedir build: output is dist/SimpleAgentNative/ containing the exe + support files.
#  - console=True keeps a terminal open so you can READ errors during the first build.
#    Flip it to False once it runs cleanly for a proper windowless app.

# Files our code reads. config.py does AGENT_HOME = Path(__file__).parent, so these
# must sit next to the bundled config.py at runtime (dest "." = the bundle root).
datas = [
    ("tools_schema.json", "."),   # read by config.py at import time
    ("agent_config.json", "."),   # read by load_config()
    (".env", "."),                # your API key (don't share the dist folder)
    ("web", "web"),               # static assets for the QWebEngineView chat
]

# qt_app.py imports these normally, so PyInstaller should trace them automatically.
# Listed explicitly just as a safety net.
hiddenimports = [
    "config",
    "llm",
    "prompt",
    "tools",
    "agent",
    "agent_events",
    "safety",
    "ui",
    "sessions",
    "PySide6.QtWebEngineWidgets",
    "PySide6.QtWebChannel",        # often needed alongside QWebEngine
]

a = Analysis(
    ["qt_app.py"],
    pathex=["."],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="SimpleAgentNative",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,            # <-- keep True for the first build so you can read errors
    disable_windowed_traceback=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="SimpleAgentNative",
)

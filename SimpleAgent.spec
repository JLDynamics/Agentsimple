# PyInstaller spec for the Simple Agent desktop app.
# Build with:  uv run pyinstaller SimpleAgent.spec --noconfirm
#
# Notes:
#  - This is a --onedir build: output goes to dist/SimpleAgent/ as a folder
#    containing SimpleAgent.exe plus its support files. Easier to debug and
#    faster to start than a single-file build.
#  - console=True (below) keeps a terminal window open so you can READ errors
#    during the first builds. Once it runs cleanly, flip it to False for a
#    proper windowed app.

from PyInstaller.utils.hooks import collect_all, collect_submodules, copy_metadata

datas = []
binaries = []
hiddenimports = []

# --- Chainlit and its trickier dependencies: bundle everything (code + data) ---
for pkg in ["chainlit", "literalai", "socketio", "engineio", "uvicorn", "starlette", "fastapi"]:
    pkg_datas, pkg_binaries, pkg_hidden = collect_all(pkg)
    datas += pkg_datas
    binaries += pkg_binaries
    hiddenimports += pkg_hidden

# Some libraries read their own installed metadata at runtime.
for pkg in ["chainlit", "literalai"]:
    datas += copy_metadata(pkg)

# uvicorn loads its protocol/lifespan classes by string name (dynamic import).
hiddenimports += collect_submodules("uvicorn")

# Some deps (tomli, etc.) are compiled with mypyc and ship a top-level, hash-named
# shared module like "<hash>__mypyc.cp313-win_amd64.pyd" at the root of
# site-packages. collect_all misses these because they live outside the package
# folder. Find them and bundle each as a binary + hidden import.
import glob as _glob
import os as _os
import sysconfig as _sysconfig

_seen_mypyc = set()
for _root in {_sysconfig.get_paths()["purelib"], _sysconfig.get_paths()["platlib"]}:
    for _pyd in _glob.glob(_os.path.join(_root, "*__mypyc*.pyd")):
        _mod = _os.path.basename(_pyd).split(".")[0]   # e.g. 3c22db...__mypyc
        if _mod in _seen_mypyc:
            continue
        _seen_mypyc.add(_mod)
        binaries += [(_pyd, ".")]
        hiddenimports += [_mod]

# --- Our own data files. These must sit next to config.py at runtime,
#     because AGENT_HOME = Path(__file__).parent reads them from there. ---
datas += [
    ("chainlit_app.py", "."),     # Chainlit loads this BY FILE PATH, so it must be a real file
    ("tools_schema.json", "."),   # config.py reads this at import time
    ("agent_config.json", "."),
    ("chainlit.md", "."),
    (".env", "."),                # contains your API key (see warning in the chat)
    (".chainlit", ".chainlit"),   # Chainlit UI config + translations
]

# --- Our own modules. chainlit_app.py is loaded dynamically, so PyInstaller
#     can't discover these by tracing imports. Name them explicitly. ---
hiddenimports += [
    "chainlit_app",
    "agent",
    "agent_events",
    "config",
    "llm",
    "prompt",
    "sessions",
    "safety",
    "tools",
    "ui",
]

a = Analysis(
    ["desktop.py"],
    pathex=["."],
    binaries=binaries,
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
    name="SimpleAgent",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,            # <-- keep True for the first builds so you can read errors
    disable_windowed_traceback=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="SimpleAgent",
)

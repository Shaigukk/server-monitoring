# -*- mode: python ; coding: utf-8 -*-
"""Сборка одного exe для Windows. Запуск из папки monitoring:

.venv\\Scripts\\python -m PyInstaller --noconfirm --clean monitoring.spec
"""

a = Analysis(
    ["app.py"],
    pathex=[],
    binaries=[],
    datas=[
        ("index.html", "."),
        ("style.css", "."),
        ("app.js", "."),
        ("config.example.json", "."),
    ],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="monitoring",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
)

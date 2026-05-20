# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['C:\\Users\\User\\OneDrive\\Documents\\conf-comparison-tool\\server.py'],
    pathex=[],
    binaries=[],
    datas=[('C:\\Users\\User\\OneDrive\\Documents\\conf-comparison-tool\\config', 'config'), ('C:\\Users\\User\\OneDrive\\Documents\\conf-comparison-tool\\comparison_engine\\templates', 'comparison_engine\\templates'), ('C:\\Users\\User\\OneDrive\\Documents\\conf-comparison-tool\\schemes', 'schemes'), ('C:\\Users\\User\\OneDrive\\Documents\\conf-comparison-tool\\rubrics', 'rubrics')],
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
    [],
    exclude_binaries=True,
    name='conf-comparison-server',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='conf-comparison-server',
)

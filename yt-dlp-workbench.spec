# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_all

datas = [('web', 'web')]
binaries = [('C:/Users/yujiayi/.workbuddy/binaries/python/envs/default/Lib/site-packages/imageio_ffmpeg/binaries/ffmpeg-win-x86_64-v7.1.exe', 'bin')]
hiddenimports = ['onnxruntime']
tmp_ret = collect_all('onnxruntime')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]


a = Analysis(
    ['server.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # 只用到 numpy 和 onnxruntime。不加这个列表的话 site-packages 里的
    # pandas / pyarrow / scipy 会被 hook 顺手收进来，白白多出七十多 MB
    excludes=[
        'pandas', 'pyarrow', 'scipy', 'matplotlib', 'PIL', 'IPython',
        'jupyter', 'notebook', 'nbformat', 'tornado', 'sqlalchemy',
        'pytest', 'tkinter', 'lib2to3', 'pydoc_data',
    ],
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
    name='yt-dlp-workbench',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['icon.ico'],
)

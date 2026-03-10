# -*- mode: python -*-
# /Library/Frameworks/Python.framework/Versions/3.10/bin/pyinstaller  --onefile --clean --noconfirm Ortho4XP.spec

#/usr/local/bin/python3 -m PyInstaller --clean --noconfirm Ortho4XP.spec

block_cipher = None


a = Analysis(['../Ortho4XP.py'],
             pathex=['/Users/apple/code/Ortho4XP'],
             binaries=[],
             datas=[('../Utils/Earth.gif','.')],
             hiddenimports=[],
             runtime_hooks=[],
             excludes=[],
             win_no_prefer_redirects=False,
             win_private_assemblies=False,
             cipher=block_cipher)
pyz = PYZ(a.pure, a.zipped_data,
             cipher=block_cipher)
exe = EXE(pyz,
          a.scripts,
          a.binaries,
          a.zipfiles,
          a.datas,
          name='Ortho4XP',
          debug=False,
          strip=False,
          upx=True,
          runtime_tmpdir=None,
          console=True , icon='../Utils/Earth.gif')
app = BUNDLE(exe,
             a.datas, 
             name='Ortho4XP.app',
             icon='logo.icns')

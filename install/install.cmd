call env.cmd

rmdir /s /q dist


python.exe -m PyInstaller  --clean --noconfirm install\Ortho4XP.spec

pause


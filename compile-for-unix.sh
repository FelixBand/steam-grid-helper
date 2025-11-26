rm -rf build
rm -rf dist
pyinstaller --onefile --windowed --icon=icon.icns -n Bandit "bandit.py"
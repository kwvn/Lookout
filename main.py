# some hosts look for main.py
import runpy
from pathlib import Path

runpy.run_path(str(Path(__file__).parent / "bot.py"), run_name="__main__")

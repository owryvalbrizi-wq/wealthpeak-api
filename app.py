"""Bootstrap: load the full fixed application"""
from pathlib import Path
_code = Path(__file__).with_name("app_FIXED.py").read_text()
exec(compile(_code, "app_FIXED.py", "exec"), globals())

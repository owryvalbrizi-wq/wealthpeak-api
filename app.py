from pathlib import Path
_d = Path(__file__).parent
_code = (_d/"src_a.py").read_text() + (_d/"src_b.py").read_text()
exec(compile(_code, "app_real.py", "exec"), globals())

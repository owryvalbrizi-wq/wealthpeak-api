import base64, pathlib
_dir = pathlib.Path(__file__).parent
_parts = sorted(_dir.glob("app_part_*.b64"))
assert _parts, "missing app_part_*.b64 payload files"
_b64 = "".join(p.read_text().strip() for p in _parts)
_code = base64.b64decode(_b64.encode()).decode()
exec(compile(_code, "app_real.py", "exec"), globals())

"""Bootstrap: load full app and harden pending labels"""
from pathlib import Path
_code = Path(__file__).with_name("app_FIXED.py").read_text()
_code = _code.replace(
    'f"PawaPay deposit ({country})"',
    'f"PENDING unpaid — PawaPay ({country}) — not credited yet"',
)
_code = _code.replace(
    'f"Pending deposit via {provider_name}"',
    'f"PENDING unpaid — {provider_name}"',
)
exec(compile(_code, "app_FIXED.py", "exec"), globals())

"""Bootstrap: load full app + Pesapal KES conversion + clear errors"""
from pathlib import Path

_code = Path(__file__).with_name("app_FIXED.py").read_text()

# Pending labels
_code = _code.replace(
    'f"PawaPay deposit ({country})"',
    'f"PENDING unpaid — PawaPay ({country}) — not credited yet"',
)
_code = _code.replace(
    'f"Pending deposit via {provider_name}"',
    'f"PENDING unpaid — {provider_name}"',
)

# Force Pesapal to send KES for Kenya (merchant USD limit is very low)
_code = _code.replace(
    'currency="USD",          # Force USD for simplicity; change if needed',
    'currency=("KES" if country in ("KE", "KEN") else "USD"),',
)

# Convert USD amount to KES (~130) for Kenya before submit_order
_old = '''    result = submit_order(
        amount=amount,
        currency=("KES" if country in ("KE", "KEN") else "USD"),
        description=f"WealthPeak Deposit — User #{request.current_user['id']}",'''

_new = '''    pay_amount = amount
    pay_currency = "USD"
    if country in ("KE", "KEN"):
        pay_currency = "KES"
        pay_amount = round(amount * 130, 2)  # USD to KES approx

    result = submit_order(
        amount=pay_amount,
        currency=pay_currency,
        description=f"WealthPeak Deposit — User #{request.current_user['id']}",'''

if _old in _code:
    _code = _code.replace(_old, _new)

# Stringify object errors so frontend does not show [object Object]
_code = _code.replace(
    'if result.get("error"):
        return jsonify({"error": result["error"]}), 400',
    '''if result.get("error"):
        err = result["error"]
        if isinstance(err, dict):
            err = err.get("message") or err.get("code") or str(err)
        return jsonify({"error": err}), 400''',
)

exec(compile(_code, "app_FIXED.py", "exec"), globals())

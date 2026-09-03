"""Bootstrap: load full app with Pesapal fixes"""
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

# Replace USD force with KES conversion block
_code = _code.replace(
    '''    result = submit_order(
        amount=amount,
        currency="USD",          # Force USD for simplicity; change if needed
        description=f"WealthPeak Deposit — User #{request.current_user['id']}",
        email=email,
        phone=phone,
        first_name=first_name,
        country_code=country,
        merchant_reference=f"WP-{request.current_user['id']}-{datetime.datetime.now().strftime('%Y%m%d%H%M%S')}"
    )

    if result.get("error"):
        return jsonify({"error": result["error"]}), 400''',
    '''    # Pesapal merchant has a low default limit (~$5–9 USD).
    # For Kenya send KES; keep USD amount for our ledger.
    pay_amount = amount
    pay_currency = "USD"
    if str(country).upper() in ("KE", "KEN"):
        pay_currency = "KES"
        pay_amount = round(float(amount) * 130, 2)

    result = submit_order(
        amount=pay_amount,
        currency=pay_currency,
        description=f"WealthPeak Deposit — User #{request.current_user['id']}",
        email=email,
        phone=phone,
        first_name=first_name,
        country_code="KE" if str(country).upper() in ("KE", "KEN") else country,
        merchant_reference=f"WP-{request.current_user['id']}-{datetime.datetime.now().strftime('%Y%m%d%H%M%S')}"
    )

    if result.get("error"):
        err = result["error"]
        if isinstance(err, dict):
            err = err.get("message") or err.get("code") or str(err)
        return jsonify({"error": err}), 400'''
)

exec(compile(_code, "app_FIXED.py", "exec"), globals())

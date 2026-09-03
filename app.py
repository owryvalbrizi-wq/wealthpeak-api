"""Bootstrap: multi-country Pesapal (ZW, ZM, MW, KE)"""
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
    '''    # Map country → Pesapal ISO code + currency + local amount
    # User sends ZW/ZM/MW/KE (or KEN etc.)
    c = str(country or "KE").upper().strip()
    if c in ("KEN", "KENYA"): c = "KE"
    if c in ("ZWE", "ZIMBABWE"): c = "ZW"
    if c in ("ZMB", "ZAMBIA"): c = "ZM"
    if c in ("MWI", "MALAWI"): c = "MW"

    # Currency + approx USD conversion for local rails
    # Note: payment methods shown depend on YOUR Pesapal merchant country registration
    conf = {
        "KE": {"currency": "KES", "rate": 130.0, "iso": "KE"},
        "ZW": {"currency": "USD", "rate": 1.0, "iso": "ZW"},
        "ZM": {"currency": "ZMW", "rate": 27.0, "iso": "ZM"},
        "MW": {"currency": "MWK", "rate": 1730.0, "iso": "MW"},
    }.get(c, {"currency": "USD", "rate": 1.0, "iso": c[:2] if len(c) >= 2 else "KE"})

    pay_currency = conf["currency"]
    pay_amount = round(float(amount) * conf["rate"], 2) if conf["rate"] != 1.0 else float(amount)
    # Keep small amounts within typical merchant limits where possible

    result = submit_order(
        amount=pay_amount,
        currency=pay_currency,
        description=f"WealthPeak Deposit — User #{request.current_user['id']}",
        email=email,
        phone=phone,
        first_name=first_name,
        country_code=conf["iso"],
        merchant_reference=f"WP-{request.current_user['id']}-{datetime.datetime.now().strftime('%Y%m%d%H%M%S')}"
    )

    if result.get("error"):
        err = result["error"]
        if isinstance(err, dict):
            err = err.get("message") or err.get("code") or str(err)
        return jsonify({"error": err}), 400'''
)

exec(compile(_code, "app_FIXED.py", "exec"), globals())

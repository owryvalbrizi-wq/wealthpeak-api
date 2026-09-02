"""
PawaPay Merchant API helper (Sandbox + Live)
"""

import os
import uuid
import requests
from datetime import datetime, timezone

PAWAPAY_API_TOKEN = os.environ.get("PAWAPAY_API_TOKEN", "").strip()
# sandbox by default until you switch to live
PAWAPAY_SANDBOX = os.environ.get("PAWAPAY_SANDBOX", "true").lower() == "true"

if PAWAPAY_SANDBOX:
    BASE_URL = "https://api.sandbox.pawapay.io"
else:
    BASE_URL = "https://api.pawapay.io"

# Country + default provider mapping for your markets
PROVIDERS = {
    "KEN": {"currency": "KES", "correspondent": "MPESA_KEN", "name": "Kenya M-Pesa"},
    "ZMB": {"currency": "ZMW", "correspondent": "MTN_MOMO_ZMB", "name": "Zambia MTN"},
    "MWI": {"currency": "MWK", "correspondent": "AIRTEL_MWI", "name": "Malawi Airtel"},
    "ZWE": {"currency": "USD", "correspondent": "ECOCASH_ZWE", "name": "Zimbabwe EcoCash"},
}


def is_configured():
    return bool(PAWAPAY_API_TOKEN)


def _headers():
    return {
        "Authorization": f"Bearer {PAWAPAY_API_TOKEN}",
        "Content-Type": "application/json",
    }


def normalize_phone(phone: str, country: str) -> str:
    """Digits only, with country code, no leading + or 0."""
    digits = "".join(c for c in phone if c.isdigit())
    # strip leading zero if present after country code attempts
    prefixes = {
        "KEN": "254",
        "ZMB": "260",
        "MWI": "265",
        "ZWE": "263",
    }
    cc = prefixes.get(country, "")
    if digits.startswith("0") and cc:
        digits = cc + digits[1:]
    elif cc and not digits.startswith(cc):
        if len(digits) <= 10:
            digits = cc + digits.lstrip("0")
    return digits


def request_deposit(amount: float, phone: str, country: str = "KEN", description: str = "WealthPeak Deposit"):
    """
    Initiate a mobile money deposit via PawaPay.
    amount is in USD for our app — for sandbox we send as local currency units
    for simplicity (you can convert rates later).
    """
    if not is_configured():
        return {"error": "PawaPay API token not configured", "demo": True}

    meta = PROVIDERS.get(country, PROVIDERS["KEN"])
    deposit_id = str(uuid.uuid4())
    msisdn = normalize_phone(phone, country)

    # Statement description: 4–22 characters required by PawaPay
    stmt = (description or "WealthPeak")[:22]
    if len(stmt) < 4:
        stmt = "WealthPeak Pay"

    payload = {
        "depositId": deposit_id,
        "amount": f"{float(amount):.0f}",
        "currency": meta["currency"],
        "correspondent": meta["correspondent"],
        "payer": {
            "type": "MSISDN",
            "address": {"value": msisdn},
        },
        "customerTimestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "statementDescription": stmt,
        "country": country,
        "metadata": [
            {"fieldName": "platform", "fieldValue": "WealthPeak"},
        ],
    }

    try:
        res = requests.post(
            f"{BASE_URL}/deposits",
            json=payload,
            headers=_headers(),
            timeout=30,
        )
        data = res.json() if res.content else {}
        data["_http_status"] = res.status_code
        data["depositId"] = deposit_id
        data["msisdn"] = msisdn
        data["correspondent"] = meta["correspondent"]
        return data
    except Exception as e:
        return {"error": str(e), "depositId": deposit_id}


def check_deposit(deposit_id: str):
    if not is_configured():
        return {"error": "Not configured"}
    try:
        res = requests.get(
            f"{BASE_URL}/deposits/{deposit_id}",
            headers=_headers(),
            timeout=20,
        )
        return res.json() if res.content else {}
    except Exception as e:
        return {"error": str(e)}

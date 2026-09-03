"""
PawaPay Merchant API helper (Sandbox + Live)
"""

import os
import uuid
import requests
from datetime import datetime, timezone

PAWAPAY_API_TOKEN = os.environ.get("PAWAPAY_API_TOKEN", "").strip()
PAWAPAY_SANDBOX = os.environ.get("PAWAPAY_SANDBOX", "true").lower() == "true"

if PAWAPAY_SANDBOX:
    BASE_URL = "https://api.sandbox.pawapay.io"
else:
    BASE_URL = "https://api.pawapay.io"

USD_RATES = {
    "KEN": 130.0,
    "ZMB": 27.0,
    "MWI": 1730.0,
    "ZWE": 1.0,
}

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
    digits = "".join(c for c in phone if c.isdigit())
    prefixes = {"KEN": "254", "ZMB": "260", "MWI": "265", "ZWE": "263"}
    cc = prefixes.get(country, "")
    if digits.startswith("0") and cc:
        digits = cc + digits[1:]
    elif cc and not digits.startswith(cc):
        if len(digits) <= 10:
            digits = cc + digits.lstrip("0")
    return digits


def usd_to_local(amount_usd: float, country: str) -> str:
    rate = USD_RATES.get(country, 1.0)
    local = max(1, round(float(amount_usd) * rate))
    return str(local)


def request_deposit(amount_usd: float, phone: str, country: str = "KEN", description: str = "WealthPeak"):
    if not is_configured():
        return {"error": "PawaPay API token not configured", "demo": True}

    meta = PROVIDERS.get(country, PROVIDERS["KEN"])
    deposit_id = str(uuid.uuid4())
    msisdn = normalize_phone(phone, country)
    local_amount = usd_to_local(amount_usd, country)

    stmt = (description or "WealthPeak")[:22]
    if len(stmt) < 4:
        stmt = "WealthPeak Pay"

    payload = {
        "depositId": deposit_id,
        "amount": local_amount,
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
            {"fieldName": "usdAmount", "fieldValue": str(amount_usd)},
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
        print(f"[PawaPay] status={res.status_code} body={data}")
        data["_http_status"] = res.status_code
        data["depositId"] = data.get("depositId") or deposit_id
        data["msisdn"] = msisdn
        data["local_amount"] = local_amount
        data["currency"] = meta["currency"]
        data["correspondent"] = meta["correspondent"]
        if res.status_code >= 400:
            reason = (
                data.get("failureReason")
                or data.get("rejectionReason")
                or data.get("errorMessage")
                or data.get("message")
                or str(data)
            )
            data["error"] = reason
        return data
    except Exception as e:
        print(f"[PawaPay] exception: {e}")
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
        data = res.json() if res.content else {}
        print(f"[PawaPay] check {deposit_id}: {data}")
        return data
    except Exception as e:
        return {"error": str(e)}

"""
Pesapal API 3.0 Integration Helper
Ready for live keys — currently runs in DEMO mode if keys are not set.
"""

import os
import uuid
import requests
from datetime import datetime

# ==================== CONFIG ====================
# Your Pesapal merchant credentials
PESAPAL_CONSUMER_KEY = os.environ.get("PESAPAL_CONSUMER_KEY", "Y7jRFhGxUTR2HbOtWjvpDvCvFMHOYA2/")
PESAPAL_CONSUMER_SECRET = os.environ.get("PESAPAL_CONSUMER_SECRET", "8USK6RbGpFwBqzApyjfi2q3xkbM=")
PESAPAL_IPN_ID = os.environ.get("PESAPAL_IPN_ID", "")  # Register IPN once you have a public HTTPS URL

# Sandbox (testing) vs Live
# Your keys are LIVE keys → default is live mode (False)
PESAPAL_SANDBOX = os.environ.get("PESAPAL_SANDBOX", "false").lower() == "true"

if PESAPAL_SANDBOX:
    BASE_URL = "https://cybqa.pesapal.com/pesapalv3"
else:
    BASE_URL = "https://pay.pesapal.com/v3"

# Your website callback (change when you host the site)
CALLBACK_URL = os.environ.get("PESAPAL_CALLBACK_URL", "https://wealthpeak-investments.netlify.app/dashboard.html")


def is_configured():
    """Returns True if real keys are present"""
    return bool(PESAPAL_CONSUMER_KEY and PESAPAL_CONSUMER_SECRET)


def get_access_token():
    """Step 1: Get Bearer token from Pesapal (valid ~5 minutes)"""
    if not is_configured():
        return None

    key = (PESAPAL_CONSUMER_KEY or "").strip()
    secret = (PESAPAL_CONSUMER_SECRET or "").strip()
    url = f"{BASE_URL}/api/Auth/RequestToken"
    payload = {
        "consumer_key": key,
        "consumer_secret": secret
    }
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json"
    }

    try:
        res = requests.post(url, json=payload, headers=headers, timeout=20)
        data = res.json() if res.content else {}
        token = data.get("token")
        if not token:
            print(f"[Pesapal] Auth failed status={res.status_code} body={data}")
        return token
    except Exception as e:
        print(f"[Pesapal] Token error: {e}")
        return None


def register_ipn(ipn_url: str, notification_type: str = "GET"):
    """
    Register your IPN (callback) URL with Pesapal.
    Run this once after you have a public HTTPS URL.
    Returns notification_id which you save as PESAPAL_IPN_ID.
    """
    token = get_access_token()
    if not token:
        return {"error": "Could not get access token. Check your keys."}

    url = f"{BASE_URL}/api/URLSetup/RegisterIPN"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json"
    }
    payload = {
        "url": ipn_url,
        "ipn_notification_type": notification_type
    }

    try:
        res = requests.post(url, json=payload, headers=headers, timeout=15)
        return res.json()
    except Exception as e:
        return {"error": str(e)}


def submit_order(
    amount: float,
    currency: str = "USD",
    description: str = "WealthPeak Investment Deposit",
    email: str = "",
    phone: str = "",
    first_name: str = "Investor",
    last_name: str = "",
    country_code: str = "KE",
    merchant_reference: str = None,
    callback_url: str = None
):
    """
    Step 2: Create a payment order and get the redirect/iframe URL.
    Returns dict with redirect_url and order_tracking_id.
    """
    if not is_configured():
        # Demo mode — return fake response
        fake_id = str(uuid.uuid4())
        return {
            "demo": True,
            "order_tracking_id": fake_id,
            "merchant_reference": merchant_reference or f"WP-{datetime.now().strftime('%Y%m%d%H%M%S')}",
            "redirect_url": None,
            "message": "Pesapal keys not configured — running in DEMO mode"
        }

    token = get_access_token()
    if not token:
        return {"error": "Failed to authenticate with Pesapal"}

    if not merchant_reference:
        merchant_reference = f"WP-{datetime.now().strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:6]}"

    url = f"{BASE_URL}/api/Transactions/SubmitOrderRequest"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json"
    }

    payload = {
        "id": merchant_reference,
        "currency": currency,
        "amount": float(amount),
        "description": description,
        "callback_url": callback_url or CALLBACK_URL,
        "notification_id": PESAPAL_IPN_ID or "",
        "billing_address": {
            "email_address": email or "investor@wealthpeak.com",
            "phone_number": phone or "",
            "country_code": country_code,
            "first_name": first_name,
            "middle_name": "",
            "last_name": last_name or "User",
            "line_1": "",
            "line_2": "",
            "city": "",
            "state": "",
            "postal_code": "",
            "zip_code": ""
        }
    }

    try:
        res = requests.post(url, json=payload, headers=headers, timeout=20)
        data = res.json()
        data["merchant_reference"] = merchant_reference
        return data
    except Exception as e:
        return {"error": str(e)}


def get_transaction_status(order_tracking_id: str):
    """Check payment status by tracking ID"""
    if not is_configured():
        return {"demo": True, "payment_status_description": "COMPLETED"}

    token = get_access_token()
    if not token:
        return {"error": "Auth failed"}

    url = f"{BASE_URL}/api/Transactions/GetTransactionStatus?orderTrackingId={order_tracking_id}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json"
    }

    try:
        res = requests.get(url, headers=headers, timeout=15)
        return res.json()
    except Exception as e:
        return {"error": str(e)}

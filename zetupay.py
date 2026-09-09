"""ZetuPay Kenya M-Pesa — STK / hosted checkout via pay.zetupay.co.ke"""
import os
import uuid
import datetime
import requests
from flask import jsonify, request

ZETUPAY_SECRET_KEY = os.environ.get("ZETUPAY_SECRET_KEY", "").strip()
ZETUPAY_BASE = os.environ.get("ZETUPAY_BASE", "https://pay.zetupay.co.ke/api/v1").rstrip("/")
# USD → KES (approx). Override with ZETUPAY_USD_KES env if needed.
USD_KES = float(os.environ.get("ZETUPAY_USD_KES", "130"))
CALLBACK_REDIRECT = os.environ.get(
    "ZETUPAY_REDIRECT_URL",
    "https://wealthpeak-bill-023c.vercel.app/dashboard",
)


def is_configured():
    return bool(ZETUPAY_SECRET_KEY) and ZETUPAY_SECRET_KEY.startswith("sk_")


def _headers():
    return {
        "Authorization": f"Bearer {ZETUPAY_SECRET_KEY}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def normalize_phone(phone: str) -> str:
    digits = "".join(c for c in (phone or "") if c.isdigit())
    if digits.startswith("0") and len(digits) == 10:
        digits = "254" + digits[1:]
    elif digits.startswith("254") and len(digits) == 12:
        pass
    elif len(digits) == 9:
        digits = "254" + digits
    return digits


def usd_to_kes(amount_usd: float) -> int:
    kes = max(1, int(round(float(amount_usd) * USD_KES)))
    return kes


def initiate_payment(amount_usd: float, phone: str, reference: str = None, redirect_url: str = None):
    if not is_configured():
        return {"error": "ZetuPay secret key not configured. Set ZETUPAY_SECRET_KEY (sk_test_... or sk_live_...)", "demo": True}

    phone_n = normalize_phone(phone)
    if len(phone_n) < 12:
        return {"error": "Invalid Kenya phone number. Use 07XXXXXXXX or 2547XXXXXXXX"}

    amount_kes = usd_to_kes(amount_usd)
    ref = reference or f"WP-{datetime.datetime.utcnow().strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:6]}"

    payload = {
        "amount": amount_kes,
        "phoneNumber": phone_n,
        "reference": ref,
        "redirectUrl": redirect_url or CALLBACK_REDIRECT,
    }

    url = f"{ZETUPAY_BASE}/payment/initiate"
    try:
        res = requests.post(url, json=payload, headers=_headers(), timeout=30)
        data = res.json() if res.content else {}
        data["_http_status"] = res.status_code
        data["reference"] = ref
        data["amount_kes"] = amount_kes
        data["amount_usd"] = float(amount_usd)
        data["phone"] = phone_n

        if res.status_code >= 400:
            err = (
                data.get("message")
                or data.get("error")
                or data.get("detail")
                or str(data)
            )
            data["error"] = err
        return data
    except Exception as e:
        return {"error": str(e), "reference": ref}


def get_payment_status(payment_key: str):
    if not payment_key:
        return {"error": "paymentKey required"}
    url = f"{ZETUPAY_BASE}/payment/{payment_key}"
    try:
        # Docs: status endpoint may not require auth
        res = requests.get(url, timeout=20)
        data = res.json() if res.content else {}
        data["_http_status"] = res.status_code
        return data
    except Exception as e:
        return {"error": str(e)}


def register_zetupay_routes(app, get_db, token_required):
    @app.route("/api/zetupay/status", methods=["GET"])
    def zetupay_status():
        return jsonify({
            "configured": is_configured(),
            "sandbox": ZETUPAY_SECRET_KEY.startswith("sk_test") if ZETUPAY_SECRET_KEY else None,
            "base": ZETUPAY_BASE,
            "usd_kes": USD_KES,
            "message": "ZetuPay ready" if is_configured() else "Set ZETUPAY_SECRET_KEY on Render (sk_test_... or sk_live_...)",
        })

    @app.route("/api/zetupay/initiate", methods=["POST"])
    @token_required
    def zetupay_initiate():
        data = request.get_json() or {}
        amount = float(data.get("amount") or 0)
        phone = (data.get("phone") or data.get("phoneNumber") or "").strip()
        if amount < 5:
            return jsonify({"error": "Minimum deposit is $5"}), 400
        if not phone:
            return jsonify({"error": "Phone number required"}), 400

        user_id = request.current_user["id"]
        ref = f"ZP-{datetime.datetime.utcnow().strftime('%Y%m%d%H%M%S')}-{user_id}"

        result = initiate_payment(amount_usd=amount, phone=phone, reference=ref)
        if result.get("error") and result.get("demo"):
            return jsonify(result), 503
        if result.get("error"):
            return jsonify({"error": result["error"], "detail": result}), 400

        payment_key = (
            result.get("paymentKey")
            or result.get("payment_key")
            or result.get("id")
            or result.get("key")
            or ""
        )
        checkout_url = (
            result.get("checkoutUrl")
            or result.get("checkout_url")
            or result.get("url")
            or result.get("redirectUrl")
        )

        conn = get_db()
        cur = conn.cursor()
        cur.execute(
            """INSERT INTO transactions (user_id, type, amount, description, status, reference)
               VALUES (?, 'deposit', ?, ?, 'pending', ?)""",
            (
                user_id,
                amount,
                f"PENDING ZetuPay M-Pesa KES {result.get('amount_kes')} — ref {ref}",
                ref,
            ),
        )
        # Store payment key in description if needed for status poll
        if payment_key:
            cur.execute(
                "UPDATE transactions SET description = ? WHERE reference = ?",
                (
                    f"PENDING ZetuPay M-Pesa key={payment_key} KES {result.get('amount_kes')} — ref {ref}",
                    ref,
                ),
            )
        conn.commit()
        conn.close()

        return jsonify({
            "message": "STK / checkout initiated — complete payment on your phone",
            "reference": ref,
            "paymentKey": payment_key,
            "checkoutUrl": checkout_url,
            "amount_usd": amount,
            "amount_kes": result.get("amount_kes"),
            "phone": result.get("phone"),
            "status": "pending",
            "raw": {k: v for k, v in result.items() if not str(k).startswith("_")},
        })

    @app.route("/api/zetupay/check/<payment_key>", methods=["GET"])
    @token_required
    def zetupay_check(payment_key):
        data = get_payment_status(payment_key)
        status = (data.get("status") or data.get("payment_status") or "").lower()
        return jsonify({"paymentKey": payment_key, "status": status, "data": data})

    @app.route("/api/zetupay/webhook", methods=["POST", "GET"])
    def zetupay_webhook():
        # Verify secret header
        incoming = (
            request.headers.get("x-zetupay-secret")
            or request.headers.get("X-Zetupay-Secret")
            or ""
        )
        if is_configured() and incoming and incoming != ZETUPAY_SECRET_KEY:
            return jsonify({"error": "Invalid signature"}), 401

        body = request.get_json(silent=True) or {}
        if not body and request.args:
            body = dict(request.args)

        event = body.get("event") or body.get("type") or ""
        data = body.get("data") if isinstance(body.get("data"), dict) else body

        status = (data.get("status") or "").lower()
        reference = data.get("reference") or data.get("merchantReference") or ""
        wave_id = data.get("waveTransactionId") or data.get("wave_transaction_id") or data.get("transactionId") or ""
        amount_kes = float(data.get("net") or data.get("amount") or data.get("amount_kes") or 0)

        success = (
            event in ("payment.success", "payment_success", "success")
            or status in ("success", "completed", "paid")
        )

        if success and reference:
            conn = get_db()
            cur = conn.cursor()
            tx = cur.execute(
                "SELECT * FROM transactions WHERE reference = ?", (reference,)
            ).fetchone()
            if tx and tx["status"] == "pending":
                # Credit Main wallet (USD amount stored on the tx)
                usd = float(tx["amount"] or 0)
                cur.execute(
                    "UPDATE users SET balance = balance + ? WHERE id = ?",
                    (usd, tx["user_id"]),
                )
                cur.execute(
                    "UPDATE transactions SET status = 'completed', description = ? WHERE reference = ?",
                    (
                        f"ZetuPay M-Pesa deposit ${usd:.2f} (KES {amount_kes}) wave={wave_id}",
                        reference,
                    ),
                )
                conn.commit()
                print(f"[ZetuPay] Credited ${usd} to user {tx['user_id']} ref={reference}")
            elif tx and tx["status"] != "pending":
                print(f"[ZetuPay] Already processed {reference} status={tx['status']}")
            conn.close()

        return jsonify({"ok": True, "received": True})

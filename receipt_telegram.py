"""Receipt uploads, Telegram notify, approve/reject, withdraw after plan end."""
import base64
import datetime
import os
import re
import sqlite3

import requests
from flask import jsonify, request

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")


def _ensure_receipts_table(get_db):
    conn = get_db()
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS receipts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            amount REAL NOT NULL,
            image_b64 TEXT,
            filename TEXT,
            status TEXT DEFAULT 'pending',
            reference TEXT,
            telegram_msg_id TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            reviewed_at TEXT,
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
        """
    )
    conn.commit()
    conn.close()


def _plan_amounts(get_db):
    conn = get_db()
    rows = conn.execute(
        "SELECT min_amount FROM plans WHERE is_active = 1"
    ).fetchall()
    conn.close()
    return {float(r["min_amount"]) for r in rows}


def _telegram_send(text, photo_b64=None):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return None
    base = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"
    try:
        if photo_b64:
            raw = photo_b64
            if "," in raw:
                raw = raw.split(",", 1)[1]
            data = base64.b64decode(raw)
            files = {"photo": ("receipt.jpg", data)}
            r = requests.post(
                f"{base}/sendPhoto",
                data={"chat_id": TELEGRAM_CHAT_ID, "caption": text[:1000]},
                files=files,
                timeout=30,
            )
        else:
            r = requests.post(
                f"{base}/sendMessage",
                json={"chat_id": TELEGRAM_CHAT_ID, "text": text},
                timeout=20,
            )
        j = r.json()
        if j.get("ok"):
            return str(j["result"].get("message_id", ""))
    except Exception as e:
        print("telegram error", e)
    return None


def register_receipt_routes(app, get_db, token_required):
    _ensure_receipts_table(get_db)

    @app.route("/api/receipt/upload", methods=["POST"])
    @token_required
    def receipt_upload():
        data = request.get_json() or {}
        amount = float(data.get("amount") or 0)
        image = data.get("image") or ""
        filename = (data.get("filename") or "receipt.jpg")[:120]

        if amount < 50:
            return jsonify({"error": "Minimum amount is $50"}), 400
        if not image or not str(image).startswith("data:image"):
            return jsonify({"error": "Upload a valid image receipt"}), 400

        # Silent checks: amount should match a plan; recent-looking submission only accepted for review queue
        plans = _plan_amounts(get_db)
        matched = any(abs(amount - p) < 0.01 for p in plans)
        # Still accept into pending queue; auto-flag mismatch for admin
        flag = "" if matched else " [AMOUNT not matching a plan]"

        user_id = request.current_user["id"]
        email = request.current_user.get("email", "")
        ref = f"RC-{datetime.datetime.now().strftime('%Y%m%d%H%M%S')}-{user_id}"

        conn = get_db()
        cur = conn.cursor()
        cur.execute(
            """INSERT INTO receipts (user_id, amount, image_b64, filename, status, reference)
               VALUES (?, ?, ?, ?, 'pending', ?)""",
            (user_id, amount, image[:2_500_000], filename, ref),
        )
        rid = cur.lastrowid
        cur.execute(
            """INSERT INTO transactions (user_id, type, amount, description, status, reference)
               VALUES (?, 'deposit', ?, ?, 'pending', ?)""",
            (user_id, amount, f"PENDING receipt upload — awaiting review", ref),
        )
        conn.commit()
        conn.close()

        caption = (
            f"WealthPeak receipt #{rid}\n"
            f"User: {email} (id {user_id})\n"
            f"Amount: ${amount:.2f}{flag}\n"
            f"Ref: {ref}\n"
            f"Reply: APPROVE {rid}   or   REJECT {rid}"
        )
        msg_id = _telegram_send(caption, photo_b64=image)
        if msg_id:
            conn = get_db()
            conn.execute(
                "UPDATE receipts SET telegram_msg_id = ? WHERE id = ?",
                (msg_id, rid),
            )
            conn.commit()
            conn.close()

        return jsonify(
            {
                "message": "Receipt submitted for review",
                "reference": ref,
                "status": "pending",
                "id": rid,
            }
        )

    @app.route("/api/telegram/webhook", methods=["POST"])
    def telegram_webhook():
        update = request.get_json() or {}
        msg = update.get("message") or update.get("edited_message") or {}
        text = (msg.get("text") or "").strip()
        chat = str((msg.get("chat") or {}).get("id", ""))
        if TELEGRAM_CHAT_ID and chat and chat != str(TELEGRAM_CHAT_ID):
            return jsonify({"ok": True})

        m = re.match(r"^(APPROVE|REJECT)\s+(\d+)$", text, re.I)
        if not m:
            return jsonify({"ok": True})

        action, rid = m.group(1).upper(), int(m.group(2))
        conn = get_db()
        cur = conn.cursor()
        row = cur.execute(
            "SELECT * FROM receipts WHERE id = ?", (rid,)
        ).fetchone()
        if not row:
            _telegram_send(f"Receipt #{rid} not found")
            conn.close()
            return jsonify({"ok": True})
        if row["status"] != "pending":
            _telegram_send(f"Receipt #{rid} already {row['status']}")
            conn.close()
            return jsonify({"ok": True})

        now = datetime.datetime.utcnow().isoformat()
        if action == "APPROVE":
            # Credit balance
            cur.execute(
                "UPDATE users SET balance = balance + ? WHERE id = ?",
                (row["amount"], row["user_id"]),
            )
            cur.execute(
                "UPDATE receipts SET status = 'approved', reviewed_at = ? WHERE id = ?",
                (now, rid),
            )
            cur.execute(
                "UPDATE transactions SET status = 'completed', description = ? WHERE reference = ?",
                (f"Deposit via receipt approved ${row['amount']}", row["reference"]),
            )
            conn.commit()
            _telegram_send(f"Approved #{rid} — ${row['amount']:.2f} credited to user {row['user_id']}")
        else:
            cur.execute(
                "UPDATE receipts SET status = 'rejected', reviewed_at = ? WHERE id = ?",
                (now, rid),
            )
            cur.execute(
                "UPDATE transactions SET status = 'failed', description = ? WHERE reference = ?",
                ("Receipt rejected", row["reference"]),
            )
            conn.commit()
            _telegram_send(f"Rejected #{rid}")

        conn.close()
        return jsonify({"ok": True})

    # Also allow simple poll endpoint for manual approve via API if needed
    @app.route("/api/receipt/<int:rid>/decide", methods=["POST"])
    def receipt_decide(rid):
        data = request.get_json() or {}
        secret = data.get("secret") or request.headers.get("X-Admin-Secret")
        if secret != os.environ.get("ADMIN_SECRET", "wealthpeak-admin-2026"):
            return jsonify({"error": "Unauthorized"}), 401
        action = (data.get("action") or "").upper()
        # reuse webhook logic via synthetic
        with app.test_request_context(
            "/api/telegram/webhook",
            method="POST",
            json={
                "message": {
                    "chat": {"id": TELEGRAM_CHAT_ID or "0"},
                    "text": f"{action} {rid}",
                }
            },
        ):
            return telegram_webhook()


def patch_withdraw(app, get_db, token_required):
    """Replace withdraw: block while user has active (not completed) investments."""

    @app.route("/api/withdraw", methods=["POST"])
    @token_required
    def withdraw():
        data = request.get_json() or {}
        amount = float(data.get("amount", 0))
        if amount < 5:
            return jsonify({"error": "Minimum withdrawal is $5"}), 400

        user_id = request.current_user["id"]
        conn = get_db()
        cursor = conn.cursor()

        # Block if any investment still active (end_date in future)
        active = cursor.execute(
            """
            SELECT COUNT(*) AS c FROM investments
            WHERE user_id = ? AND status = 'active'
              AND date(end_date) > date('now')
            """,
            (user_id,),
        ).fetchone()["c"]
        if active and active > 0:
            conn.close()
            return jsonify({"error": "Withdrawal is not available yet"}), 400

        user = cursor.execute(
            "SELECT balance FROM users WHERE id = ?", (user_id,)
        ).fetchone()
        if user["balance"] < amount:
            conn.close()
            return jsonify({"error": "Insufficient balance"}), 400

        cursor.execute(
            "UPDATE users SET balance = balance - ? WHERE id = ?",
            (amount, user_id),
        )
        ref = f"WD-{datetime.datetime.now().strftime('%Y%m%d%H%M%S')}"
        cursor.execute(
            """INSERT INTO transactions (user_id, type, amount, description, status, reference)
               VALUES (?, 'withdraw', ?, ?, 'completed', ?)""",
            (user_id, amount, f"Withdrawal of ${amount}", ref),
        )
        conn.commit()
        new_balance = cursor.execute(
            "SELECT balance FROM users WHERE id = ?", (user_id,)
        ).fetchone()["balance"]
        conn.close()
        return jsonify(
            {
                "message": f"Withdrawal of ${amount} processed",
                "new_balance": round(new_balance, 2),
                "reference": ref,
            }
        )

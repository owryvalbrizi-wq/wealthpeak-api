"""Receipt uploads, Telegram notify with Approve/Reject buttons."""
import base64
import json
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


def _approve_keyboard(rid):
    return {
        "inline_keyboard": [
            [
                {"text": "\u2705 Approve", "callback_data": f"APPROVE:{rid}"},
                {"text": "\u274c Reject", "callback_data": f"REJECT:{rid}"},
            ]
        ]
    }


def _telegram_send(text, photo_b64=None, reply_markup=None):
    token = os.environ.get("TELEGRAM_BOT_TOKEN") or TELEGRAM_BOT_TOKEN
    chat = os.environ.get("TELEGRAM_CHAT_ID") or TELEGRAM_CHAT_ID
    if not token or not chat:
        print("telegram missing token/chat")
        return None
    base = f"https://api.telegram.org/bot{token}"
    try:
        if photo_b64:
            raw = photo_b64
            if "," in raw:
                raw = raw.split(",", 1)[1]
            data = base64.b64decode(raw)
            files = {"photo": ("receipt.jpg", data)}
            form = {"chat_id": chat, "caption": text[:1000]}
            if reply_markup:
                form["reply_markup"] = json.dumps(reply_markup)
            r = requests.post(
                f"{base}/sendPhoto",
                data=form,
                files=files,
                timeout=30,
            )
            j = r.json()
            if j.get("ok"):
                return str(j["result"].get("message_id", ""))
            print("telegram photo failed", j, "— falling back to text")
        payload = {"chat_id": chat, "text": text[:4000]}
        if reply_markup:
            payload["reply_markup"] = reply_markup
        r = requests.post(
            f"{base}/sendMessage",
            json=payload,
            timeout=20,
        )
        j = r.json()
        if j.get("ok"):
            return str(j["result"].get("message_id", ""))
        print("telegram api", j)
    except Exception as e:
        print("telegram error", e)
    return None


def _telegram_answer_callback(callback_query_id, text):
    token = os.environ.get("TELEGRAM_BOT_TOKEN") or TELEGRAM_BOT_TOKEN
    if not token:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{token}/answerCallbackQuery",
            json={"callback_query_id": callback_query_id, "text": text, "show_alert": False},
            timeout=15,
        )
    except Exception as e:
        print("telegram callback answer error", e)


def _telegram_edit_caption(chat_id, message_id, caption):
    token = os.environ.get("TELEGRAM_BOT_TOKEN") or TELEGRAM_BOT_TOKEN
    if not token:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{token}/editMessageCaption",
            json={
                "chat_id": chat_id,
                "message_id": message_id,
                "caption": caption[:1000],
                "reply_markup": {"inline_keyboard": []},
            },
            timeout=15,
        )
    except Exception:
        try:
            requests.post(
                f"https://api.telegram.org/bot{token}/editMessageText",
                json={
                    "chat_id": chat_id,
                    "message_id": message_id,
                    "text": caption[:1000],
                    "reply_markup": {"inline_keyboard": []},
                },
                timeout=15,
            )
        except Exception as e:
            print("telegram edit error", e)


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

        plans = _plan_amounts(get_db)
        matched = any(abs(amount - p) < 0.01 for p in plans)
        flag = "" if matched else " [amount not matching a plan]"

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
            f"Tap a button below:"
        )
        msg_id = _telegram_send(caption, photo_b64=image, reply_markup=_approve_keyboard(rid))
        if not msg_id:
            # Force a second text attempt without photo
            msg_id = _telegram_send(caption, photo_b64=None, reply_markup=_approve_keyboard(rid))
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
                "telegram": bool(msg_id),
            }
        )

    def _process_receipt_decision(action, rid, callback_query=None):
        conn = get_db()
        cur = conn.cursor()
        row = cur.execute(
            "SELECT * FROM receipts WHERE id = ?", (rid,)
        ).fetchone()
        if not row:
            if callback_query:
                _telegram_answer_callback(callback_query.get("id"), f"#{rid} not found")
            else:
                _telegram_send(f"Receipt #{rid} not found")
            conn.close()
            return
        if row["status"] != "pending":
            if callback_query:
                _telegram_answer_callback(callback_query.get("id"), f"Already {row['status']}")
            else:
                _telegram_send(f"Receipt #{rid} already {row['status']}")
            conn.close()
            return

        now = datetime.datetime.utcnow().isoformat()
        if action == "APPROVE":
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
            result_text = f"\u2705 Approved #{rid} — ${row['amount']:.2f} credited"
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
            result_text = f"\u274c Rejected #{rid}"

        conn.close()

        if callback_query:
            _telegram_answer_callback(callback_query.get("id"), result_text[:200])
            msg = callback_query.get("message") or {}
            chat_id = (msg.get("chat") or {}).get("id")
            message_id = msg.get("message_id")
            if chat_id and message_id:
                old_cap = msg.get("caption") or msg.get("text") or ""
                _telegram_edit_caption(chat_id, message_id, f"{old_cap}\n\n{result_text}")
        else:
            _telegram_send(result_text)

    @app.route("/api/telegram/webhook", methods=["POST"])
    def telegram_webhook():
        update = request.get_json() or {}

        cq = update.get("callback_query")
        if cq:
            data = (cq.get("data") or "").strip()
            chat = str(((cq.get("message") or {}).get("chat") or {}).get("id", ""))
            expected = str(os.environ.get("TELEGRAM_CHAT_ID") or TELEGRAM_CHAT_ID or "")
            if expected and chat and chat != expected:
                return jsonify({"ok": True})
            m = re.match(r"^(APPROVE|REJECT):(\d+)$", data, re.I)
            if m:
                action, rid = m.group(1).upper(), int(m.group(2))
                _process_receipt_decision(action, rid, callback_query=cq)
            return jsonify({"ok": True})

        msg = update.get("message") or update.get("edited_message") or {}
        text = (msg.get("text") or "").strip()
        chat = str((msg.get("chat") or {}).get("id", ""))
        expected = str(os.environ.get("TELEGRAM_CHAT_ID") or TELEGRAM_CHAT_ID or "")
        if expected and chat and chat != expected:
            return jsonify({"ok": True})

        m = re.match(r"^(APPROVE|REJECT)\s+(\d+)$", text, re.I)
        if not m:
            return jsonify({"ok": True})

        action, rid = m.group(1).upper(), int(m.group(2))
        _process_receipt_decision(action, rid, callback_query=None)
        return jsonify({"ok": True})

    @app.route("/api/receipt/<int:rid>/decide", methods=["POST"])
    def receipt_decide(rid):
        data = request.get_json() or {}
        secret = data.get("secret") or request.headers.get("X-Admin-Secret")
        if secret != os.environ.get("ADMIN_SECRET", "wealthpeak-admin-2026"):
            return jsonify({"error": "Unauthorized"}), 401
        action = (data.get("action") or "").upper()
        with app.test_request_context(
            "/api/telegram/webhook",
            method="POST",
            json={
                "message": {
                    "chat": {"id": os.environ.get("TELEGRAM_CHAT_ID") or TELEGRAM_CHAT_ID or "0"},
                    "text": f"{action} {rid}",
                }
            },
        ):
            return telegram_webhook()


def patch_withdraw(app, get_db, token_required):
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

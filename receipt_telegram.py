"""
WealthPeak — Receipt uploads + Telegram Approve/Reject bot
Stable version with retries, fast webhook responses, and robust error handling.
"""

import base64
import json
import datetime
import os
import re
import time

import requests
from flask import jsonify, request

# ---------------------------------------------------------------------------
# Config (re-read from env on every use so late-set env vars still work)
# ---------------------------------------------------------------------------

def _token():
    return (os.environ.get("TELEGRAM_BOT_TOKEN") or "").strip()

def _chat_id():
    return (os.environ.get("TELEGRAM_CHAT_ID") or "").strip()

def _admin_secret():
    return os.environ.get("ADMIN_SECRET", "wealthpeak-admin-2026")

# ---------------------------------------------------------------------------
# Robust Telegram HTTP helper with retries
# ---------------------------------------------------------------------------

def _tg_request(method, payload=None, files=None, timeout=25, max_retries=3):
    """Call Telegram Bot API with retries and exponential backoff."""
    token = _token()
    if not token:
        print("[tg] missing TELEGRAM_BOT_TOKEN")
        return None

    url = f"https://api.telegram.org/bot{token}/{method}"
    last_err = None

    for attempt in range(1, max_retries + 1):
        try:
            if files:
                r = requests.post(url, data=payload or {}, files=files, timeout=timeout)
            else:
                r = requests.post(url, json=payload or {}, timeout=timeout)

            try:
                data = r.json()
            except Exception:
                data = {"ok": False, "description": r.text[:300], "status_code": r.status_code}

            if data.get("ok"):
                return data

            # Retry on temporary Telegram / network errors
            desc = (data.get("description") or "").lower()
            if r.status_code in (429, 500, 502, 503, 504) or "retry" in desc or "too many" in desc:
                wait = min(2 ** attempt, 8)
                print(f"[tg] {method} attempt {attempt} failed ({r.status_code}): {data.get('description')} — retry in {wait}s")
                time.sleep(wait)
                last_err = data
                continue

            # Permanent error — don't retry
            print(f"[tg] {method} failed: {data}")
            return data

        except requests.exceptions.Timeout as e:
            print(f"[tg] {method} timeout attempt {attempt}: {e}")
            last_err = str(e)
            time.sleep(min(2 ** attempt, 6))
        except Exception as e:
            print(f"[tg] {method} error attempt {attempt}: {e}")
            last_err = str(e)
            time.sleep(min(1.5 ** attempt, 5))

    print(f"[tg] {method} gave up after {max_retries} attempts: {last_err}")
    return None


def _telegram_send(text, photo_b64=None, reply_markup=None):
    """Send message (optionally with photo) to the admin chat. Returns message_id or None."""
    chat = _chat_id()
    if not chat:
        print("[tg] missing TELEGRAM_CHAT_ID")
        return None

    text = (text or "")[:4000]
    markup = reply_markup

    # Try photo first if provided
    if photo_b64:
        try:
            raw = photo_b64
            if "," in raw:
                raw = raw.split(",", 1)[1]
            data = base64.b64decode(raw)
            # Telegram limit ~10 MB; keep under 5 MB for reliability on free hosts
            if len(data) > 4_500_000:
                print("[tg] photo too large, falling back to text")
            else:
                files = {"photo": ("receipt.jpg", data, "image/jpeg")}
                form = {
                    "chat_id": chat,
                    "caption": text[:1024],
                }
                if markup:
                    form["reply_markup"] = json.dumps(markup)
                result = _tg_request("sendPhoto", payload=form, files=files, timeout=35)
                if result and result.get("ok"):
                    return str(result["result"].get("message_id", ""))
                print("[tg] sendPhoto failed — falling back to text")
        except Exception as e:
            print("[tg] photo decode/send error:", e)

    # Text (primary or fallback)
    payload = {
        "chat_id": chat,
        "text": text,
        "disable_web_page_preview": True,
    }
    if markup:
        payload["reply_markup"] = markup

    result = _tg_request("sendMessage", payload=payload, timeout=20)
    if result and result.get("ok"):
        return str(result["result"].get("message_id", ""))
    return None


def _telegram_answer_callback(callback_query_id, text):
    if not callback_query_id:
        return
    _tg_request(
        "answerCallbackQuery",
        payload={
            "callback_query_id": callback_query_id,
            "text": (text or "")[:200],
            "show_alert": False,
        },
        timeout=12,
        max_retries=2,
    )


def _telegram_edit_message(chat_id, message_id, new_text, is_caption=False):
    if not chat_id or not message_id:
        return
    empty_kb = {"inline_keyboard": []}
    method = "editMessageCaption" if is_caption else "editMessageText"
    payload = {
        "chat_id": chat_id,
        "message_id": int(message_id),
        "reply_markup": empty_kb,
    }
    if is_caption:
        payload["caption"] = (new_text or "")[:1024]
    else:
        payload["text"] = (new_text or "")[:4000]

    result = _tg_request(method, payload=payload, timeout=15, max_retries=2)
    if not result or not result.get("ok"):
        # Try the other method as fallback
        alt = "editMessageText" if is_caption else "editMessageCaption"
        alt_payload = {
            "chat_id": chat_id,
            "message_id": int(message_id),
            "reply_markup": empty_kb,
        }
        if alt == "editMessageCaption":
            alt_payload["caption"] = (new_text or "")[:1024]
        else:
            alt_payload["text"] = (new_text or "")[:4000]
        _tg_request(alt, payload=alt_payload, timeout=12, max_retries=1)


# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------

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
            plan_type TEXT DEFAULT 'investment',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            reviewed_at TEXT,
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
        """
    )
    try:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(receipts)").fetchall()]
        if "plan_type" not in cols:
            conn.execute("ALTER TABLE receipts ADD COLUMN plan_type TEXT DEFAULT 'investment'")
        if "telegram_msg_id" not in cols:
            conn.execute("ALTER TABLE receipts ADD COLUMN telegram_msg_id TEXT")
    except Exception as e:
        print("[tg] receipts schema:", e)
    conn.commit()
    conn.close()


def _plan_amounts(get_db):
    try:
        conn = get_db()
        rows = conn.execute("SELECT min_amount FROM plans WHERE is_active = 1").fetchall()
        conn.close()
        amounts = {float(r["min_amount"]) for r in rows}
    except Exception:
        amounts = set()
    amounts.update({10.0, 20.0, 30.0, 50.0, 100.0, 200.0, 500.0, 1000.0, 2500.0, 5000.0})
    return amounts


def _approve_keyboard(rid):
    return {
        "inline_keyboard": [
            [
                {"text": "✅ Approve", "callback_data": f"APPROVE:{rid}"},
                {"text": "❌ Reject", "callback_data": f"REJECT:{rid}"},
            ]
        ]
    }


# ---------------------------------------------------------------------------
# Core decision logic
# ---------------------------------------------------------------------------

def _process_receipt_decision(get_db, action, rid, callback_query=None):
    conn = get_db()
    cur = conn.cursor()
    row = cur.execute("SELECT * FROM receipts WHERE id = ?", (rid,)).fetchone()

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
    plan_type = "investment"
    try:
        plan_type = (row["plan_type"] or "investment").strip().lower()
    except Exception:
        pass

    if action == "APPROVE":
        if plan_type == "automation":
            try:
                cur.execute(
                    "UPDATE users SET automation_balance = COALESCE(automation_balance, 0) + ? WHERE id = ?",
                    (row["amount"], row["user_id"]),
                )
            except Exception:
                cur.execute("UPDATE users SET balance = balance + ? WHERE id = ?", (row["amount"], row["user_id"]))
            credit_msg = f"Automation wallet +${row['amount']:.2f}"
        else:
            cur.execute("UPDATE users SET balance = balance + ? WHERE id = ?", (row["amount"], row["user_id"]))
            credit_msg = f"Main wallet +${row['amount']:.2f}"

        cur.execute(
            "UPDATE receipts SET status = 'approved', reviewed_at = ? WHERE id = ?",
            (now, rid),
        )
        cur.execute(
            "UPDATE transactions SET status = 'completed', description = ? WHERE reference = ?",
            (f"Deposit via receipt approved ${row['amount']:.2f} ({credit_msg})", row["reference"]),
        )
        result_text = f"✅ Approved #{rid} — ${row['amount']:.2f} · {credit_msg}"
    else:
        cur.execute(
            "UPDATE receipts SET status = 'rejected', reviewed_at = ? WHERE id = ?",
            (now, rid),
        )
        cur.execute(
            "UPDATE transactions SET status = 'failed', description = ? WHERE reference = ?",
            ("Receipt rejected", row["reference"]),
        )
        result_text = f"❌ Rejected #{rid}"

    conn.commit()
    conn.close()

    # Notify Telegram
    if callback_query:
        _telegram_answer_callback(callback_query.get("id"), result_text[:180])
        msg = callback_query.get("message") or {}
        chat_id = (msg.get("chat") or {}).get("id")
        message_id = msg.get("message_id")
        is_caption = bool(msg.get("caption"))
        old = msg.get("caption") or msg.get("text") or ""
        _telegram_edit_message(chat_id, message_id, f"{old}\n\n{result_text}", is_caption=is_caption)
    else:
        _telegram_send(result_text)


# ---------------------------------------------------------------------------
# Route registration
# ---------------------------------------------------------------------------

def register_receipt_routes(app, get_db, token_required):
    """Call this once after creating the Flask app."""
    _ensure_receipts_table(get_db)

    @app.route("/api/receipt/upload", methods=["POST"])
    @token_required
    def receipt_upload():
        data = request.get_json(silent=True) or {}
        try:
            amount = float(data.get("amount") or 0)
        except (TypeError, ValueError):
            return jsonify({"error": "Invalid amount"}), 400

        image = data.get("image") or ""
        filename = (data.get("filename") or "receipt.jpg")[:120]
        plan_type = (data.get("plan_type") or "investment").strip().lower()
        if plan_type not in ("investment", "automation"):
            plan_type = "investment"

        min_amt = 100 if plan_type == "automation" else 10
        if amount < min_amt:
            return jsonify({"error": f"Minimum amount is ${min_amt}"}), 400
        if not image or not str(image).startswith("data:image"):
            return jsonify({"error": "Upload a valid image receipt (data URL)"}), 400

        plans = _plan_amounts(get_db)
        matched = any(abs(amount - p) < 0.05 for p in plans)
        flag = "" if matched else " ⚠️ amount not matching a plan"

        user_id = request.current_user["id"]
        email = request.current_user.get("email", "")
        prefix = "AU" if plan_type == "automation" else "RC"
        ref = f"{prefix}-{datetime.datetime.now().strftime('%Y%m%d%H%M%S')}-{user_id}"

        conn = get_db()
        cur = conn.cursor()
        try:
            cur.execute(
                """INSERT INTO receipts
                   (user_id, amount, image_b64, filename, status, reference, plan_type)
                   VALUES (?, ?, ?, ?, 'pending', ?, ?)""",
                (user_id, amount, image[:2_000_000], filename, ref, plan_type),
            )
        except Exception as e:
            print("[tg] insert receipts error:", e)
            cur.execute(
                """INSERT INTO receipts
                   (user_id, amount, image_b64, filename, status, reference)
                   VALUES (?, ?, ?, ?, 'pending', ?)""",
                (user_id, amount, image[:2_000_000], filename, ref),
            )
        rid = cur.lastrowid

        label = "AUTOMATION binary plan" if plan_type == "automation" else "investment plan"
        cur.execute(
            """INSERT INTO transactions
               (user_id, type, amount, description, status, reference)
               VALUES (?, 'deposit', ?, ?, 'pending', ?)""",
            (user_id, amount, f"PENDING {label} receipt — awaiting review", ref),
        )
        conn.commit()
        conn.close()

        if plan_type == "automation":
            caption = (
                f"🤖 AUTOMATION receipt #{rid}\n"
                f"User: {email} (id {user_id})\n"
                f"Amount: ${amount:.2f}{flag}\n"
                f"Ref: {ref}\n"
                f"Plans: $100 / $500 / $1000 · max 2/day\n"
                f"Tap a button below:"
            )
        else:
            caption = (
                f"💰 WealthPeak receipt #{rid}\n"
                f"User: {email} (id {user_id})\n"
                f"Amount: ${amount:.2f}{flag}\n"
                f"Ref: {ref}\n"
                f"Tap a button below:"
            )

        msg_id = _telegram_send(caption, photo_b64=image, reply_markup=_approve_keyboard(rid))
        if not msg_id:
            msg_id = _telegram_send(caption, photo_b64=None, reply_markup=_approve_keyboard(rid))

        if msg_id:
            try:
                conn = get_db()
                conn.execute("UPDATE receipts SET telegram_msg_id = ? WHERE id = ?", (msg_id, rid))
                conn.commit()
                conn.close()
            except Exception as e:
                print("[tg] save msg_id error:", e)

        return jsonify({
            "message": "Receipt submitted for review",
            "reference": ref,
            "status": "pending",
            "id": rid,
            "plan_type": plan_type,
            "telegram": bool(msg_id),
        })

    @app.route("/api/telegram/webhook", methods=["POST", "GET"])
    def telegram_webhook():
        if request.method == "GET":
            return jsonify({"ok": True, "service": "telegram-webhook"})

        update = request.get_json(silent=True) or {}

        cq = update.get("callback_query")
        if cq:
            try:
                data = (cq.get("data") or "").strip()
                chat = str(((cq.get("message") or {}).get("chat") or {}).get("id", ""))
                expected = _chat_id()
                if expected and chat and chat != expected:
                    return jsonify({"ok": True})

                m = re.match(r"^(APPROVE|REJECT):(\d+)$", data, re.I)
                if m:
                    action, rid = m.group(1).upper(), int(m.group(2))
                    _process_receipt_decision(get_db, action, rid, callback_query=cq)
            except Exception as e:
                print("[tg] webhook callback error:", e)
            return jsonify({"ok": True})

        msg = update.get("message") or update.get("edited_message") or {}
        text = (msg.get("text") or "").strip()
        chat = str((msg.get("chat") or {}).get("id", ""))
        expected = _chat_id()
        if expected and chat and chat != expected:
            return jsonify({"ok": True})

        m = re.match(r"^(APPROVE|REJECT)\s+(\d+)$", text, re.I)
        if m:
            try:
                action, rid = m.group(1).upper(), int(m.group(2))
                _process_receipt_decision(get_db, action, rid, callback_query=None)
            except Exception as e:
                print("[tg] webhook text command error:", e)

        return jsonify({"ok": True})

    @app.route("/api/receipt/<int:rid>/decide", methods=["POST"])
    def receipt_decide(rid):
        data = request.get_json(silent=True) or {}
        secret = data.get("secret") or request.headers.get("X-Admin-Secret")
        if secret != _admin_secret():
            return jsonify({"error": "Unauthorized"}), 401
        action = (data.get("action") or "").upper()
        if action not in ("APPROVE", "REJECT"):
            return jsonify({"error": "action must be APPROVE or REJECT"}), 400
        _process_receipt_decision(get_db, action, rid, callback_query=None)
        return jsonify({"ok": True, "action": action, "rid": rid})

    @app.route("/api/telegram/status", methods=["GET"])
    def telegram_status():
        token = _token()
        chat = _chat_id()
        info = {
            "token_configured": bool(token),
            "chat_id_configured": bool(chat),
            "token_prefix": (token[:8] + "…") if token else None,
            "chat_id": chat or None,
        }
        if token:
            me = _tg_request("getMe", timeout=10, max_retries=1)
            if me and me.get("ok"):
                info["bot"] = me["result"].get("username")
                info["bot_ok"] = True
            else:
                info["bot_ok"] = False
                info["bot_error"] = (me or {}).get("description")
        else:
            info["bot_ok"] = False
        return jsonify(info)

    @app.route("/api/telegram/setup-webhook", methods=["POST", "GET"])
    def telegram_setup_webhook():
        data = request.get_json(silent=True) or {}
        base = (
            data.get("url")
            or request.args.get("url")
            or os.environ.get("PUBLIC_API_URL")
            or request.url_root.rstrip("/")
        )
        webhook_url = base.rstrip("/") + "/api/telegram/webhook"

        _tg_request("deleteWebhook", payload={"drop_pending_updates": False}, max_retries=1)

        result = _tg_request(
            "setWebhook",
            payload={
                "url": webhook_url,
                "allowed_updates": ["message", "callback_query"],
                "drop_pending_updates": False,
                "max_connections": 20,
            },
            timeout=15,
        )

        info = {
            "webhook_url": webhook_url,
            "set_ok": bool(result and result.get("ok")),
            "telegram_response": result,
        }

        wh = _tg_request("getWebhookInfo", timeout=10, max_retries=1)
        if wh and wh.get("ok"):
            info["current"] = wh["result"]

        return jsonify(info)

    @app.route("/api/telegram/test", methods=["POST"])
    def telegram_test():
        secret = (request.get_json(silent=True) or {}).get("secret") or request.headers.get("X-Admin-Secret")
        if secret != _admin_secret():
            return jsonify({"error": "Unauthorized"}), 401
        msg_id = _telegram_send(
            "✅ WealthPeak Telegram bot is online and stable.\n"
            f"Time: {datetime.datetime.utcnow().isoformat()}Z",
            reply_markup=None,
        )
        return jsonify({"ok": bool(msg_id), "message_id": msg_id})

    print("[tg] receipt + telegram routes registered")

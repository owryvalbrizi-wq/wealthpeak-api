# Deploy steps
1. Upload app_FIXED.py as app.py (replace)
2. pawapay.py already updated on repo
3. On Render: Manual Deploy -> Deploy latest commit

Fixes:
- Registration validates email format
- Login rejects wrong password
- Deposits stay PENDING until payment confirmed
- No balance credit until PawaPay COMPLETED callback
- USD amounts converted to KES (x130) for M-Pesa STK

# Pesapal Integration Setup Guide

## Current Status
The system is **ready**. It runs in **DEMO mode** until you add your real keys.

## How to go LIVE

### 1. Create Pesapal Business Account
1. Go to https://www.pesapal.com
2. Register a **Business / Merchant** account (Kenya)
3. Complete KYC / document verification
4. After approval you will receive:
   - `consumer_key`
   - `consumer_secret`

### 2. Add your keys

Open the file `api/pesapal.py` and set:

```python
PESAPAL_CONSUMER_KEY = "your_real_consumer_key_here"
PESAPAL_CONSUMER_SECRET = "your_real_consumer_secret_here"
PESAPAL_SANDBOX = False          # False = Live
```

Or use environment variables (recommended for production):

```bash
export PESAPAL_CONSUMER_KEY="your_key"
export PESAPAL_CONSUMER_SECRET="your_secret"
export PESAPAL_SANDBOX="false"
export PESAPAL_CALLBACK_URL="https://yourdomain.com/dashboard.html"
```

### 3. Register IPN (callback) URL

Once your website has a public HTTPS URL, register the IPN:

```python
from pesapal import register_ipn
result = register_ipn("https://yourdomain.com/api/pesapal/ipn")
print(result)   # Save the notification_id
```

Then put the returned `notification_id` into:

```python
PESAPAL_IPN_ID = "the-uuid-you-received"
```

### 4. Test flow

1. User clicks **Pay Securely with Pesapal**
2. Backend calls Pesapal → gets `redirect_url`
3. User is sent to Pesapal checkout (EcoCash / MTN / Airtel / M-Pesa)
4. After payment Pesapal calls your `/api/pesapal/ipn`
5. System credits the user’s balance automatically

## Endpoints prepared

| Endpoint | Purpose |
|----------|---------|
| `GET  /api/pesapal/status` | Check if keys are configured |
| `POST /api/pesapal/initiate` | Create payment & get redirect URL |
| `GET/POST /api/pesapal/ipn` | Pesapal callback (auto credit) |
| `GET  /api/pesapal/check/<id>` | Manually check transaction status |

## Notes

- Sandbox URL: `https://cybqa.pesapal.com/pesapalv3`
- Live URL: `https://pay.pesapal.com/v3`
- Supported countries for your use case: Zimbabwe (EcoCash), Zambia (MTN/Airtel), Malawi (Airtel/TNM), Kenya (M-Pesa)
- Settlement goes to your Kenyan bank account linked to the Pesapal merchant account

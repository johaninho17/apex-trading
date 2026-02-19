import os
import time
import requests
import base64
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.backends import default_backend
from dotenv import load_dotenv

load_dotenv()

API_KEY_ID = os.getenv('KALSHI_API_KEY_ID')
PRIVATE_KEY_PATH = os.getenv('KALSHI_PRIVATE_KEY')
BASE_URL = os.getenv('KALSHI_API_URL', 'https://api.elections.kalshi.com/trade-api/v2')

print(f"--- Kalshi Auth Debug ---")
print(f"Time: {time.time()} ({time.ctime()})")
print(f"API Key ID: {API_KEY_ID}")
print(f"Private Key Path/Content: {PRIVATE_KEY_PATH[:20]}..." if PRIVATE_KEY_PATH else "None")
print(f"Base URL: {BASE_URL}")

if not API_KEY_ID or not PRIVATE_KEY_PATH:
    print("❌ Missing credentials")
    exit(1)

# Load Key
try:
    if os.path.exists(PRIVATE_KEY_PATH):
        with open(PRIVATE_KEY_PATH, "rb") as f:
            key_data = f.read()
    else:
        key_data = PRIVATE_KEY_PATH.replace('\\n', '\n').encode()

    private_key = serialization.load_pem_private_key(
        key_data,
        password=None,
        backend=default_backend()
    )
    print("✅ Private key loaded successfully")
except Exception as e:
    print(f"❌ Failed to load private key: {e}")
    exit(1)

# Generate Signature
timestamp = str(int(time.time() * 1000))
method = "GET"
path = "/portfolio/balance"
message = f"{timestamp}{method}{path}"
signature = base64.b64encode(
    private_key.sign(
        message.encode('utf-8'),
        padding.PSS(
            mgf=padding.MGF1(hashes.SHA256()),
            salt_length=padding.PSS.MAX_LENGTH
        ),
        hashes.SHA256()
    )
).decode('utf-8')

headers = {
    'KALSHI-ACCESS-KEY': API_KEY_ID,
    'KALSHI-ACCESS-TIMESTAMP': timestamp,
    'KALSHI-ACCESS-SIGNATURE': signature
}

print(f"\nRequesting {BASE_URL}{path}...")
try:
    res = requests.get(f"{BASE_URL}{path}", headers=headers)
    print(f"Status: {res.status_code}")
    print(f"Response: {res.text}")
except Exception as e:
    print(f"Request failed: {e}")

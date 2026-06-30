import hashlib, binascii, os, secrets
# Importe a função hash_password do seu arquivo security.py ou redefina ela aqui:
def hash_password(password: str) -> str:
    salt = os.urandom(32)
    dk = hashlib.pbkdf2_hmac('sha256', password.encode(), salt, 150000)
    return f"{binascii.hexlify(salt).decode()}:{binascii.hexlify(dk).decode()}"

print(f"O NOVO HASH É: {hash_password('Hepta@123')}")
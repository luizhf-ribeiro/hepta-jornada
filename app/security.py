import os, hashlib, binascii, secrets
from datetime import datetime, timedelta
from .db import get_conn
from .config import SESSION_MINUTES

COOKIE_NAME = 'hepta_session'

def hash_password(password: str) -> str:
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac('sha256', password.encode(), salt, 150000)
    return binascii.hexlify(salt).decode() + ':' + binascii.hexlify(dk).decode()

def verify_password(password: str, stored: str) -> bool:
    salt_hex, hash_hex = stored.split(':')
    salt = binascii.unhexlify(salt_hex)
    dk = hashlib.pbkdf2_hmac('sha256', password.encode(), salt, 150000)
    return secrets.compare_digest(binascii.hexlify(dk).decode(), hash_hex)

def create_session(user_id: int) -> str:
    token = secrets.token_urlsafe(32)
    expires = (datetime.utcnow() + timedelta(minutes=SESSION_MINUTES)).isoformat()
    conn = get_conn()
    conn.execute('INSERT INTO sessions(token,user_id,expires_at) VALUES(?,?,?)', (token, user_id, expires))
    conn.commit(); conn.close()
    return token

def get_user_by_token(token: str):
    if not token:
        return None
    conn = get_conn()
    row = conn.execute('''SELECT u.* FROM sessions s JOIN users u ON u.id=s.user_id
                          WHERE s.token=? AND s.expires_at > ? AND u.active=1''', (token, datetime.utcnow().isoformat())).fetchone()
    conn.close()
    return row

def delete_session(token: str):
    conn = get_conn(); conn.execute('DELETE FROM sessions WHERE token=?', (token,)); conn.commit(); conn.close()

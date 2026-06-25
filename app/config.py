import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATABASE_URL = os.getenv('DATABASE_URL', os.getenv('SUPABASE_DB_URL', '')).strip()
DB_ENGINE = 'postgres' if DATABASE_URL.startswith(('postgres://', 'postgresql://')) else 'sqlite'
DB_PATH = Path(os.getenv('HEPTA_DB_PATH', BASE_DIR / 'hepta_jornada.db'))
UPLOAD_DIR = Path(os.getenv('HEPTA_UPLOAD_DIR', BASE_DIR / 'uploads'))
BACKUP_DIR = Path(os.getenv('HEPTA_BACKUP_DIR', BASE_DIR / 'backups'))

APP_HOST = os.getenv('HEPTA_HOST', '0.0.0.0')
APP_PORT = int(os.getenv('PORT', os.getenv('HEPTA_PORT', '8000')))
SESSION_MINUTES = int(os.getenv('HEPTA_SESSION_MINUTES', '720'))
CORPORATE_DOMAIN = os.getenv('HEPTA_CORPORATE_DOMAIN', '@hepta.com.br')

SMTP_HOST = os.getenv('SMTP_HOST', '')
SMTP_PORT = int(os.getenv('SMTP_PORT', '587'))
SMTP_USER = os.getenv('SMTP_USER', '')
SMTP_PASS = os.getenv('SMTP_PASS', '')
SMTP_FROM = os.getenv('SMTP_FROM', SMTP_USER)
SMTP_TLS = os.getenv('SMTP_TLS', '1') == '1'

FIRST_BUSINESS_DAY_DEADLINE = os.getenv('HEPTA_FIRST_BUSINESS_DAY_DEADLINE', '21:00')
LEGAL_TOLERANCE_PER_MARK_MIN = int(os.getenv('HEPTA_LEGAL_TOLERANCE_PER_MARK_MIN', '5'))
LEGAL_TOLERANCE_DAY_MIN = int(os.getenv('HEPTA_LEGAL_TOLERANCE_DAY_MIN', '10'))
MANAGEMENT_DELAY_ALERT_MIN = int(os.getenv('HEPTA_MANAGEMENT_DELAY_ALERT_MIN', '15'))

from pathlib import Path
import re
import sqlite3
import os
import time
import logging
from .config import BASE_DIR, DB_PATH, UPLOAD_DIR, BACKUP_DIR, DATABASE_URL, DB_ENGINE

# Configuração de logs
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

UPLOAD_DIR.mkdir(exist_ok=True)
BACKUP_DIR.mkdir(exist_ok=True)

ID_TABLES = {
    'users','time_records','adjustment_requests','monthly_sheets','audit_logs',
    'notifications','email_queue','login_history','password_history','holidays','file_blobs'
}

class PgCompatCursor:
    def __init__(self, cur):
        self.cur = cur
        self.lastrowid = None

    def fetchone(self):
        return self.cur.fetchone()

    def fetchall(self):
        return self.cur.fetchall()

    def __iter__(self):
        return iter(self.cur)


class PgCompatConnection:
    def __init__(self, conn):
        self.conn = conn

    def execute(self, sql, params=()):
        cur = self.conn.cursor()
        sql = self._translate(sql)
        returning = False
        m = re.match(r"\s*insert\s+into\s+([a-zA-Z_][a-zA-Z0-9_]*)", sql, flags=re.I)
        if m:
            table = m.group(1).lower()
            if table in ID_TABLES and ' returning ' not in sql.lower():
                sql = sql.rstrip().rstrip(';') + ' RETURNING id'
                returning = True

        cur.execute(sql, params or ())
        wrapper = PgCompatCursor(cur)
        if returning:
            row = cur.fetchone()
            if row:
                wrapper.lastrowid = row.get('id') if isinstance(row, dict) else row[0]
        return wrapper

    def cursor(self):
        return self

    def executescript(self, sql):
        cur = self.conn.cursor()
        cur.execute(sql)
        return PgCompatCursor(cur)

    def commit(self):
        self.conn.commit()

    def rollback(self):
        self.conn.rollback()   # ← Correção aqui

    def close(self):
        self.conn.close()

    def _translate(self, sql: str) -> str:
        out = sql.replace('?', '%s')
        out = out.replace('status="pendente"', "status='pendente'")
        out = out.replace('status="aprovado"', "status='aprovado'")
        return out


def get_conn():
    if DB_ENGINE == 'postgres':
        import psycopg
        from psycopg.rows import dict_row

        retries = 12
        for attempt in range(retries):
            try:
                conn = psycopg.connect(
                    DATABASE_URL,
                    row_factory=dict_row,
                    connect_timeout=25,
                    keepalives=1,
                    keepalives_idle=60,
                    keepalives_interval=10,
                    keepalives_count=5,
                    sslmode='require'  # Força SSL
                )
                logger.info("✅ Conexão com Supabase realizada com sucesso!")
                return PgCompatConnection(conn)
            except Exception as e:
                logger.warning(f"❌ Tentativa {attempt+1}/{retries} falhou: {e}")
                if attempt == retries - 1:
                    logger.error("❌ Falha final na conexão com Supabase.")
                    raise
                time.sleep(5 * (attempt + 1))  # backoff mais agressivo

    # SQLite fallback
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys = ON')
    return conn


def init_db():
    if DB_ENGINE == 'postgres':
        try:
            init_postgres()
        except Exception as e:
            logger.error(f"❌ Falha ao conectar no banco (Supabase). A aplicação vai continuar sem DB por enquanto: {e}")
            # Não levanta erro → permite que a app suba
    else:
        init_sqlite()


def init_postgres():
    conn = get_conn()
    
    ddl = '''
    CREATE TABLE IF NOT EXISTS users (...);  -- (coloque aqui todo o DDL completo)
    '''  # ← Vamos completar isso

    # DDL completo (mesmo do seu arquivo original)
    ddl = '''
    CREATE TABLE IF NOT EXISTS users (
        id SERIAL PRIMARY KEY,
        name TEXT NOT NULL,
        email TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL,
        role TEXT NOT NULL CHECK(role IN ('colaborador','gestor','admin')),
        active INTEGER NOT NULL DEFAULT 1,
        work_start TEXT DEFAULT '08:00',
        lunch_min INTEGER DEFAULT 60,
        work_minutes INTEGER DEFAULT 480,
        created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
        must_change_password INTEGER DEFAULT 0,
        last_password_change TEXT,
        failed_login_attempts INTEGER DEFAULT 0,
        locked_until TEXT
    );
    CREATE TABLE IF NOT EXISTS sessions (
        token TEXT PRIMARY KEY,
        user_id INTEGER NOT NULL REFERENCES users(id),
        created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
        expires_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS time_records (
        id SERIAL PRIMARY KEY,
        user_id INTEGER NOT NULL REFERENCES users(id),
        record_date TEXT NOT NULL,
        event_type TEXT NOT NULL CHECK(event_type IN ('entrada','almoco_inicio','almoco_fim','saida')),
        server_time TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
        ip TEXT,
        user_agent TEXT,
        source TEXT NOT NULL DEFAULT 'web',
        immutable INTEGER NOT NULL DEFAULT 1
    );
    CREATE TABLE IF NOT EXISTS adjustment_requests (
        id SERIAL PRIMARY KEY,
        user_id INTEGER NOT NULL REFERENCES users(id),
        target_date TEXT NOT NULL,
        event_type TEXT NOT NULL,
        requested_time TEXT,
        reason TEXT NOT NULL,
        evidence_path TEXT,
        status TEXT NOT NULL DEFAULT 'pendente' CHECK(status IN ('pendente','aprovado','rejeitado')),
        manager_id INTEGER REFERENCES users(id),
        manager_note TEXT,
        decided_at TEXT,
        created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS monthly_sheets (
        id SERIAL PRIMARY KEY,
        user_id INTEGER NOT NULL REFERENCES users(id),
        month_ref TEXT NOT NULL,
        version INTEGER NOT NULL DEFAULT 1,
        file_path TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'pendente_aprovacao' CHECK(status IN ('pendente_aprovacao','aprovado','reprovado','enviado_assinado','enviado_pendente_assinatura','arquivo_corrente')),
        created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
        parsed_name TEXT,
        parsed_period TEXT,
        parsed_function TEXT,
        parsed_schedule TEXT,
        raw_text TEXT,
        manual_filled INTEGER DEFAULT 0,
        govbr_signed INTEGER DEFAULT 0,
        release_month_ref TEXT,
        validated_by INTEGER REFERENCES users(id),
        validated_at TEXT,
        validation_note TEXT,
        rejection_reason TEXT,
        active_version INTEGER NOT NULL DEFAULT 1,
        doc_type TEXT NOT NULL DEFAULT 'preenchida_anterior',
        requires_manager_approval INTEGER NOT NULL DEFAULT 1
    );
    CREATE TABLE IF NOT EXISTS file_blobs (
        id SERIAL PRIMARY KEY,
        sheet_id INTEGER REFERENCES monthly_sheets(id),
        filename TEXT NOT NULL,
        content_type TEXT NOT NULL DEFAULT 'application/pdf',
        content BYTEA NOT NULL,
        sha256 TEXT,
        created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS audit_logs (
        id SERIAL PRIMARY KEY,
        user_id INTEGER REFERENCES users(id),
        action TEXT NOT NULL,
        entity TEXT NOT NULL,
        entity_id TEXT,
        details TEXT,
        ip TEXT,
        user_agent TEXT,
        created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS notifications (
        id SERIAL PRIMARY KEY,
        user_id INTEGER NOT NULL REFERENCES users(id),
        title TEXT NOT NULL,
        message TEXT NOT NULL,
        level TEXT NOT NULL DEFAULT 'info',
        read_at TEXT,
        created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS email_queue (
        id SERIAL PRIMARY KEY,
        user_id INTEGER REFERENCES users(id),
        to_email TEXT NOT NULL,
        subject TEXT NOT NULL,
        body TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'pendente',
        attempts INTEGER NOT NULL DEFAULT 0,
        error TEXT,
        created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
        sent_at TEXT
    );
    CREATE TABLE IF NOT EXISTS login_history (
        id SERIAL PRIMARY KEY,
        user_id INTEGER,
        email TEXT,
        success INTEGER NOT NULL,
        ip TEXT,
        user_agent TEXT,
        created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS password_history (
        id SERIAL PRIMARY KEY,
        user_id INTEGER NOT NULL REFERENCES users(id),
        password_hash TEXT NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS holidays (
        id SERIAL PRIMARY KEY,
        holiday_date TEXT UNIQUE NOT NULL,
        description TEXT NOT NULL,
        active INTEGER NOT NULL DEFAULT 1
    );
    CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL,
        updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    '''

    for stmt in [s.strip() for s in ddl.split(';') if s.strip()]:
        if stmt:
            conn.execute(stmt)

    conn.commit()
    conn.close()
    logger.info("✅ Banco PostgreSQL inicializado com sucesso.")


def init_sqlite():
    # Seu código original do init_sqlite (mantido igual)
    conn = get_conn()
    cur = conn.cursor()
    cur.executescript(''' ... seu código completo do init_sqlite ... ''')
    # (mantenha todo o resto do init_sqlite como você tinha antes)
    conn.commit()
    conn.close()
from pathlib import Path
import re
import sqlite3
from .config import BASE_DIR, DB_PATH, UPLOAD_DIR, BACKUP_DIR, DATABASE_URL, DB_ENGINE

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
        # usado apenas no caminho SQLite; mantido por compatibilidade
        cur = self.conn.cursor(); cur.execute(sql); return PgCompatCursor(cur)
    def commit(self):
        self.conn.commit()
    def rollback(self):
        self.conn.rollback()
    def close(self):
        self.conn.close()
    def _translate(self, sql: str) -> str:
        # Compatibilidade básica SQLite -> PostgreSQL.
        out = sql.replace('?', '%s')
        out = out.replace('status="pendente"', "status='pendente'")
        out = out.replace('status="aprovado"', "status='aprovado'")
        out = out.replace('CURRENT_TIMESTAMP', 'CURRENT_TIMESTAMP')
        return out


def get_conn():
    if DB_ENGINE == 'postgres':
        import psycopg
        from psycopg.rows import dict_row
        conn = psycopg.connect(DATABASE_URL, row_factory=dict_row)
        return PgCompatConnection(conn)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys = ON')
    return conn


def init_db():
    if DB_ENGINE == 'postgres':
        init_postgres()
    else:
        init_sqlite()


def init_postgres():
    conn = get_conn()
    # DDL compatível com Supabase/PostgreSQL.
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
    # psycopg não executa múltiplos statements via execute em alguns ambientes; dividir com segurança simples.
    for stmt in [s.strip() for s in ddl.split(';') if s.strip()]:
        conn.execute(stmt)
    conn.commit(); conn.close()


def init_sqlite():
    conn = get_conn()
    cur = conn.cursor()
    cur.executescript('''
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        email TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL,
        role TEXT NOT NULL CHECK(role IN ('colaborador','gestor','admin')),
        active INTEGER NOT NULL DEFAULT 1,
        work_start TEXT DEFAULT '08:00',
        lunch_min INTEGER DEFAULT 60,
        work_minutes INTEGER DEFAULT 480,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS sessions (
        token TEXT PRIMARY KEY,
        user_id INTEGER NOT NULL,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        expires_at TEXT NOT NULL,
        FOREIGN KEY(user_id) REFERENCES users(id)
    );
    CREATE TABLE IF NOT EXISTS time_records (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        record_date TEXT NOT NULL,
        event_type TEXT NOT NULL CHECK(event_type IN ('entrada','almoco_inicio','almoco_fim','saida')),
        server_time TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        ip TEXT,
        user_agent TEXT,
        source TEXT NOT NULL DEFAULT 'web',
        immutable INTEGER NOT NULL DEFAULT 1,
        FOREIGN KEY(user_id) REFERENCES users(id)
    );
    CREATE TABLE IF NOT EXISTS adjustment_requests (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        target_date TEXT NOT NULL,
        event_type TEXT NOT NULL,
        requested_time TEXT,
        reason TEXT NOT NULL,
        evidence_path TEXT,
        status TEXT NOT NULL DEFAULT 'pendente' CHECK(status IN ('pendente','aprovado','rejeitado')),
        manager_id INTEGER,
        manager_note TEXT,
        decided_at TEXT,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(user_id) REFERENCES users(id),
        FOREIGN KEY(manager_id) REFERENCES users(id)
    );
    CREATE TABLE IF NOT EXISTS monthly_sheets (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        month_ref TEXT NOT NULL,
        version INTEGER NOT NULL DEFAULT 1,
        file_path TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'pendente_aprovacao' CHECK(status IN ('pendente_aprovacao','aprovado','reprovado','enviado_assinado','enviado_pendente_assinatura','arquivo_corrente')),
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        parsed_name TEXT,
        parsed_period TEXT,
        parsed_function TEXT,
        parsed_schedule TEXT,
        raw_text TEXT,
        manual_filled INTEGER DEFAULT 0,
        govbr_signed INTEGER DEFAULT 0,
        release_month_ref TEXT,
        validated_by INTEGER,
        validated_at TEXT,
        validation_note TEXT,
        rejection_reason TEXT,
        active_version INTEGER NOT NULL DEFAULT 1,
        doc_type TEXT NOT NULL DEFAULT 'preenchida_anterior',
        requires_manager_approval INTEGER NOT NULL DEFAULT 1,
        FOREIGN KEY(user_id) REFERENCES users(id),
        FOREIGN KEY(validated_by) REFERENCES users(id)
    );
    CREATE TABLE IF NOT EXISTS file_blobs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        sheet_id INTEGER,
        filename TEXT NOT NULL,
        content_type TEXT NOT NULL DEFAULT 'application/pdf',
        content BLOB NOT NULL,
        sha256 TEXT,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(sheet_id) REFERENCES monthly_sheets(id)
    );
    CREATE TABLE IF NOT EXISTS audit_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        action TEXT NOT NULL,
        entity TEXT NOT NULL,
        entity_id TEXT,
        details TEXT,
        ip TEXT,
        user_agent TEXT,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(user_id) REFERENCES users(id)
    );
    CREATE TABLE IF NOT EXISTS notifications (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        title TEXT NOT NULL,
        message TEXT NOT NULL,
        level TEXT NOT NULL DEFAULT 'info',
        read_at TEXT,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(user_id) REFERENCES users(id)
    );
    CREATE TABLE IF NOT EXISTS email_queue (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        to_email TEXT NOT NULL,
        subject TEXT NOT NULL,
        body TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'pendente',
        attempts INTEGER NOT NULL DEFAULT 0,
        error TEXT,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        sent_at TEXT,
        FOREIGN KEY(user_id) REFERENCES users(id)
    );
    CREATE TABLE IF NOT EXISTS login_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        email TEXT,
        success INTEGER NOT NULL,
        ip TEXT,
        user_agent TEXT,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS password_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        password_hash TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(user_id) REFERENCES users(id)
    );
    CREATE TABLE IF NOT EXISTS holidays (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        holiday_date TEXT UNIQUE NOT NULL,
        description TEXT NOT NULL,
        active INTEGER NOT NULL DEFAULT 1
    );
    CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    ''')
    user_cols = [r[1] for r in conn.execute('PRAGMA table_info(users)').fetchall()]
    user_extra_cols = {'must_change_password': 'INTEGER DEFAULT 0','last_password_change': 'TEXT','failed_login_attempts': 'INTEGER DEFAULT 0','locked_until': 'TEXT'}
    for col, typ in user_extra_cols.items():
        if col not in user_cols:
            conn.execute(f'ALTER TABLE users ADD COLUMN {col} {typ}')
    cols = [r[1] for r in conn.execute('PRAGMA table_info(monthly_sheets)').fetchall()]
    extra_cols = {'parsed_name':'TEXT','parsed_period':'TEXT','parsed_function':'TEXT','parsed_schedule':'TEXT','raw_text':'TEXT','manual_filled':'INTEGER DEFAULT 0','govbr_signed':'INTEGER DEFAULT 0','release_month_ref':'TEXT','validated_by':'INTEGER','validated_at':'TEXT','validation_note':'TEXT','version':'INTEGER DEFAULT 1','rejection_reason':'TEXT','active_version':'INTEGER DEFAULT 1','doc_type':"TEXT NOT NULL DEFAULT 'preenchida_anterior'",'requires_manager_approval':'INTEGER NOT NULL DEFAULT 1'}
    for col, typ in extra_cols.items():
        if col not in cols:
            conn.execute(f'ALTER TABLE monthly_sheets ADD COLUMN {col} {typ}')
    blob_cols = [r[1] for r in conn.execute('PRAGMA table_info(file_blobs)').fetchall()]
    conn.commit(); conn.close()

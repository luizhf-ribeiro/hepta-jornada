from .db import get_conn
from .audit import log
from .config import SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASS, SMTP_FROM, SMTP_TLS
import smtplib
from email.message import EmailMessage


def create_notification(user_id, title, message, level='info', request=None):
    conn = get_conn()
    cur = conn.execute('INSERT INTO notifications(user_id,title,message,level) VALUES(?,?,?,?)',
                       (user_id, title, message, level))
    conn.commit(); nid = cur.lastrowid; conn.close()
    if request:
        log(user_id, 'CRIADA_NOTIFICACAO', 'notifications', nid, title, request)
    return nid


def enqueue_email(to_email, subject, body, user_id=None):
    conn = get_conn()
    cur = conn.execute('INSERT INTO email_queue(user_id,to_email,subject,body,status) VALUES(?,?,?,?,?)',
                       (user_id, to_email, subject, body, 'pendente'))
    conn.commit(); eid = cur.lastrowid; conn.close()
    return eid


def send_email_now(to_email, subject, body):
    if not SMTP_HOST or not SMTP_FROM:
        print(f'[EMAIL HOMOLOGACAO] Para: {to_email} | Assunto: {subject}\n{body}')
        return False
    msg = EmailMessage()
    msg['From'] = SMTP_FROM
    msg['To'] = to_email
    msg['Subject'] = subject
    msg.set_content(body)
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=20) as smtp:
        if SMTP_TLS:
            smtp.starttls()
        if SMTP_USER:
            smtp.login(SMTP_USER, SMTP_PASS)
        smtp.send_message(msg)
    return True


def process_email_queue(limit=100):
    conn = get_conn()
    rows = conn.execute("SELECT * FROM email_queue WHERE status='pendente' ORDER BY created_at LIMIT ?", (limit,)).fetchall()
    sent = failed = 0
    for r in rows:
        try:
            delivered = send_email_now(r['to_email'], r['subject'], r['body'])
            status = 'enviado' if delivered else 'simulado'
            conn.execute("UPDATE email_queue SET status=?, sent_at=CURRENT_TIMESTAMP, error=NULL WHERE id=?", (status, r['id']))
            sent += 1
        except Exception as exc:
            conn.execute("UPDATE email_queue SET status='erro', attempts=attempts+1, error=? WHERE id=?", (str(exc), r['id']))
            failed += 1
    conn.commit(); conn.close()
    return {'processed': len(rows), 'sent_or_simulated': sent, 'failed': failed}

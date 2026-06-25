from .db import get_conn

def log(user_id, action, entity, entity_id=None, details=None, request=None):
    ip = request.client.host if request and request.client else None
    ua = request.headers.get('user-agent') if request else None
    conn = get_conn()
    conn.execute('INSERT INTO audit_logs(user_id,action,entity,entity_id,details,ip,user_agent) VALUES(?,?,?,?,?,?,?)',
                 (user_id, action, entity, str(entity_id) if entity_id else None, details, ip, ua))
    conn.commit(); conn.close()

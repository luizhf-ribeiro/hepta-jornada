from datetime import date
from .db import init_db, get_conn
from .notify import enqueue_email, process_email_queue
from .main import check_jornada_release


def gerar_alertas_pendencias():
    init_db()
    conn = get_conn()
    users = conn.execute("SELECT * FROM users WHERE active=1 AND role='colaborador'").fetchall()
    managers = conn.execute("SELECT email FROM users WHERE active=1 AND role IN ('gestor','admin')").fetchall()
    today = date.today().isoformat()
    pendencias = []
    for u in users:
        release = check_jornada_release(u['id'], date.today())
        recs = conn.execute('SELECT event_type FROM time_records WHERE user_id=? AND record_date=?', (u['id'], today)).fetchall()
        events = {r['event_type'] for r in recs}
        msgs = []
        if not release['released']:
            msgs.append('folha do mês anterior pendente/reprovada')
        if 'entrada' in events and 'saida' not in events:
            msgs.append('saída não registrada')
        if 'almoco_inicio' in events and 'almoco_fim' not in events:
            msgs.append('retorno do almoço não registrado')
        if msgs:
            texto = f"Olá, {u['name']}. Identificamos pendência(s) de jornada: {', '.join(msgs)}. Acesse o HEPTA Jornada para regularizar."
            enqueue_email(u['email'], 'Pendência de apontamento de jornada - HEPTA', texto, u['id'])
            pendencias.append(f"{u['name']}: {', '.join(msgs)}")
    if pendencias and managers:
        body = 'Pendências de jornada identificadas hoje:\n\n' + '\n'.join(pendencias)
        for m in managers:
            enqueue_email(m['email'], 'Pendências de jornada da equipe - Consolidado diário', body)
    conn.close()
    return process_email_queue()

if __name__ == '__main__':
    print(gerar_alertas_pendencias())

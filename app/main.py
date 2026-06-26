from fastapi import FastAPI, Request, Form, UploadFile, File, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pathlib import Path
from datetime import datetime, date, timedelta, time
import csv, io, shutil, calendar, re, os, smtplib, hashlib
from .db import init_db, get_conn, UPLOAD_DIR
from .security import hash_password, verify_password, create_session, get_user_by_token, delete_session, COOKIE_NAME
from .audit import log
from .config import CORPORATE_DOMAIN, FIRST_BUSINESS_DAY_DEADLINE, MANAGEMENT_DELAY_ALERT_MIN, LEGAL_TOLERANCE_DAY_MIN
from .notify import enqueue_email, process_email_queue

app = FastAPI(title='HEPTA Jornada')
BASE = Path(__file__).resolve().parent
app.mount('/static', StaticFiles(directory=BASE/'static'), name='static')
templates = Jinja2Templates(directory=str(BASE/'templates'))

@app.on_event('startup')
def startup():
    init_db()
    #seed_admin()

def seed_admin():
    conn = get_conn()
    exists = conn.execute("SELECT id FROM users WHERE email='admin@hepta.com.br'").fetchone()
    if not exists:
        conn.execute('INSERT INTO users(name,email,password_hash,role) VALUES(?,?,?,?)',
                      ('Administrador HEPTA','admin@hepta.com.br',hash_password('Hepta@123'),'admin'))
        conn.commit()
    conn.close()





def send_email(to_email: str, subject: str, body: str, user_id: int | None = None):
    """Registra e-mail em fila. O envio real depende das variáveis SMTP."""
    return enqueue_email(to_email, subject, body, user_id)


EVENT_LABELS = {
    'entrada': 'Entrada',
    'almoco_inicio': 'Início almoço',
    'almoco_fim': 'Retorno almoço',
    'saida': 'Saída',
    'falta': 'Falta',
    'atraso': 'Atraso'
}

def parse_pdf_sheet(path: str):
    """Extrai metadados simples da folha HEPTA em PDF. Falha sem bloquear o upload."""
    result = {'parsed_name': None, 'parsed_period': None, 'parsed_function': None, 'parsed_schedule': None, 'raw_text': None}
    try:
        from pypdf import PdfReader
        reader = PdfReader(path)
        text = '\n'.join(page.extract_text() or '' for page in reader.pages)
        result['raw_text'] = text[:12000]
        name = re.search(r'Nome:\s*(.*?)\s+CTPS:', text, re.S|re.I)
        period = re.search(r'Per[ií]odo:\s*([0-9]{1,2}/[0-9]{4})', text, re.I)
        function = re.search(r'Fun[cç][aã]o:\s*(.*?)(?:\n|\r)', text, re.I)
        schedule = re.search(r'(\d{2}:\d{2}\s*[àa]\s*\d{2}:\d{2}\s*/\s*\d{2}:\d{2}\s*[àa]\s*\d{2}:\d{2})', text, re.I)
        if name: result['parsed_name'] = ' '.join(name.group(1).split())
        if period: result['parsed_period'] = period.group(1)
        if function: result['parsed_function'] = ' '.join(function.group(1).split())
        if schedule: result['parsed_schedule'] = schedule.group(1).replace(' a ', ' às ')
    except Exception as exc:
        result['raw_text'] = f'Falha na leitura automática do PDF: {exc}'
    return result

def minutes_to_hhmm(minutes):
    sign = '-' if minutes < 0 else ''
    minutes = abs(int(minutes or 0))
    return f"{sign}{minutes//60:02d}:{minutes%60:02d}"

def parse_dt(value):
    if not value:
        return None
    for fmt in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%dT%H:%M:%S'):
        try:
            return datetime.strptime(str(value)[:19], fmt)
        except ValueError:
            pass
    return None

def time_to_dt(day_iso, hhmm):
    if not hhmm:
        return None
    try:
        return datetime.strptime(f'{day_iso} {hhmm}', '%Y-%m-%d %H:%M')
    except ValueError:
        return None

def month_bounds(month_ref: str):
    y, m = [int(x) for x in month_ref.split('-')]
    last = calendar.monthrange(y, m)[1]
    return date(y, m, 1), date(y, m, last)



def previous_month_ref(ref_date: date | None = None):
    d = ref_date or date.today()
    y, m = d.year, d.month
    if m == 1:
        return f"{y-1}-12"
    return f"{y}-{m-1:02d}"

def first_business_day(year: int, month: int):
    d = date(year, month, 1)
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d

def release_month_for_sheet(month_ref: str):
    y, m = [int(x) for x in month_ref.split('-')]
    if m == 12:
        return f"{y+1}-01"
    return f"{y}-{m+1:02d}"

DOC_PREVIOUS_COMPLETED = 'preenchida_anterior'
DOC_CURRENT_BLANK = 'corrente_modelo'

def doc_type_label(doc_type: str):
    return 'Folha preenchida do mês anterior' if doc_type == DOC_PREVIOUS_COMPLETED else 'Folha do mês corrente'

def check_jornada_release(user_id: int, ref_date: date | None = None):
    d = ref_date or date.today()
    now = datetime.now()
    fbd = first_business_day(d.year, d.month)
    required = previous_month_ref(d)
    current_ref = d.strftime('%Y-%m')

    # Antes do 1º dia útil não exige a folha do mês anterior.
    if d < fbd:
        return {'released': True, 'required_month': required, 'reason': None, 'first_business_day': fbd.isoformat()}

    # No 1º dia útil até o horário configurado, permite registro para o colaborador anexar a folha.
    deadline_h, deadline_m = [int(x) for x in FIRST_BUSINESS_DAY_DEADLINE.split(':')]
    deadline = time(deadline_h, deadline_m)
    if d == fbd and now.time() <= deadline:
        return {'released': True, 'required_month': required, 'reason': None, 'first_business_day': fbd.isoformat(), 'deadline': FIRST_BUSINESS_DAY_DEADLINE}

    conn = get_conn()
    row = conn.execute("""SELECT * FROM monthly_sheets
                          WHERE user_id=? AND month_ref=?
                            AND COALESCE(doc_type,'preenchida_anterior')=?
                            AND COALESCE(manual_filled,0)=1
                            AND COALESCE(govbr_signed,0)=1
                          ORDER BY version DESC, created_at DESC LIMIT 1""", (user_id, required, DOC_PREVIOUS_COMPLETED)).fetchone()
    conn.close()
    if row and row['status'] in ('pendente_aprovacao', 'aprovado'):
        return {'released': True, 'required_month': required, 'reason': None, 'sheet': row, 'first_business_day': fbd.isoformat()}

    detail = 'não enviada' if not row else f"status atual: {row['status']}"
    return {
        'released': False,
        'required_month': required,
        'release_month': current_ref,
        'first_business_day': fbd.isoformat(),
        'reason': f'Para registrar a jornada de {current_ref}, a folha HEPTA de {required} deve estar enviada até o horário limite configurado do 1º dia útil, preenchida manualmente, assinada via GOV.br e não pode estar reprovada. Situação: {detail}.'
    }

def month_days(start: date, end: date):
    d = start
    while d <= end:
        yield d
        d += timedelta(days=1)

def compute_month_panel(user_id: int, month_ref: str):
    start, end = month_bounds(month_ref)
    conn = get_conn()
    user = conn.execute('SELECT * FROM users WHERE id=?', (user_id,)).fetchone()
    sheet = conn.execute('SELECT * FROM monthly_sheets WHERE user_id=? AND month_ref=?', (user_id, month_ref)).fetchone()
    recs = conn.execute("""SELECT * FROM time_records WHERE user_id=? AND record_date BETWEEN ? AND ? ORDER BY record_date,server_time""",
                        (user_id, start.isoformat(), end.isoformat())).fetchall()
    approved = conn.execute("""SELECT * FROM adjustment_requests WHERE user_id=? AND target_date BETWEEN ? AND ? AND status='aprovado' ORDER BY target_date,created_at""",
                            (user_id, start.isoformat(), end.isoformat())).fetchall()
    pending_count = conn.execute("""SELECT COUNT(*) c FROM adjustment_requests WHERE user_id=? AND target_date BETWEEN ? AND ? AND status='pendente' """,
                                 (user_id, start.isoformat(), end.isoformat())).fetchone()['c']
    conn.close()
    by_day = {d.isoformat(): {'date': d, 'events': {}, 'raw': [], 'adjustments': []} for d in month_days(start, end)}
    for r in recs:
        day = by_day.get(r['record_date'])
        if day is not None:
            dt = parse_dt(r['server_time'])
            if dt and r['event_type'] not in day['events']:
                day['events'][r['event_type']] = dt
            day['raw'].append(r)
    for a in approved:
        day = by_day.get(a['target_date'])
        if day is not None:
            day['adjustments'].append(a)
            if a['event_type'] in ['entrada','almoco_inicio','almoco_fim','saida'] and a['requested_time']:
                dt = time_to_dt(a['target_date'], a['requested_time'])
                if dt:
                    day['events'][a['event_type']] = dt
    expected_minutes_day = int(user['work_minutes'] or 480) if user else 480
    calendar_rows = []
    total_expected = total_worked = total_positive = total_negative = 0
    expected_days = present_days = absences = incomplete = 0
    for key in sorted(by_day.keys()):
        item = by_day[key]
        d = item['date']
        business = d.weekday() < 5
        expected = expected_minutes_day if business else 0
        if business:
            expected_days += 1
            total_expected += expected
        ev = item['events']
        worked = None
        status = 'Sem registro'
        status_class = 'ausente'
        entrada, a_ini, a_fim, saida = ev.get('entrada'), ev.get('almoco_inicio'), ev.get('almoco_fim'), ev.get('saida')
        if entrada or saida or a_ini or a_fim:
            if entrada and saida:
                lunch = 0
                if a_ini and a_fim:
                    lunch = max(0, int((a_fim - a_ini).total_seconds()//60))
                worked = max(0, int((saida - entrada).total_seconds()//60) - lunch)
                total_worked += worked
                if worked >= expected:
                    status = 'Presente'
                    status_class = 'presente'
                else:
                    status = 'Jornada menor'
                    status_class = 'atencao'
                if business:
                    present_days += 1
            else:
                incomplete += 1
                worked = 0
                status = 'Incompleto'
                status_class = 'incompleto'
        elif not business:
            status = 'Fim de semana'
            status_class = 'folga'
        else:
            absences += 1
        if worked is not None:
            balance = worked - expected
        elif business:
            balance = -expected
        else:
            balance = 0
        if balance > 0: total_positive += balance
        if balance < 0: total_negative += abs(balance)
        labels = []
        for et in ['entrada','almoco_inicio','almoco_fim','saida']:
            if ev.get(et): labels.append(f"{EVENT_LABELS[et]} {ev[et].strftime('%H:%M')}")
        for a in item['adjustments']:
            labels.append(f"Ajuste aprovado: {EVENT_LABELS.get(a['event_type'], a['event_type'])}")
        calendar_rows.append({
            'date': d, 'day': d.day, 'weekday': ['Seg','Ter','Qua','Qui','Sex','Sáb','Dom'][d.weekday()],
            'business': business, 'status': status, 'status_class': status_class, 'events_text': ' · '.join(labels),
            'worked': minutes_to_hhmm(worked or 0), 'expected': minutes_to_hhmm(expected), 'balance': minutes_to_hhmm(balance),
            'positive': balance > 0, 'negative': balance < 0
        })
    assiduity = round((present_days / expected_days) * 100, 1) if expected_days else 0
    return {
        'employee': user, 'month_ref': month_ref, 'sheet': sheet, 'days': calendar_rows,
        'cards': {
            'assiduity': assiduity, 'expected_days': expected_days, 'present_days': present_days,
            'absences': absences, 'incomplete': incomplete, 'pending': pending_count,
            'expected': minutes_to_hhmm(total_expected), 'worked': minutes_to_hhmm(total_worked),
            'positive': minutes_to_hhmm(total_positive), 'negative': minutes_to_hhmm(total_negative),
            'balance': minutes_to_hhmm(total_worked - total_expected)
        }
    }

def available_months_for_user(user_id: int):
    conn = get_conn()
    rows = conn.execute("""SELECT month_ref FROM monthly_sheets WHERE user_id=?
                           UNION SELECT substr(record_date,1,7) month_ref FROM time_records WHERE user_id=?
                           ORDER BY month_ref DESC""", (user_id, user_id)).fetchall()
    conn.close()
    if rows:
        return [r['month_ref'] for r in rows]
    return [date.today().strftime('%Y-%m')]

def current_user(request: Request):
    return get_user_by_token(request.cookies.get(COOKIE_NAME))

def require_user(request: Request):
    user = current_user(request)
    if not user: raise HTTPException(status_code=401)
    return user

def require_role(request: Request, roles):
    user = require_user(request)
    if user['role'] not in roles: raise HTTPException(status_code=403)
    return user

def redirect(path='/'):
    return RedirectResponse(path, status_code=303)

@app.get('/login', response_class=HTMLResponse)
def login_page(request: Request):
    return templates.TemplateResponse(request=request, name='login.html')

@app.post('/login')
def login(request: Request, email: str = Form(...), password: str = Form(...)):
    conn = get_conn()
    user = conn.execute('SELECT * FROM public.users WHERE email=? AND active=1', (email.lower().strip(),)).fetchone()
    conn.close() # 2. Fechar imediatamente após a consulta
    print(f"DEBUG: E-mail buscado: '{email.lower().strip()}'")
    print(f"DEBUG: Usuário encontrado: {user}")
    valida = False
    if user:
        valida = verify_password(password, user['password_hash'])
        print(f"DEBUG: Senha é válida: {valida}")
    if not user or not valida:
        conn = get_conn()
        conn.execute('INSERT INTO login_history(email,success,ip,user_agent) VALUES(?,?,?,?)', 
                     (email.lower(), 0, request.client.host if request.client else None, request.headers.get('user-agent')))
        conn.commit()
        conn.close()
        log(None, 'LOGIN_FALHA', 'auth', details=email, request=request)
        return templates.TemplateResponse(request=request, name='login.html', context={'request': request, 'error':'Usuário ou senha inválidos'})
    token = create_session(user['id'])
    conn = get_conn()
    conn.execute('INSERT INTO login_history(user_id,email,success,ip,user_agent) VALUES(?,?,?,?,?)', 
                 (user['id'], email.lower(), 1, request.client.host if request.client else None, request.headers.get('user-agent')))
    conn.commit()
    conn.close()
    log(user['id'], 'LOGIN_SUCESSO', 'auth', request=request)
    if user.get('must_change_password'):
        resp = redirect('/alterar-senha')
    else:
        resp = redirect('/')
    resp.set_cookie(COOKIE_NAME, token, httponly=True, samesite='lax')
    return resp

@app.get('/logout')
def logout(request: Request):
    token = request.cookies.get(COOKIE_NAME)
    user = current_user(request)
    if token: delete_session(token)
    if user: log(user['id'], 'LOGOUT', 'auth', request=request)
    resp = redirect('/login'); resp.delete_cookie(COOKIE_NAME); return resp

@app.get('/', response_class=HTMLResponse)
def home(request: Request):
    user = require_user(request)
    conn = get_conn()
    today = date.today().isoformat()
    records = conn.execute('SELECT * FROM time_records WHERE user_id=? AND record_date=? ORDER BY server_time', (user['id'], today)).fetchall()
    pending = conn.execute("SELECT COUNT(*) c FROM adjustment_requests WHERE status='pendente'").fetchone()['c'] if user['role'] in ('gestor','admin') else 0
    conn.close()
    release = check_jornada_release(user['id'], date.today()) if user['role'] == 'colaborador' else {'released': True}
    return templates.TemplateResponse(request=request, name='home.html', context={'user':user,'records':records,'today':today,'pending':pending,'release':release})

@app.post('/ponto/{event_type}')
def mark_time(event_type: str, request: Request):
    user = require_user(request)
    if event_type not in ['entrada','almoco_inicio','almoco_fim','saida']: raise HTTPException(400)
    if user['role'] == 'colaborador':
        release = check_jornada_release(user['id'], date.today())
        if not release['released']:
            log(user['id'], 'BLOQUEIO_REGISTRO_SEM_FOLHA_ASSINADA', 'time_records', None, release.get('reason'), request)
            return redirect('/folha-preenchida?bloqueado=1')
    today = date.today().isoformat()
    conn = get_conn()
    exists = conn.execute('SELECT id FROM time_records WHERE user_id=? AND record_date=? AND event_type=?', (user['id'], today, event_type)).fetchone()
    if exists:
        conn.close(); return redirect('/?erro=duplicado')
    ua = request.headers.get('user-agent'); ip = request.client.host if request.client else None
    cur = conn.execute('INSERT INTO time_records(user_id,record_date,event_type,ip,user_agent) VALUES(?,?,?,?,?)', (user['id'], today, event_type, ip, ua))
    conn.commit(); rid = cur.lastrowid; conn.close()
    log(user['id'], 'REGISTRO_PONTO', 'time_records', rid, event_type, request)
    return redirect('/')

@app.get('/ajustes', response_class=HTMLResponse)
def adjustments(request: Request):
    user = require_user(request)
    conn = get_conn()
    if user['role'] in ('gestor','admin'):
        rows = conn.execute('''SELECT a.*, u.name user_name, m.name manager_name FROM adjustment_requests a
                                JOIN users u ON u.id=a.user_id LEFT JOIN users m ON m.id=a.manager_id
                                ORDER BY a.created_at DESC''').fetchall()
    else:
        rows = conn.execute('SELECT * FROM adjustment_requests WHERE user_id=? ORDER BY created_at DESC', (user['id'],)).fetchall()
    conn.close()
    return templates.TemplateResponse(request=request, name='ajustes.html', context={'request': request, 'user':user,'rows':rows})

@app.post('/ajustes/novo')
def new_adjustment(request: Request, target_date: str = Form(...), event_type: str = Form(...), requested_time: str = Form(''), reason: str = Form(...), evidence: UploadFile|None = File(None)):
    user = require_user(request)
    path = None
    if evidence and evidence.filename:
        safe = f"evidencia_{user['id']}_{datetime.now().strftime('%Y%m%d%H%M%S')}_{Path(evidence.filename).name}"
        path = str(UPLOAD_DIR/safe)
        with open(path,'wb') as f: shutil.copyfileobj(evidence.file, f)
    conn = get_conn()
    cur = conn.execute('INSERT INTO adjustment_requests(user_id,target_date,event_type,requested_time,reason,evidence_path) VALUES(?,?,?,?,?,?)',
                        (user['id'], target_date, event_type, requested_time, reason, path))
    conn.commit(); aid = cur.lastrowid; conn.close()
    log(user['id'], 'SOLICITOU_AJUSTE', 'adjustment_requests', aid, f'{target_date} {event_type}', request)
    return redirect('/ajustes')

@app.post('/ajustes/{aid}/decidir')
def decide_adjustment(aid: int, request: Request, status: str = Form(...), manager_note: str = Form('')):
    user = require_role(request, ['gestor','admin'])
    if status not in ['aprovado','rejeitado']: raise HTTPException(400)
    conn = get_conn()
    conn.execute("UPDATE adjustment_requests SET status=?, manager_id=?, manager_note=?, decided_at=CURRENT_TIMESTAMP WHERE id=? AND status='pendente'",
                 (status, user['id'], manager_note, aid))
    conn.commit(); conn.close()
    log(user['id'], 'DECIDIU_AJUSTE', 'adjustment_requests', aid, status, request)
    return redirect('/ajustes')

@app.get('/folha', response_class=HTMLResponse)
def legacy_sheet_redirect(request: Request):
    require_user(request)
    return redirect('/folha-preenchida')

@app.get('/folha-preenchida', response_class=HTMLResponse)
def completed_sheet_page(request: Request):
    user = require_user(request)
    conn = get_conn()
    rows = conn.execute("""SELECT * FROM monthly_sheets
                           WHERE user_id=? AND COALESCE(doc_type,'preenchida_anterior')=?
                           ORDER BY month_ref DESC, version DESC""", (user['id'], DOC_PREVIOUS_COMPLETED)).fetchall()
    conn.close()
    return templates.TemplateResponse(request=request, name='folha_preenchida.html', context={'request': request, 'user':user,'rows':rows})

@app.get('/folha-corrente', response_class=HTMLResponse)
def current_sheet_page(request: Request):
    user = require_user(request)
    conn = get_conn()
    rows = conn.execute("""SELECT * FROM monthly_sheets
                           WHERE user_id=? AND COALESCE(doc_type,'preenchida_anterior')=?
                           ORDER BY month_ref DESC, version DESC""", (user['id'], DOC_CURRENT_BLANK)).fetchall()
    conn.close()
    return templates.TemplateResponse(request=request, name='folha_corrente.html', context={'request': request, 'user':user,'rows':rows})

@app.post('/folha-preenchida/upload')
def upload_completed_sheet(request: Request, month_ref: str = Form(...), manual_filled: str = Form(''), govbr_signed: str = Form(''), file: UploadFile = File(...)):
    user = require_user(request)
    if not file.filename.lower().endswith('.pdf'):
        raise HTTPException(status_code=400, detail='A folha preenchida deve ser anexada em PDF.')
    conn = get_conn()
    last = conn.execute('SELECT COALESCE(MAX(version),0) v FROM monthly_sheets WHERE user_id=? AND month_ref=? AND COALESCE(doc_type,?)=?', (user['id'], month_ref, DOC_PREVIOUS_COMPLETED, DOC_PREVIOUS_COMPLETED)).fetchone()['v']
    version = int(last or 0) + 1
    safe = f"folha_preenchida_{user['id']}_{month_ref}_v{version}_{Path(file.filename).name}".replace(' ', '_')
    path = str(UPLOAD_DIR/safe)
    with open(path,'wb') as f: shutil.copyfileobj(file.file, f)
    parsed = parse_pdf_sheet(path)
    release_month = release_month_for_sheet(month_ref)
    mf = 1 if manual_filled == '1' else 0
    gs = 1 if govbr_signed == '1' else 0
    status = 'pendente_aprovacao' if (mf and gs) else 'enviado_pendente_assinatura'
    conn.execute('UPDATE monthly_sheets SET active_version=0 WHERE user_id=? AND month_ref=? AND COALESCE(doc_type,?)=?', (user['id'], month_ref, DOC_PREVIOUS_COMPLETED, DOC_PREVIOUS_COMPLETED))
    cur = conn.execute("""INSERT INTO monthly_sheets
                    (user_id,month_ref,version,file_path,status,parsed_name,parsed_period,parsed_function,parsed_schedule,raw_text,manual_filled,govbr_signed,release_month_ref,active_version,doc_type,requires_manager_approval)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,1,?,1)""",
                 (user['id'], month_ref, version, path, status, parsed['parsed_name'], parsed['parsed_period'],
                  parsed['parsed_function'], parsed['parsed_schedule'], parsed['raw_text'], mf, gs, release_month, DOC_PREVIOUS_COMPLETED))
    sheet_id = cur.lastrowid
    content = Path(path).read_bytes()
    conn.execute('INSERT INTO file_blobs(sheet_id,filename,content_type,content,sha256) VALUES(?,?,?,?,?)',
                 (sheet_id, Path(path).name, 'application/pdf', content, hashlib.sha256(content).hexdigest()))
    managers = conn.execute("SELECT email FROM users WHERE role IN ('gestor','admin') AND active=1").fetchall()
    conn.commit(); conn.close()
    details = f"{path} | tipo={DOC_PREVIOUS_COMPLETED} | versão={version} | status={status} | período PDF: {parsed.get('parsed_period') or 'não identificado'} | jornada: {parsed.get('parsed_schedule') or 'não identificada'} | manual={mf} | govbr={gs} | libera={release_month}"
    log(user['id'], 'UPLOAD_FOLHA_PREENCHIDA_ANTERIOR', 'monthly_sheets', sheet_id, details, request)
    for m in managers:
        send_email(m['email'], f'Folha preenchida pendente de aprovação - {user["name"]} - {month_ref}',
                   f'O colaborador {user["name"]} anexou a folha preenchida da competência {month_ref} (versão {version}). Acesse o painel do gestor para validar, aprovar ou reprovar.')
    return redirect('/folha-preenchida')

@app.post('/folha-corrente/upload')
def upload_current_sheet(request: Request, month_ref: str = Form(...), file: UploadFile = File(...)):
    user = require_user(request)
    if not file.filename.lower().endswith('.pdf'):
        raise HTTPException(status_code=400, detail='A folha do mês corrente deve ser anexada em PDF.')
    conn = get_conn()
    last = conn.execute('SELECT COALESCE(MAX(version),0) v FROM monthly_sheets WHERE user_id=? AND month_ref=? AND COALESCE(doc_type,?)=?', (user['id'], month_ref, DOC_PREVIOUS_COMPLETED, DOC_CURRENT_BLANK)).fetchone()['v']
    version = int(last or 0) + 1
    safe = f"folha_corrente_{user['id']}_{month_ref}_v{version}_{Path(file.filename).name}".replace(' ', '_')
    path = str(UPLOAD_DIR/safe)
    with open(path,'wb') as f: shutil.copyfileobj(file.file, f)
    parsed = parse_pdf_sheet(path)
    conn.execute('UPDATE monthly_sheets SET active_version=0 WHERE user_id=? AND month_ref=? AND COALESCE(doc_type,?)=?', (user['id'], month_ref, DOC_PREVIOUS_COMPLETED, DOC_CURRENT_BLANK))
    cur = conn.execute("""INSERT INTO monthly_sheets
                    (user_id,month_ref,version,file_path,status,parsed_name,parsed_period,parsed_function,parsed_schedule,raw_text,manual_filled,govbr_signed,release_month_ref,active_version,doc_type,requires_manager_approval)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,1,?,0)""",
                 (user['id'], month_ref, version, path, 'arquivo_corrente', parsed['parsed_name'], parsed['parsed_period'],
                  parsed['parsed_function'], parsed['parsed_schedule'], parsed['raw_text'], 0, 0, None, DOC_CURRENT_BLANK))
    sheet_id = cur.lastrowid
    content = Path(path).read_bytes()
    conn.execute('INSERT INTO file_blobs(sheet_id,filename,content_type,content,sha256) VALUES(?,?,?,?,?)',
                 (sheet_id, Path(path).name, 'application/pdf', content, hashlib.sha256(content).hexdigest()))
    conn.commit(); conn.close()
    log(user['id'], 'UPLOAD_FOLHA_CORRENTE', 'monthly_sheets', sheet_id, f'{path} | tipo={DOC_CURRENT_BLANK} | versão={version} | sem aprovação obrigatória', request)
    return redirect('/folha-corrente')

@app.get('/folha/download/{sheet_id}')
def download_sheet(sheet_id: int, request: Request):
    user = require_user(request)
    conn = get_conn()
    if user['role'] in ('gestor','admin'):
        row = conn.execute('SELECT ms.*, u.name user_name FROM monthly_sheets ms JOIN users u ON u.id=ms.user_id WHERE ms.id=?', (sheet_id,)).fetchone()
    else:
        row = conn.execute('SELECT * FROM monthly_sheets WHERE id=? AND user_id=?', (sheet_id, user['id'])).fetchone()
    conn.close()
    if not row:
        raise HTTPException(status_code=404, detail='Folha não encontrada')
    path = Path(row['file_path'])
    content = None
    filename = path.name.replace(' ', '_')
    if path.exists():
        content = path.read_bytes()
    else:
        # No Render o filesystem pode ser efêmero. Quando DATABASE_URL/Supabase estiver ativo,
        # o PDF também fica persistido em file_blobs para não perder documentos em redeploy.
        conn = get_conn()
        blob = conn.execute('SELECT filename, content FROM file_blobs WHERE sheet_id=? ORDER BY id DESC LIMIT 1', (sheet_id,)).fetchone()
        conn.close()
        if blob:
            filename = (blob['filename'] or filename).replace(' ', '_')
            content = blob['content']
        else:
            raise HTTPException(status_code=404, detail='Arquivo físico não localizado e sem cópia persistida no banco')
    action = 'DOWNLOAD_FOLHA_CORRENTE' if row['doc_type'] == DOC_CURRENT_BLANK else 'DOWNLOAD_FOLHA_PREENCHIDA_ANTERIOR'
    log(user['id'], action, 'monthly_sheets', sheet_id, str(path), request)
    return Response(content, media_type='application/pdf', headers={'Content-Disposition': f'attachment; filename="{filename}"'})

@app.get('/gestor/folhas', response_class=HTMLResponse)
def manager_sheets_redirect(request: Request):
    require_role(request, ['gestor','admin'])
    return redirect('/gestor/folhas-preenchidas')

@app.get('/gestor/folhas-preenchidas', response_class=HTMLResponse)
def manager_completed_sheets(request: Request):
    user = require_role(request, ['gestor','admin'])
    conn = get_conn()
    rows = conn.execute("""SELECT ms.*, u.name user_name, u.email user_email
                           FROM monthly_sheets ms JOIN users u ON u.id=ms.user_id
                           WHERE COALESCE(ms.doc_type,'preenchida_anterior')=?
                           ORDER BY ms.month_ref DESC, u.name, ms.version DESC""", (DOC_PREVIOUS_COMPLETED,)).fetchall()
    conn.close()
    return templates.TemplateResponse(request=request, name='gestor_folhas_preenchidas.html', context={'request': request, 'user': user, 'rows': rows})

@app.get('/gestor/folhas-correntes', response_class=HTMLResponse)
def manager_current_sheets(request: Request):
    user = require_role(request, ['gestor','admin'])
    conn = get_conn()
    rows = conn.execute("""SELECT ms.*, u.name user_name, u.email user_email
                           FROM monthly_sheets ms JOIN users u ON u.id=ms.user_id
                           WHERE COALESCE(ms.doc_type,'preenchida_anterior')=?
                           ORDER BY ms.month_ref DESC, u.name, ms.version DESC""", (DOC_CURRENT_BLANK,)).fetchall()
    conn.close()
    return templates.TemplateResponse(request=request, name='gestor_folhas_correntes.html', context={'request': request, 'user': user, 'rows': rows})

@app.post('/gestor/folhas/{sheet_id}/decidir')
def decide_sheet(sheet_id: int, request: Request, status: str = Form(...), validation_note: str = Form(''), rejection_reason: str = Form('')):
    user = require_role(request, ['gestor','admin'])
    if status not in ['aprovado','reprovado']:
        raise HTTPException(status_code=400, detail='Status inválido')
    if status == 'reprovado' and not (rejection_reason or validation_note):
        raise HTTPException(status_code=400, detail='Informe o motivo da reprovação')
    conn = get_conn()
    row = conn.execute("""SELECT ms.*, u.name user_name, u.email user_email
                          FROM monthly_sheets ms JOIN users u ON u.id=ms.user_id
                          WHERE ms.id=?""", (sheet_id,)).fetchone()
    if not row:
        conn.close(); raise HTTPException(status_code=404, detail='Folha não encontrada')
    if row['doc_type'] == DOC_CURRENT_BLANK or not row['requires_manager_approval']:
        conn.close(); raise HTTPException(status_code=400, detail='A folha do mês corrente não exige aprovação do gestor.')
    conn.execute("""UPDATE monthly_sheets SET status=?, validated_by=?, validated_at=CURRENT_TIMESTAMP,
                    validation_note=?, rejection_reason=? WHERE id=?""",
                 (status, user['id'], validation_note, rejection_reason, sheet_id))
    if status == 'aprovado':
        conn.execute('UPDATE monthly_sheets SET active_version=0 WHERE user_id=? AND month_ref=? AND COALESCE(doc_type,?)=? AND id<>?', (row['user_id'], row['month_ref'], DOC_PREVIOUS_COMPLETED, DOC_PREVIOUS_COMPLETED, sheet_id))
        conn.execute('UPDATE monthly_sheets SET active_version=1 WHERE id=?', (sheet_id,))
    conn.commit(); conn.close()
    log(user['id'], 'VALIDOU_FOLHA_PREENCHIDA_ANTERIOR', 'monthly_sheets', sheet_id, f'{status} | motivo={rejection_reason} | obs={validation_note}', request)
    if status == 'aprovado':
        send_email(row['user_email'], f'Folha preenchida aprovada - {row["month_ref"]}',
                   f'Sua folha preenchida da competência {row["month_ref"]}, versão {row["version"]}, foi aprovada pelo gestor. Observação: {validation_note or "sem observações"}.')
    else:
        send_email(row['user_email'], f'Folha preenchida reprovada - {row["month_ref"]}',
                   f'Sua folha preenchida da competência {row["month_ref"]}, versão {row["version"]}, foi reprovada. Motivo: {rejection_reason or validation_note}. Faça os ajustes necessários e anexe uma nova versão no menu Folha Preenchida do Mês Anterior.')
    return redirect('/gestor/folhas-preenchidas')

@app.get('/meu-painel', response_class=HTMLResponse)
def my_panel(request: Request, mes: str | None = None):
    user = require_user(request)
    months = available_months_for_user(user['id'])
    month_ref = mes or months[0]
    panel = compute_month_panel(user['id'], month_ref)
    log(user['id'], 'CONSULTOU_PAINEL_INDIVIDUAL', 'monthly_panel', month_ref, request=request)
    return templates.TemplateResponse(request=request, name='painel_colaborador.html',
                                      context={'request': request, 'user': user, 'panel': panel, 'months': months})

@app.get('/gestor/colaborador/{employee_id}', response_class=HTMLResponse)
def manager_employee_panel(employee_id: int, request: Request, mes: str | None = None):
    user = require_role(request, ['gestor','admin'])
    months = available_months_for_user(employee_id)
    month_ref = mes or months[0]
    panel = compute_month_panel(employee_id, month_ref)
    if not panel['employee']:
        raise HTTPException(status_code=404, detail='Colaborador não encontrado')
    log(user['id'], 'CONSULTOU_PAINEL_COLABORADOR', 'monthly_panel', f"{employee_id}:{month_ref}", request=request)
    return templates.TemplateResponse(request=request, name='painel_colaborador.html',
                                      context={'request': request, 'user': user, 'panel': panel, 'months': months, 'gestor_view': True})

@app.get('/gestor', response_class=HTMLResponse)
def manager_dashboard(request: Request):
    user = require_role(request, ['gestor','admin'])
    conn = get_conn(); today = date.today().isoformat()
    users = conn.execute('SELECT id,name,email,role,active FROM users ORDER BY name').fetchall()
    recs = conn.execute('''SELECT tr.*, u.name FROM time_records tr JOIN users u ON u.id=tr.user_id
                           WHERE tr.record_date=? ORDER BY u.name,tr.server_time''', (today,)).fetchall()
    conn.close()
    return templates.TemplateResponse(request=request, name='gestor.html', context={'request': request, 'user':user,'users':users,'recs':recs,'today':today})

@app.get('/relatorio.csv')
def report_csv(request: Request, inicio: str, fim: str):
    user = require_role(request, ['gestor','admin'])
    conn = get_conn()
    rows = conn.execute('''SELECT u.name,u.email,tr.record_date,tr.event_type,tr.server_time,tr.ip
                           FROM time_records tr JOIN users u ON u.id=tr.user_id
                           WHERE tr.record_date BETWEEN ? AND ? ORDER BY u.name,tr.record_date,tr.server_time''', (inicio,fim)).fetchall()
    conn.close()
    out = io.StringIO(); w = csv.writer(out, delimiter=';')
    w.writerow(['Nome','Email','Data','Evento','Hora Servidor','IP'])
    for r in rows: w.writerow([r['name'],r['email'],r['record_date'],r['event_type'],r['server_time'],r['ip']])
    log(user['id'], 'GEROU_RELATORIO', 'time_records', None, f'{inicio} a {fim}', request)
    return Response(out.getvalue(), media_type='text/csv; charset=utf-8', headers={'Content-Disposition':'attachment; filename=relatorio_jornada.csv'})

@app.get('/admin', response_class=HTMLResponse)
def admin_page(request: Request):
    user = require_role(request, ['admin'])
    conn = get_conn(); users = conn.execute('SELECT * FROM users ORDER BY name').fetchall(); conn.close()
    return templates.TemplateResponse(request=request, name='admin.html', context={'request': request, 'user':user,'users':users})

@app.post('/admin/usuarios')
def create_user(request: Request, name: str=Form(...), email: str=Form(...), password: str=Form(...), role: str=Form(...)):
    user = require_role(request, ['admin'])
    email = email.lower().strip()
    if not email.endswith(CORPORATE_DOMAIN):
        raise HTTPException(status_code=400, detail='Cadastro permitido somente para e-mail corporativo configurado')
    conn = get_conn(); cur = conn.execute('INSERT INTO users(name,email,password_hash,role,must_change_password) VALUES(?,?,?,?,1)', (name,email,hash_password(password),role)); conn.commit(); uid=cur.lastrowid; conn.close()
    log(user['id'], 'CRIOU_USUARIO', 'users', uid, email, request)
    return redirect('/admin')



@app.get('/alterar-senha', response_class=HTMLResponse)
def change_password_page(request: Request):
    user = require_user(request)
    return templates.TemplateResponse(request=request, name='alterar_senha.html', context={'request': request, 'user': user})

@app.post('/alterar-senha')
def change_password(request: Request, current_password: str = Form(...), new_password: str = Form(...), confirm_password: str = Form(...)):
    user = require_user(request)
    if new_password != confirm_password:
        return templates.TemplateResponse(request=request, name='alterar_senha.html', context={'request': request, 'user': user, 'error': 'A confirmação não confere.'})
    if len(new_password) < 8:
        return templates.TemplateResponse(request=request, name='alterar_senha.html', context={'request': request, 'user': user, 'error': 'A senha deve ter pelo menos 8 caracteres.'})
    conn = get_conn(); row = conn.execute('SELECT * FROM users WHERE id=?', (user['id'],)).fetchone()
    if not row or not verify_password(current_password, row['password_hash']):
        conn.close()
        return templates.TemplateResponse(request=request, name='alterar_senha.html', context={'request': request, 'user': user, 'error': 'Senha atual inválida.'})
    hp = hash_password(new_password)
    conn.execute('UPDATE users SET password_hash=?, must_change_password=0, last_password_change=CURRENT_TIMESTAMP WHERE id=?', (hp, user['id']))
    conn.execute('INSERT INTO password_history(user_id,password_hash) VALUES(?,?)', (user['id'], hp))
    conn.commit(); conn.close()
    log(user['id'], 'ALTEROU_SENHA_PROPRIA', 'users', user['id'], request=request)
    return templates.TemplateResponse(request=request, name='alterar_senha.html', context={'request': request, 'user': user, 'success': 'Senha alterada com sucesso.'})

@app.post('/admin/usuarios/{uid}/resetar-senha')
def admin_reset_password(uid: int, request: Request, new_password: str = Form(...)):
    user = require_role(request, ['admin'])
    if len(new_password) < 8:
        raise HTTPException(status_code=400, detail='Senha deve ter pelo menos 8 caracteres')
    hp = hash_password(new_password)
    conn = get_conn()
    conn.execute('UPDATE users SET password_hash=?, must_change_password=1 WHERE id=?', (hp, uid))
    conn.execute('INSERT INTO password_history(user_id,password_hash) VALUES(?,?)', (uid, hp))
    conn.commit(); conn.close()
    log(user['id'], 'RESETOU_SENHA_USUARIO', 'users', uid, request=request)
    return redirect('/admin')

@app.get('/notificacoes', response_class=HTMLResponse)
def notifications_page(request: Request):
    user = require_user(request)
    conn = get_conn()
    rows = conn.execute('SELECT * FROM notifications WHERE user_id=? ORDER BY created_at DESC LIMIT 100', (user['id'],)).fetchall()
    conn.close()
    return templates.TemplateResponse(request=request, name='notificacoes.html', context={'request': request, 'user': user, 'rows': rows})

@app.post('/admin/processar-emails')
def admin_process_emails(request: Request):
    user = require_role(request, ['admin'])
    result = process_email_queue()
    log(user['id'], 'PROCESSOU_FILA_EMAIL', 'email_queue', details=str(result), request=request)
    return redirect('/admin')

@app.get('/auditoria', response_class=HTMLResponse)
def audit_page(request: Request):
    user = require_role(request, ['gestor','admin'])
    conn = get_conn(); rows = conn.execute('''SELECT a.*, u.name FROM audit_logs a LEFT JOIN users u ON u.id=a.user_id ORDER BY a.created_at DESC LIMIT 500''').fetchall(); conn.close()
    return templates.TemplateResponse(request=request, name='auditoria.html', context={'request': request, 'user':user,'rows':rows})
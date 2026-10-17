import importlib
from datetime import date

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv('DATABASE_URL', 'sqlite:///'+str(tmp_path/'test.db'))
    monkeypatch.setenv('UPLOAD_DIR', str(tmp_path/'receipts'))
    monkeypatch.setenv('ADMIN_EMAIL', 'admin@example.com')
    monkeypatch.setenv('ADMIN_PASSWORD', 'Admin-test-2026!')
    monkeypatch.delenv('SMTP_HOST', raising=False)
    from app import main
    main.engine.dispose()
    m = importlib.reload(main)
    m.attempts.clear()
    with TestClient(m.app) as client:
        assert client.post('/api/auth/login', json={'email':'admin@example.com', 'password':'Admin-test-2026!'}).status_code == 200
        yield client, m
    m.engine.dispose()


def setup_family(client, email='parent@example.com'):
    r = client.post('/api/guardians', json={'name':'Responsável Teste','email':email,'phone':'16999999999','password':'Parent-test-2026!'})
    assert r.status_code == 201, r.text
    gid = r.json()['id']
    r = client.post('/api/students', json={'name':'Aluno Teste','school':'Escola Municipal','shift':'Manhã','guardian_id':gid})
    assert r.status_code == 201, r.text
    sid = r.json()['id']
    year = date.today().year
    r = client.post('/api/contracts', json={'student_id':sid,'monthly_cents':35000,'discount_cents':5000,'due_day':31,'start':f'{year}-01-01','end':f'{year}-12-31'})
    assert r.status_code == 201, r.text
    return gid, sid, r.json()['id']


def invoice(client):
    return client.get('/api/invoices').json()[0]


def parent(m, email='parent@example.com'):
    c = TestClient(m.app)
    r = c.post('/api/auth/login', json={'email':email,'password':'Parent-test-2026!'})
    assert r.status_code == 200
    return c


def test_scope_and_roles(env):
    c,m = env
    setup_family(c)
    own = invoice(c)
    setup_family(c,'other@example.com')
    other = invoice(c)
    p = parent(m)
    assert len(p.get('/api/invoices').json()) == 1
    assert len(p.get('/api/students').json()) == 1
    assert p.get('/api/guardians').status_code == 403
    assert p.get('/api/reports/export').status_code == 403
    assert p.post('/api/invoices/'+str(own['id'])+'/payments',json={'amount_cents':1,'date':str(m.today()),'method':'Pix'}).status_code == 403
    assert p.post('/api/invoices/'+str(other['id'])+'/receipts',data={'amount_cents':1,'paid_on':str(m.today())},files={'file':('test.pdf',b'%PDF-1.7 test','application/pdf')}).status_code == 404


def test_month_generation_unique_and_short_month(env):
    c,m = env
    _,_,cid = setup_family(c)
    month = f'{m.today().year}-02'
    r = c.post('/api/invoices/generate',json={'month':month})
    assert r.status_code == 200
    assert c.post('/api/invoices/generate',json={'month':month}).json()['created'] == 0
    inv = next(i for i in c.get('/api/invoices').json() if i['month']==month)
    assert int(inv['due'][-2:]) in (28,29)
    assert inv['amount_cents'] == 30000
    c.patch('/api/contracts/'+str(cid),json={'status':'paused'})
    c.post('/api/invoices/generate',json={'month':f'{m.today().year}-03'})
    assert not any(i['month'].endswith('-03') for i in c.get('/api/invoices').json()) or m.today().month == 3


def test_partial_overpayment_and_reversal(env):
    c,m = env
    setup_family(c)
    i = invoice(c)
    path = '/api/invoices/'+str(i['id'])+'/payments'
    payload = {'amount_cents':10000,'date':str(m.today()),'method':'Dinheiro'}
    r = c.post(path,json=payload)
    assert r.status_code == 201
    pid = r.json()['id']
    assert invoice(c)['balance_cents'] == 20000
    assert c.post(path,json={**payload,'amount_cents':20001}).status_code == 409
    assert c.post('/api/payments/'+str(pid)+'/reverse',json={'reason':'Registro incorreto'}).status_code == 200
    assert invoice(c)['balance_cents'] == 30000
    assert c.post('/api/payments/'+str(pid)+'/reverse',json={'reason':'Repetido'}).status_code == 409
    assert c.get('/api/payments').json()[0]['reversed']
    assert len(c.get('/api/audit').json()) >= 3


def test_receipt_requires_confirmation_and_once_only(env):
    c,m = env
    setup_family(c)
    i = invoice(c)
    p = parent(m)
    path = '/api/invoices/'+str(i['id'])+'/receipts'
    r = p.post(path,data={'amount_cents':30000,'paid_on':str(m.today())},files={'file':('proof.pdf',b'%PDF-1.7 test','application/pdf')})
    assert r.status_code == 201, r.text
    rid = r.json()['id']
    assert invoice(c)['paid_cents'] == 0
    assert invoice(c)['status'] == 'review'
    assert p.post('/api/receipts/'+str(rid)+'/review',json={'approve':True}).status_code == 403
    assert c.post('/api/receipts/'+str(rid)+'/review',json={'approve':True}).status_code == 200
    assert invoice(c)['status'] == 'paid'
    assert c.post('/api/receipts/'+str(rid)+'/review',json={'approve':True}).status_code == 409
    setup_family(c,'other@example.com')
    other = parent(m,'other@example.com')
    assert other.get('/api/receipts/'+str(rid)+'/file').status_code == 404
    assert p.get('/api/receipts/'+str(rid)+'/file').status_code == 200
    assert any('confirmado' in n['message'] for n in p.get('/api/notices').json())


def test_receipt_rejection_resubmit_and_validation(env):
    c,m = env
    setup_family(c)
    i = invoice(c)
    p = parent(m)
    path = '/api/invoices/'+str(i['id'])+'/receipts'
    data = {'amount_cents':20000,'paid_on':str(m.today())}
    assert p.post(path,data=data,files={'file':('bad.html',b'<script>alert(1)</script>','image/png')}).status_code == 400
    assert p.post(path,data=data,files={'file':('big.pdf',b'%PDF-'+b'a'*(5*1024*1024),'application/pdf')}).status_code == 413
    rid = p.post(path,data=data,files={'file':('proof.pdf',b'%PDF-1.7','application/pdf')}).json()['id']
    assert p.post(path,data=data,files={'file':('proof.pdf',b'%PDF-1.7','application/pdf')}).status_code == 409
    assert c.post('/api/receipts/'+str(rid)+'/review',json={'approve':False}).status_code == 400
    assert c.post('/api/receipts/'+str(rid)+'/review',json={'approve':False,'note':'Pagamento não localizado'}).status_code == 200
    assert p.post(path,data=data,files={'file':('proof.pdf',b'%PDF-1.7','application/pdf')}).status_code == 201
    assert invoice(c)['paid_cents'] == 0


def test_invalid_contracts_and_overlap(env):
    c,m = env
    _,sid,_ = setup_family(c)
    payload={'student_id':sid,'monthly_cents':10000,'discount_cents':10000,'due_day':10,'start':str(m.today()),'end':str(m.today())}
    assert c.post('/api/contracts',json=payload).status_code == 422
    assert c.post('/api/contracts',json={**payload,'discount_cents':0}).status_code == 409
    assert c.post('/api/invoices/generate',json={'month':'2026-99'}).status_code == 422


def test_cancel_and_disable(env):
    c,m = env
    gid,_,_ = setup_family(c)
    p=parent(m)
    i=invoice(c)
    assert c.post('/api/invoices/'+str(i['id'])+'/cancel',json={'reason':'Cobrança indevida'}).status_code == 200
    assert invoice(c)['status']=='cancelled'
    assert c.post('/api/invoices/'+str(i['id'])+'/payments',json={'amount_cents':100,'date':str(m.today()),'method':'Pix'}).status_code==409
    c.patch('/api/guardians/'+str(gid),json={'name':'Nome Teste','phone':'','active':False})
    assert p.get('/api/auth/me').status_code == 401
    assert invoice(c)['status']=='cancelled'


def test_csrf_auth_password_and_logout(env):
    c,m=env
    assert c.post('/api/auth/logout',headers={'Origin':'https://evil.example'}).status_code==403
    assert c.post('/api/auth/password',json={'current_password':'wrong','new_password':'New-password-2026'}).status_code==400
    assert c.post('/api/auth/password',json={'current_password':'Admin-test-2026!','new_password':'New-password-2026'}).status_code==200
    assert c.get('/api/auth/me').status_code==401
    assert c.post('/api/auth/login',json={'email':'admin@example.com','password':'New-password-2026'}).status_code==200
    cookie=c.cookies.get('school_session')
    with m.SessionLocal() as s:
        assert s.get(m.Session,m.token_hash(cookie))
        assert not s.get(m.Session,cookie)
    c.post('/api/auth/logout')
    assert c.get('/api/auth/me').status_code==401


def test_csv_injection_and_no_smtp(env):
    c,m=env
    gid,_,_=setup_family(c)
    c.patch('/api/guardians/'+str(gid),json={'name':'=SUM(1+1)','phone':'','active':True})
    report=c.get('/api/reports/export')
    assert report.status_code==200
    assert "'=SUM(1+1)" in report.text
    assert c.post('/api/auth/forgot',json={'email':'any@example.com'}).status_code==503


def test_reset_token_expiry_and_one_time(env):
    c,m=env
    from datetime import datetime,timedelta,timezone
    raw='test-reset-token'
    with m.SessionLocal() as s:
        uid=s.scalar(m.select(m.User.id).where(m.User.role=='admin'))
        s.add(m.ResetToken(token_hash=m.token_hash(raw),user_id=uid,expires=datetime.now(timezone.utc).replace(tzinfo=None)+timedelta(minutes=5)))
        s.commit()
    assert c.post('/api/auth/reset',json={'token':raw,'password':'Reset-password-2026'}).status_code==200
    assert c.get('/api/auth/me').status_code==401
    assert c.post('/api/auth/reset',json={'token':raw,'password':'Reset-password-2026'}).status_code==400


def test_login_rate_limit(env):
    c,m=env
    m.attempts.clear()
    for _ in range(10):
        assert c.post('/api/auth/login',json={'email':'no@example.com','password':'wrong'}).status_code==401
    assert c.post('/api/auth/login',json={'email':'no@example.com','password':'wrong'}).status_code==429

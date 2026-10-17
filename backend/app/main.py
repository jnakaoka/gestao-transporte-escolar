import asyncio
import calendar
import csv
import io
import logging
import os
import re
import secrets
import smtplib
import time
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from email.message import EmailMessage
from pathlib import Path
from typing import Annotated, Literal
from zoneinfo import ZoneInfo

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import create_engine, event, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session as DBSession, sessionmaker

from .models import Audit, Base, Contract, Invoice, Notice, Payment, Receipt, ResetToken, Session, Settings, Student, User
from .security import hash_password, token_hash, verify_password

DATABASE_URL = os.getenv('DATABASE_URL', 'sqlite:///./school.db')
engine = create_engine(DATABASE_URL, connect_args={'check_same_thread': False} if DATABASE_URL.startswith('sqlite') else {}, pool_pre_ping=True)
if DATABASE_URL.startswith('sqlite'):
    @event.listens_for(engine, 'connect')
    def sqlite_fk(connection, _):
        connection.execute('PRAGMA foreign_keys=ON')
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
UPLOAD_DIR = Path(os.getenv('UPLOAD_DIR', './uploads'))
ORIGIN = os.getenv('APP_ORIGIN', 'http://localhost:8080').rstrip('/')
SECURE_COOKIE = os.getenv('COOKIE_SECURE', 'false').lower() == 'true'
logger = logging.getLogger('school')


def today():
    return datetime.now(ZoneInfo('America/Sao_Paulo')).date()


def db():
    with SessionLocal() as session:
        yield session


DB = Annotated[DBSession, Depends(db)]


def audit(s, actor, action, detail):
    s.add(Audit(actor_id=actor.id if actor else None, action=action, detail=str(detail)))


def initialize():
    Base.metadata.create_all(engine)
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    with SessionLocal() as s:
        if not s.get(Settings, 1):
            s.add(Settings(id=1))
        if not s.scalar(select(User).where(User.role == 'admin')):
            password = os.getenv('ADMIN_PASSWORD', '')
            email = os.getenv('ADMIN_EMAIL', '').strip().lower()
            if len(password) < 12 or '@' not in email:
                raise RuntimeError('Configure ADMIN_EMAIL e ADMIN_PASSWORD (mínimo 12 caracteres).')
            s.add(User(name='Administrador', email=email, password=hash_password(password), role='admin'))
        s.commit()


def generate_month(s, month):
    first = date.fromisoformat(month + '-01')
    last = date(first.year, first.month, calendar.monthrange(first.year, first.month)[1])
    count = 0
    for c in s.scalars(select(Contract).where(Contract.status == 'active', Contract.start <= last, Contract.end >= first)):
        student = s.get(Student, c.student_id)
        if not student.active or not s.get(User, student.guardian_id).active:
            continue
        if s.scalar(select(Invoice.id).where(Invoice.contract_id == c.id, Invoice.month == month)):
            continue
        try:
            with s.begin_nested():
                s.add(Invoice(contract_id=c.id, month=month, due=date(first.year, first.month, min(c.due_day, last.day)), amount_cents=c.monthly_cents-c.discount_cents))
                s.flush()
            count += 1
        except IntegrityError:
            pass  # The unique constraint also protects concurrent scheduler runs.
    return count


async def monthly_scheduler():
    while True:
        try:
            with SessionLocal() as s:
                generate_month(s, today().strftime('%Y-%m'))
                s.commit()
        except Exception:
            logger.exception('Falha na geração automática de mensalidades')
        await asyncio.sleep(3600)


@asynccontextmanager
async def lifespan(app):
    initialize()
    task = asyncio.create_task(monthly_scheduler())
    yield
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


app = FastAPI(title='Gestão de Transporte Escolar', lifespan=lifespan)


@app.middleware('http')
async def security_headers(request: Request, call_next):
    # Requests that mutate cookie-authenticated data must come from the app origin.
    if request.method not in ('GET', 'HEAD', 'OPTIONS'):
        origin = request.headers.get('origin')
        if (origin and origin != ORIGIN) or request.headers.get('sec-fetch-site') == 'cross-site':
            return Response('Origem não permitida', status_code=403)
    response = await call_next(request)
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['Referrer-Policy'] = 'same-origin'
    response.headers['Cache-Control'] = 'no-store'
    return response


def current_user(request: Request, s: DB):
    token = request.cookies.get('school_session', '')
    session = s.get(Session, token_hash(token))
    user = s.get(User, session.user_id) if session and session.expires > datetime.now(timezone.utc).replace(tzinfo=None) else None
    if not user or not user.active:
        raise HTTPException(401, 'Entre na sua conta para continuar.')
    return user


Current = Annotated[User, Depends(current_user)]


def admin(user: Current):
    if user.role != 'admin':
        raise HTTPException(403, 'Acesso exclusivo da administração.')
    return user


Admin = Annotated[User, Depends(admin)]


def user_json(u):
    return {'id': u.id, 'name': u.name, 'email': u.email, 'phone': u.phone, 'role': u.role, 'active': u.active}


def student_json(st):
    return {k: getattr(st, k) for k in ('id', 'guardian_id', 'name', 'school', 'shift', 'route', 'active')}


def invoice_access(s, invoice_id, user, lock=False):
    query = select(Invoice).where(Invoice.id == invoice_id)
    if lock:
        query = query.with_for_update()
    inv = s.scalar(query)
    if not inv:
        raise HTTPException(404, 'Mensalidade não encontrada.')
    c = s.get(Contract, inv.contract_id)
    st = s.get(Student, c.student_id)
    if user.role != 'admin' and st.guardian_id != user.id:
        raise HTTPException(404, 'Mensalidade não encontrada.')
    return inv, st


def invoice_json(s, inv):
    c = s.get(Contract, inv.contract_id)
    st = s.get(Student, c.student_id)
    guardian = s.get(User, st.guardian_id)
    pending = bool(s.scalar(select(Receipt.id).where(Receipt.invoice_id == inv.id, Receipt.status == 'pending')))
    balance = inv.amount_cents-inv.paid_cents
    status = 'cancelled' if inv.cancelled else 'paid' if balance == 0 else 'review' if pending else 'overdue' if inv.due < today() else 'partial' if inv.paid_cents else 'pending'
    return {'id': inv.id, 'contract_id': c.id, 'student': st.name, 'guardian': guardian.name, 'guardian_id': guardian.id,
            'phone': guardian.phone, 'month': inv.month, 'due': str(inv.due), 'amount_cents': inv.amount_cents,
            'paid_cents': inv.paid_cents, 'balance_cents': balance, 'status': status,
            'overdue': not inv.cancelled and balance > 0 and inv.due < today()}


def scoped_invoices(s, user):
    q = select(Invoice).join(Contract, Contract.id == Invoice.contract_id).join(Student, Student.id == Contract.student_id)
    if user.role != 'admin':
        q = q.where(Student.guardian_id == user.id)
    return list(s.scalars(q.order_by(Invoice.due.desc(), Invoice.id.desc())))


class LoginIn(BaseModel):
    email: str = Field(max_length=254)
    password: str = Field(min_length=1, max_length=128)


class GuardianIn(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    email: str = Field(min_length=3, max_length=254, pattern=r'^[^\s@]+@[^\s@]+\.[^\s@]+$')
    phone: str = Field(default='', max_length=30)
    password: str = Field(min_length=12, max_length=128)


class StudentIn(BaseModel):
    guardian_id: int
    name: str = Field(min_length=2, max_length=120)
    school: str = Field(min_length=2, max_length=160)
    shift: Literal['Manhã', 'Tarde', 'Integral', 'Noite']
    route: str = Field(default='', max_length=120)


class ContractIn(BaseModel):
    student_id: int
    monthly_cents: int = Field(gt=0, le=100_000_000)
    discount_cents: int = Field(default=0, ge=0)
    due_day: int = Field(ge=1, le=31)
    start: date
    end: date

    @model_validator(mode='after')
    def valid(self):
        if self.end < self.start or self.discount_cents >= self.monthly_cents:
            raise ValueError('Confira o período e o desconto do contrato.')
        return self


class ContractPatch(BaseModel):
    status: Literal['active', 'paused', 'ended']


class MonthIn(BaseModel):
    month: str = Field(pattern=r'^[1-9]\d{3}-(0[1-9]|1[0-2])$')


class PaymentIn(BaseModel):
    amount_cents: int = Field(gt=0, le=100_000_000)
    date: date
    method: Literal['Pix', 'Dinheiro', 'Transferência', 'Cartão', 'Outro'] = 'Pix'


class ReviewIn(BaseModel):
    approve: bool
    note: str = Field(default='', max_length=500)


class ReasonIn(BaseModel):
    reason: str = Field(min_length=3, max_length=500)


class SettingsIn(BaseModel):
    company: str = Field(min_length=2, max_length=120)
    pix_key: str = Field(default='', max_length=200)
    pix_holder: str = Field(default='', max_length=120)


# Deliberately single-worker V1. For multiple replicas, move throttling to a shared store.
attempts = {}


def throttle(key):
    now = time.monotonic()
    for old in list(attempts):
        if attempts[old][1] < now-900:
            del attempts[old]
    count, start = attempts.get(key, (0, now))
    if now-start > 900:
        count, start = 0, now
    if count >= 10:
        raise HTTPException(429, 'Muitas tentativas. Aguarde 15 minutos.')
    attempts[key] = (count+1, start)


@app.get('/api/health')
def health(s: DB):
    s.execute(select(1))
    return {'status': 'ok'}


@app.post('/api/auth/login')
def login(data: LoginIn, request: Request, response: Response, s: DB):
    throttle(('login', request.client.host if request.client else 'unknown'))
    user = s.scalar(select(User).where(User.email == data.email.strip().lower()))
    # Do a password hash even for unknown accounts to limit timing differences.
    valid = verify_password(data.password, user.password) if user else verify_password(data.password, DUMMY_HASH)
    if not user or not user.active or not valid:
        raise HTTPException(401, 'E-mail ou senha incorretos.')
    raw = secrets.token_urlsafe(48)
    s.add(Session(token_hash=token_hash(raw), user_id=user.id, expires=datetime.now(timezone.utc).replace(tzinfo=None)+timedelta(days=7)))
    audit(s, user, 'login', 'Sessão iniciada')
    s.commit()
    response.set_cookie('school_session', raw, httponly=True, secure=SECURE_COOKIE, samesite='lax', max_age=604800, path='/')
    return user_json(user)


DUMMY_HASH = hash_password(secrets.token_urlsafe(24))


@app.get('/api/auth/me')
def me(user: Current):
    return user_json(user)


@app.post('/api/auth/logout')
def logout(request: Request, response: Response, s: DB):
    session = s.get(Session, token_hash(request.cookies.get('school_session', '')))
    if session:
        s.delete(session)
        s.commit()
    response.delete_cookie('school_session', path='/')
    return {'ok': True}


class PasswordIn(BaseModel):
    current_password: str = Field(max_length=128)
    new_password: str = Field(min_length=12, max_length=128)


@app.post('/api/auth/password')
def change_password(data: PasswordIn, user: Current, s: DB):
    if not verify_password(data.current_password, user.password):
        raise HTTPException(400, 'Senha atual incorreta.')
    user.password = hash_password(data.new_password)
    for session in s.scalars(select(Session).where(Session.user_id == user.id)):
        s.delete(session)
    audit(s, user, 'password_change', 'Sessões encerradas')
    s.commit()
    return {'ok': True}


class EmailIn(BaseModel):
    email: str = Field(max_length=254)


@app.post('/api/auth/forgot')
def forgot(data: EmailIn, request: Request, s: DB):
    throttle(('reset', request.client.host if request.client else 'unknown'))
    if not os.getenv('SMTP_HOST'):
        raise HTTPException(503, 'Recuperação por e-mail indisponível. Contate a administração.')
    user = s.scalar(select(User).where(User.email == data.email.strip().lower(), User.active == True))
    if user:
        raw = secrets.token_urlsafe(48)
        s.add(ResetToken(token_hash=token_hash(raw), user_id=user.id, expires=datetime.now(timezone.utc).replace(tzinfo=None)+timedelta(minutes=30)))
        message = EmailMessage()
        message['Subject'] = 'Redefinir senha — Transporte Escolar'
        message['From'] = os.environ['SMTP_FROM']
        message['To'] = user.email
        message.set_content(f'Use este link em até 30 minutos para redefinir sua senha:\n{ORIGIN}/?reset={raw}\n\nSe não solicitou, ignore esta mensagem.')
        try:
            with smtplib.SMTP(os.environ['SMTP_HOST'], int(os.getenv('SMTP_PORT', '587')), timeout=10) as smtp:
                smtp.starttls()
                if os.getenv('SMTP_USER'):
                    smtp.login(os.environ['SMTP_USER'], os.environ['SMTP_PASSWORD'])
                smtp.send_message(message)
        except Exception:
            s.rollback()
            logger.error('Falha no serviço SMTP de recuperação')
            raise HTTPException(503, 'Serviço de e-mail temporariamente indisponível.')
        s.commit()
    return {'message': 'Se o e-mail estiver cadastrado, você receberá um link.'}


class ResetIn(BaseModel):
    token: str = Field(max_length=128)
    password: str = Field(min_length=12, max_length=128)


@app.post('/api/auth/reset')
def reset(data: ResetIn, s: DB):
    rt = s.scalar(select(ResetToken).where(ResetToken.token_hash == token_hash(data.token)).with_for_update())
    if not rt or rt.expires <= datetime.now(timezone.utc).replace(tzinfo=None):
        raise HTTPException(400, 'Link inválido ou expirado.')
    user = s.get(User, rt.user_id)
    user.password = hash_password(data.password)
    for item in s.scalars(select(ResetToken).where(ResetToken.user_id == user.id)):
        s.delete(item)
    for item in s.scalars(select(Session).where(Session.user_id == user.id)):
        s.delete(item)
    audit(s, user, 'password_reset', 'Senha redefinida')
    s.commit()
    return {'ok': True}


@app.get('/api/guardians')
def guardians(user: Admin, s: DB):
    return [user_json(u) for u in s.scalars(select(User).where(User.role == 'guardian').order_by(User.name))]


@app.post('/api/guardians', status_code=201)
def create_guardian(data: GuardianIn, user: Admin, s: DB):
    u = User(name=data.name.strip(), email=data.email.strip().lower(), phone=data.phone, password=hash_password(data.password), role='guardian')
    s.add(u)
    audit(s, user, 'guardian_create', data.email)
    try:
        s.commit()
    except IntegrityError:
        s.rollback()
        raise HTTPException(409, 'Este e-mail já está cadastrado.')
    return user_json(u)


class GuardianPatch(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    phone: str = Field(max_length=30)
    active: bool


@app.patch('/api/guardians/{id}')
def update_guardian(id: int, data: GuardianPatch, user: Admin, s: DB):
    u = s.get(User, id)
    if not u or u.role != 'guardian':
        raise HTTPException(404, 'Responsável não encontrado.')
    for key, value in data.model_dump().items():
        setattr(u, key, value)
    if not u.active:
        for session in s.scalars(select(Session).where(Session.user_id == u.id)):
            s.delete(session)
    audit(s, user, 'guardian_update', id)
    s.commit()
    return user_json(u)


@app.get('/api/students')
def students(user: Current, s: DB):
    q = select(Student).order_by(Student.name)
    if user.role != 'admin':
        q = q.where(Student.guardian_id == user.id)
    return [student_json(st) for st in s.scalars(q)]


@app.post('/api/students', status_code=201)
def create_student(data: StudentIn, user: Admin, s: DB):
    guardian = s.get(User, data.guardian_id)
    if not guardian or guardian.role != 'guardian' or not guardian.active:
        raise HTTPException(400, 'Escolha um responsável ativo.')
    st = Student(**data.model_dump())
    s.add(st)
    audit(s, user, 'student_create', data.name)
    s.commit()
    return student_json(st)


class ActiveIn(BaseModel):
    active: bool


@app.patch('/api/students/{id}')
def student_active(id: int, data: ActiveIn, user: Admin, s: DB):
    st = s.get(Student, id)
    if not st:
        raise HTTPException(404, 'Aluno não encontrado.')
    st.active = data.active
    audit(s, user, 'student_active', f'{id}: {data.active}')
    s.commit()
    return student_json(st)


@app.get('/api/contracts')
def contracts(user: Current, s: DB):
    q = select(Contract).join(Student, Student.id == Contract.student_id)
    if user.role != 'admin':
        q = q.where(Student.guardian_id == user.id)
    return [{**{key: getattr(c, key) for key in ('id', 'student_id', 'monthly_cents', 'discount_cents', 'due_day', 'start', 'end', 'status')}, 'student': s.get(Student, c.student_id).name} for c in s.scalars(q.order_by(Contract.id.desc()))]


@app.post('/api/contracts', status_code=201)
def create_contract(data: ContractIn, user: Admin, s: DB):
    st = s.scalar(select(Student).where(Student.id == data.student_id).with_for_update())
    if not st or not st.active:
        raise HTTPException(400, 'Escolha um aluno ativo.')
    # Contracts for one student cannot overlap, including paused contracts.
    overlap = s.scalar(select(Contract.id).where(Contract.student_id == st.id, Contract.start <= data.end, Contract.end >= data.start))
    if overlap:
        raise HTTPException(409, 'Já existe um contrato neste período para o aluno.')
    c = Contract(**data.model_dump())
    s.add(c)
    s.flush()
    generate_month(s, today().strftime('%Y-%m'))
    audit(s, user, 'contract_create', c.id)
    s.commit()
    return {'id': c.id}


@app.patch('/api/contracts/{id}')
def patch_contract(id: int, data: ContractPatch, user: Admin, s: DB):
    c = s.get(Contract, id)
    if not c:
        raise HTTPException(404, 'Contrato não encontrado.')
    c.status = data.status
    audit(s, user, 'contract_status', f'{id}: {data.status}')
    s.commit()
    return {'ok': True}


@app.post('/api/invoices/generate')
def generate(data: MonthIn, user: Admin, s: DB):
    count = generate_month(s, data.month)
    audit(s, user, 'invoices_generate', f'{data.month}: {count}')
    s.commit()
    return {'created': count}


@app.get('/api/invoices')
def invoices(user: Current, s: DB):
    return [invoice_json(s, inv) for inv in scoped_invoices(s, user)]


def record_payment(s, inv, st, amount, paid_on, method, user):
    if paid_on > today():
        raise HTTPException(400, 'A data do pagamento não pode ser futura.')
    if inv.cancelled or amount <= 0 or amount > inv.amount_cents-inv.paid_cents:
        raise HTTPException(409, 'Valor superior ao saldo, ou mensalidade cancelada.')
    inv.paid_cents += amount
    p = Payment(invoice_id=inv.id, amount_cents=amount, date=paid_on, method=method, actor_id=user.id)
    s.add(p)
    s.add(Notice(user_id=st.guardian_id, message=f'Pagamento de R$ {amount/100:.2f} confirmado para {st.name}, mensalidade {inv.month}.'))
    audit(s, user, 'payment_create', f'invoice={inv.id}, cents={amount}')
    return p


@app.post('/api/invoices/{id}/payments', status_code=201)
def pay(id: int, data: PaymentIn, user: Admin, s: DB):
    inv, st = invoice_access(s, id, user, lock=True)
    p = record_payment(s, inv, st, data.amount_cents, data.date, data.method, user)
    s.commit()
    return {'id': p.id}


@app.post('/api/invoices/{id}/cancel')
def cancel(id: int, data: ReasonIn, user: Admin, s: DB):
    inv, st = invoice_access(s, id, user, lock=True)
    if inv.paid_cents or s.scalar(select(Receipt.id).where(Receipt.invoice_id == id, Receipt.status == 'pending')):
        raise HTTPException(409, 'Estorne pagamentos e confira os comprovantes antes de cancelar.')
    inv.cancelled = True
    audit(s, user, 'invoice_cancel', f'{id}: {data.reason}')
    s.add(Notice(user_id=st.guardian_id, message=f'Mensalidade {inv.month} de {st.name} cancelada: {data.reason}'))
    s.commit()
    return {'ok': True}


@app.get('/api/payments')
def payments(user: Current, s: DB):
    allowed = {i.id for i in scoped_invoices(s, user)}
    return [{key: getattr(p, key) for key in ('id', 'invoice_id', 'amount_cents', 'date', 'method', 'reversed')} for p in s.scalars(select(Payment).order_by(Payment.id.desc())) if p.invoice_id in allowed]


@app.post('/api/payments/{id}/reverse')
def reverse(id: int, data: ReasonIn, user: Admin, s: DB):
    initial = s.get(Payment, id)
    if not initial:
        raise HTTPException(404, 'Pagamento não encontrado.')
    inv, st = invoice_access(s, initial.invoice_id, user, lock=True)
    p = s.scalar(select(Payment).where(Payment.id == id).with_for_update().execution_options(populate_existing=True))
    if p.reversed:
        raise HTTPException(409, 'Este pagamento já foi estornado.')
    p.reversed = True
    inv.paid_cents -= p.amount_cents
    audit(s, user, 'payment_reverse', f'{id}: {data.reason}')
    s.add(Notice(user_id=st.guardian_id, message=f'Pagamento da mensalidade {inv.month} estornado: {data.reason}'))
    s.commit()
    return {'ok': True}


@app.post('/api/invoices/{id}/receipts', status_code=201)
async def upload_receipt(id: int, user: Current, s: DB, amount_cents: int = Form(...), paid_on: date = Form(...), file: UploadFile = File(...)):
    inv, st = invoice_access(s, id, user, lock=True)
    if inv.cancelled or amount_cents <= 0 or amount_cents > inv.amount_cents-inv.paid_cents or paid_on > today():
        raise HTTPException(400, 'Confira o valor, a data e o saldo da mensalidade.')
    if s.scalar(select(Receipt.id).where(Receipt.invoice_id == id, Receipt.status == 'pending')):
        raise HTTPException(409, 'Já existe um comprovante em análise nesta mensalidade.')
    data = await file.read(5*1024*1024+1)
    if len(data) > 5*1024*1024:
        raise HTTPException(413, 'O comprovante deve ter até 5 MB.')
    mime, ext = ('application/pdf', '.pdf') if data.startswith(b'%PDF-') else ('image/png', '.png') if data.startswith(b'\x89PNG\r\n\x1a\n') else ('image/jpeg', '.jpg') if data.startswith(b'\xff\xd8\xff') else (None, None)
    if not mime:
        raise HTTPException(400, 'Envie um arquivo PDF, JPG ou PNG válido.')
    name = secrets.token_hex(24)+ext
    path = UPLOAD_DIR/name
    path.write_bytes(data)
    receipt = Receipt(invoice_id=id, user_id=user.id, storage_name=name, original_name=Path(file.filename or 'comprovante').name[:200], mime=mime, amount_cents=amount_cents, paid_on=paid_on)
    s.add(receipt)
    audit(s, user, 'receipt_upload', id)
    try:
        s.commit()
    except Exception:
        path.unlink(missing_ok=True)
        raise
    return {'id': receipt.id}


@app.get('/api/receipts')
def receipts(user: Current, s: DB):
    allowed = {i.id for i in scoped_invoices(s, user)}
    return [{key: getattr(r, key) for key in ('id', 'invoice_id', 'original_name', 'amount_cents', 'paid_on', 'status', 'note')} for r in s.scalars(select(Receipt).order_by(Receipt.id.desc())) if r.invoice_id in allowed]


@app.get('/api/receipts/{id}/file')
def receipt_file(id: int, user: Current, s: DB):
    r = s.get(Receipt, id)
    if not r:
        raise HTTPException(404, 'Comprovante não encontrado.')
    invoice_access(s, r.invoice_id, user)
    path = UPLOAD_DIR/r.storage_name
    if not path.is_file():
        raise HTTPException(404, 'Arquivo indisponível. Contate a administração.')
    return FileResponse(path, media_type=r.mime, filename=r.original_name, content_disposition_type='attachment')


@app.post('/api/receipts/{id}/review')
def review(id: int, data: ReviewIn, user: Admin, s: DB):
    initial = s.get(Receipt, id)
    if not initial:
        raise HTTPException(404, 'Comprovante não encontrado.')
    inv, st = invoice_access(s, initial.invoice_id, user, lock=True)
    r = s.scalar(select(Receipt).where(Receipt.id == id).with_for_update().execution_options(populate_existing=True))
    if r.status != 'pending':
        raise HTTPException(409, 'Este comprovante já foi analisado.')
    if data.approve:
        record_payment(s, inv, st, r.amount_cents, r.paid_on, 'Pix', user)
    else:
        if not data.note.strip():
            raise HTTPException(400, 'Informe o motivo da recusa.')
        s.add(Notice(user_id=st.guardian_id, message=f'Comprovante de {st.name}, {inv.month}, recusado: {data.note}'))
    r.status = 'approved' if data.approve else 'rejected'
    r.note = data.note
    audit(s, user, 'receipt_review', f'{id}: {r.status}')
    s.commit()
    return {'ok': True}


@app.get('/api/dashboard')
def dashboard(user: Current, s: DB):
    all_invoices = [invoice_json(s, i) for i in scoped_invoices(s, user)]
    active = [i for i in all_invoices if i['status'] != 'cancelled']
    month = today().strftime('%Y-%m')
    allowed = {i['id'] for i in active}
    received = sum(p.amount_cents for p in s.scalars(select(Payment).where(Payment.reversed == False)) if p.invoice_id in allowed and str(p.date).startswith(month))
    return {'month': month, 'received_cents': received, 'open_cents': sum(i['balance_cents'] for i in active),
            'overdue_cents': sum(i['balance_cents'] for i in active if i['overdue']),
            'review_count': sum(i['status'] == 'review' for i in active),
            'due_soon': sorted([i for i in active if i['balance_cents'] and date.fromisoformat(i['due']) <= today()+timedelta(days=7)], key=lambda i: i['due'])[:10]}


@app.get('/api/notices')
def notices(user: Current, s: DB):
    result = [{'id': f'n{n.id}', 'message': n.message, 'created': str(n.created.date())} for n in s.scalars(select(Notice).where(Notice.user_id == user.id).order_by(Notice.id.desc()).limit(100))]
    for inv in scoped_invoices(s, user):
        if not inv.cancelled and inv.paid_cents < inv.amount_cents and inv.due <= today()+timedelta(days=3):
            st = s.get(Student, s.get(Contract, inv.contract_id).student_id)
            result.insert(0, {'id': f'i{inv.id}', 'message': f'{st.name}: mensalidade {inv.month} '+('em atraso' if inv.due < today() else 'vence em '+inv.due.strftime('%d/%m'))+'.', 'created': str(today())})
    return result


@app.get('/api/settings')
def settings(user: Current, s: DB):
    item = s.get(Settings, 1)
    return {key: getattr(item, key) for key in ('company', 'pix_key', 'pix_holder')}


@app.put('/api/settings')
def save_settings(data: SettingsIn, user: Admin, s: DB):
    item = s.get(Settings, 1)
    for key, value in data.model_dump().items():
        setattr(item, key, value)
    audit(s, user, 'settings_update', 'Dados de cobrança atualizados')
    s.commit()
    return {'ok': True}


@app.get('/api/reports/export')
def export(user: Admin, s: DB, month: str = ''):
    if month and not re.fullmatch(r'\d{4}-(0[1-9]|1[0-2])', month):
        raise HTTPException(400, 'Mês inválido.')
    output = io.StringIO()
    writer = csv.writer(output, delimiter=';')
    writer.writerow(['Responsável', 'Aluno', 'Competência', 'Vencimento', 'Valor', 'Pago', 'Saldo', 'Situação'])
    def safe(value):
        text = str(value)
        return "'"+text if text.startswith(('=', '+', '-', '@', '\t', '\r', '\n')) else text
    for inv in scoped_invoices(s, user):
        row = invoice_json(s, inv)
        if month and row['month'] != month:
            continue
        writer.writerow([safe(row['guardian']), safe(row['student']), row['month'], row['due'], *[f"{row[k]/100:.2f}".replace('.', ',') for k in ('amount_cents', 'paid_cents', 'balance_cents')], row['status']])
    return Response('\ufeff'+output.getvalue(), media_type='text/csv; charset=utf-8', headers={'Content-Disposition': 'attachment; filename="mensalidades.csv"'})


@app.get('/api/audit')
def audit_log(user: Admin, s: DB):
    return [{key: getattr(item, key) for key in ('id', 'actor_id', 'action', 'detail', 'created')} for item in s.scalars(select(Audit).order_by(Audit.id.desc()).limit(100))]

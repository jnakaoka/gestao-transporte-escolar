from datetime import date, datetime, timezone
from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = 'users'
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    email: Mapped[str] = mapped_column(String(254), unique=True)
    phone: Mapped[str] = mapped_column(String(30), default='')
    password: Mapped[str] = mapped_column(String(300))
    role: Mapped[str] = mapped_column(String(20), default='guardian')
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class Session(Base):
    __tablename__ = 'sessions'
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey('users.id'))
    expires: Mapped[datetime] = mapped_column(DateTime)


class ResetToken(Base):
    __tablename__ = 'reset_tokens'
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey('users.id'))
    expires: Mapped[datetime] = mapped_column(DateTime)


class Student(Base):
    __tablename__ = 'students'
    id: Mapped[int] = mapped_column(primary_key=True)
    guardian_id: Mapped[int] = mapped_column(ForeignKey('users.id'))
    name: Mapped[str] = mapped_column(String(120))
    school: Mapped[str] = mapped_column(String(160))
    shift: Mapped[str] = mapped_column(String(30))
    route: Mapped[str] = mapped_column(String(120), default='')
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class Contract(Base):
    __tablename__ = 'contracts'
    id: Mapped[int] = mapped_column(primary_key=True)
    student_id: Mapped[int] = mapped_column(ForeignKey('students.id'))
    monthly_cents: Mapped[int] = mapped_column(Integer)
    discount_cents: Mapped[int] = mapped_column(Integer, default=0)
    due_day: Mapped[int] = mapped_column(Integer)
    start: Mapped[date] = mapped_column(Date)
    end: Mapped[date] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(20), default='active')


class Invoice(Base):
    __tablename__ = 'invoices'
    __table_args__ = (UniqueConstraint('contract_id', 'month', name='uq_contract_month'),)
    id: Mapped[int] = mapped_column(primary_key=True)
    contract_id: Mapped[int] = mapped_column(ForeignKey('contracts.id'))
    month: Mapped[str] = mapped_column(String(7))
    due: Mapped[date] = mapped_column(Date)
    amount_cents: Mapped[int] = mapped_column(Integer)
    paid_cents: Mapped[int] = mapped_column(Integer, default=0)
    cancelled: Mapped[bool] = mapped_column(Boolean, default=False)


class Payment(Base):
    __tablename__ = 'payments'
    id: Mapped[int] = mapped_column(primary_key=True)
    invoice_id: Mapped[int] = mapped_column(ForeignKey('invoices.id'))
    amount_cents: Mapped[int] = mapped_column(Integer)
    date: Mapped[date] = mapped_column(Date)
    method: Mapped[str] = mapped_column(String(30))
    actor_id: Mapped[int] = mapped_column(ForeignKey('users.id'))
    reversed: Mapped[bool] = mapped_column(Boolean, default=False)


class Receipt(Base):
    __tablename__ = 'receipts'
    id: Mapped[int] = mapped_column(primary_key=True)
    invoice_id: Mapped[int] = mapped_column(ForeignKey('invoices.id'))
    user_id: Mapped[int] = mapped_column(ForeignKey('users.id'))
    storage_name: Mapped[str] = mapped_column(String(100))
    original_name: Mapped[str] = mapped_column(String(200))
    mime: Mapped[str] = mapped_column(String(50))
    amount_cents: Mapped[int] = mapped_column(Integer)
    paid_on: Mapped[date] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(20), default='pending')
    note: Mapped[str] = mapped_column(String(500), default='')


class Notice(Base):
    __tablename__ = 'notices'
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey('users.id'))
    message: Mapped[str] = mapped_column(String(500))
    created: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None))


class Audit(Base):
    __tablename__ = 'audit'
    id: Mapped[int] = mapped_column(primary_key=True)
    actor_id: Mapped[int | None] = mapped_column(ForeignKey('users.id'), nullable=True)
    action: Mapped[str] = mapped_column(String(100))
    detail: Mapped[str] = mapped_column(Text)
    created: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None))


class Settings(Base):
    __tablename__ = 'settings'
    id: Mapped[int] = mapped_column(primary_key=True, default=1)
    company: Mapped[str] = mapped_column(String(120), default='Minha Van Escolar')
    pix_key: Mapped[str] = mapped_column(String(200), default='')
    pix_holder: Mapped[str] = mapped_column(String(120), default='')

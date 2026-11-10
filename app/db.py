"""SQLAlchemy models and session handling. Works on PostgreSQL and SQLite."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import date, datetime, timezone

from sqlalchemy import JSON, Boolean, Date, DateTime, Float, ForeignKey, Integer, String, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker

from app.config import settings

_connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
engine = create_engine(settings.database_url, connect_args=_connect_args, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


class Supplier(Base):
    __tablename__ = "suppliers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200), unique=True, index=True)
    country: Mapped[str] = mapped_column(String(80))
    distance_km: Mapped[float] = mapped_column(Float)
    years_active: Mapped[int] = mapped_column(Integer)
    certifications: Mapped[str] = mapped_column(String(200), default="")

    orders: Mapped[list["PurchaseOrder"]] = relationship(back_populates="supplier")


class PurchaseOrder(Base):
    """Historical purchase orders: the training data for the delivery-risk model."""

    __tablename__ = "purchase_orders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    supplier_id: Mapped[int] = mapped_column(ForeignKey("suppliers.id"), index=True)
    category: Mapped[str] = mapped_column(String(60), index=True)
    order_date: Mapped[date] = mapped_column(Date, index=True)
    quantity: Mapped[int] = mapped_column(Integer)
    unit_price_usd: Mapped[float] = mapped_column(Float)
    shipping_mode: Mapped[str] = mapped_column(String(10))
    promised_lead_days: Mapped[int] = mapped_column(Integer)
    actual_lead_days: Mapped[int] = mapped_column(Integer)
    late: Mapped[bool] = mapped_column(Boolean)
    defect_rate: Mapped[float] = mapped_column(Float)

    supplier: Mapped[Supplier] = relationship(back_populates="orders")


class ComparisonRun(Base):
    __tablename__ = "comparison_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    item: Mapped[str] = mapped_column(String(200))
    quantity: Mapped[int] = mapped_column(Integer)
    recommended_supplier: Mapped[str] = mapped_column(String(200), default="")
    result: Mapped[dict] = mapped_column(JSON)


def init_db() -> None:
    Base.metadata.create_all(engine)


@contextmanager
def session_scope():
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

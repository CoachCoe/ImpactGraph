from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from .config import Settings


def create_session_factory(database_url: str | None = None) -> sessionmaker[Session]:
    url = database_url or Settings.from_env().database_url
    engine = create_engine(url, pool_pre_ping=True)
    return sessionmaker(bind=engine, expire_on_commit=False, class_=Session)


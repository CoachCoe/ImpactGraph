"""Fixtures shared by the tests that drive an in-memory database directly.

A program row is part of the minimum realistic state: evidence registration resolves the
program it commits against and refuses one the registry has not confirmed, so a test that
creates evidence without a program is testing a situation that cannot occur.
"""

from __future__ import annotations

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from impactgraph.persistence import Base, ProgramRecord

SHOWCASE_PROGRAM = "program-clean-water-kenya-2026"


def program_row(slug: str = SHOWCASE_PROGRAM, *, chain_status: str = "CONFIRMED") -> ProgramRecord:
    return ProgramRecord(
        slug=slug,
        name="Clean Water Kenya",
        operator_name="Global Water Initiative",
        operator_org_ref="org-global-water",
        region="Kisumu County",
        status="ACTIVE",
        chain_status=chain_status,
    )


def _engine() -> Engine:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    return engine


def memory_session(**program) -> Session:
    engine = _engine()
    session = Session(engine)
    with session.begin():
        session.add(program_row(**program))
    return session


def memory_factory(**program) -> sessionmaker[Session]:
    factory = sessionmaker(_engine(), expire_on_commit=False)
    with factory.begin() as session:
        session.add(program_row(**program))
    return factory

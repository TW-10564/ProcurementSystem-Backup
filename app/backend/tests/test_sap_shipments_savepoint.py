"""Regression test — a failure inside the SAP-shipments ingestion step
must not discard the OTHER work already staged in the same uncommitted
build.run() session (master/④/①/sub-files/SAP-stock, all added via
session.add() but not committed until run()'s single final commit).
Found live 2026-07-15: the first real rebuild against unfiltered NAS data
crashed on a duplicate-item collision (see test_sap_shipments.py), and
because the failure handler used a plain session.rollback(), it would
have discarded that entire session's accumulated work, not just the
shipments portion — build.py now wraps the step in a SAVEPOINT
(session.begin_nested()) instead. This test proves the SAVEPOINT
mechanism itself works, without needing live NAS access."""
from datetime import date

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from onefcst.models import Base, Item


@pytest.fixture()
def session():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    yield s
    s.close()


def test_nested_transaction_failure_preserves_earlier_uncommitted_work(session):
    earlier_item = Item(canonical_name="Earlier Work", kind="part")
    session.add(earlier_item)   # simulates master/④/①/sub-file/SAP-stock staging

    with pytest.raises(IntegrityError):
        with session.begin_nested():
            # simulates the SAP-shipments step failing partway through
            session.add(Item(canonical_name="Partial", kind="part", id=1))
            session.add(Item(canonical_name="Duplicate PK", kind="part", id=1))
            session.flush()   # triggers the PK collision inside the SAVEPOINT

    # the SAVEPOINT rolled back only the failed step's own writes —
    # earlier_item, added before the SAVEPOINT began, must still be staged
    session.commit()
    assert session.query(Item).filter_by(canonical_name="Earlier Work").count() == 1
    assert session.query(Item).filter_by(canonical_name="Partial").count() == 0

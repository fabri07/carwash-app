from app.application.idempotency import claim_idempotency_key


async def test_claim_nuevo_y_replay(db_session, tenant_a, tenant_b):
    assert await claim_idempotency_key(db_session, tenant_a.id, "k", "x") is True
    assert await claim_idempotency_key(db_session, tenant_a.id, "k", "x") is False
    assert await claim_idempotency_key(db_session, tenant_b.id, "k", "x") is True

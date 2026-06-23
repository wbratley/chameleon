import pytest

from chameleon_relay.queue import MessageQueue

SAMPLE = b"From: test@example.com\r\n\r\nHello"


@pytest.fixture
async def queue():
    q = MessageQueue(":memory:")
    await q.setup()
    yield q
    await q.close()


async def test_enqueue_returns_incrementing_ids(queue):
    id1 = await queue.enqueue(SAMPLE)
    id2 = await queue.enqueue(SAMPLE)
    assert id2 > id1


async def test_pending_returns_undelivered_in_insertion_order(queue):
    id1 = await queue.enqueue(b"first")
    id2 = await queue.enqueue(b"second")
    rows = await queue.pending()
    assert [(r[0], r[1]) for r in rows] == [(id1, b"first"), (id2, b"second")]


async def test_ack_removes_from_pending(queue):
    msg_id = await queue.enqueue(SAMPLE)
    await queue.ack(msg_id)
    assert await queue.pending() == []


async def test_ack_does_not_remove_other_messages(queue):
    id1 = await queue.enqueue(b"keep me")
    id2 = await queue.enqueue(b"ack me")
    await queue.ack(id2)
    pending = await queue.pending()
    assert len(pending) == 1
    assert pending[0][0] == id1


async def test_sweep_deletes_old_undelivered(queue):
    msg_id = await queue.enqueue(SAMPLE)
    await queue._conn.execute(
        "UPDATE messages SET received_at = '2000-01-01T00:00:00.000Z' WHERE id = ?",
        (msg_id,),
    )
    await queue._conn.commit()
    deleted = await queue.sweep()
    assert deleted >= 1
    assert await queue.pending() == []


async def test_sweep_keeps_recent_pending(queue):
    await queue.enqueue(SAMPLE)
    deleted = await queue.sweep()
    assert deleted == 0
    assert len(await queue.pending()) == 1

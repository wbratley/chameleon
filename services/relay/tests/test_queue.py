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
    rows = [row async for row in queue.pending()]
    assert rows == [(id1, b"first"), (id2, b"second")]


async def test_pending_empty_on_fresh_queue(queue):
    assert [row async for row in queue.pending()] == []


async def test_pending_snapshot_excludes_rows_enqueued_mid_replay(queue):
    """Rows arriving during a replay must not be yielded by it — they are
    delivered by the live broadcast, so one connection never sees a message
    twice (issue #8)."""
    id1 = await queue.enqueue(b"first")
    replay = queue.pending()
    assert await replay.__anext__() == (id1, b"first")

    await queue.enqueue(b"enqueued mid-replay")

    with pytest.raises(StopAsyncIteration):
        await replay.__anext__()


async def test_pending_replay_tolerates_deletes_mid_replay(queue):
    """A sweep/ack removing not-yet-replayed rows mid-iteration must not
    break the replay: short keyset queries simply move on to the next
    surviving row."""
    id1 = await queue.enqueue(b"first")
    id2 = await queue.enqueue(b"second")
    id3 = await queue.enqueue(b"third")
    replay = queue.pending()
    assert await replay.__anext__() == (id1, b"first")

    await queue.ack(id2)  # swept/expired while the replay is in progress

    assert await replay.__anext__() == (id3, b"third")
    with pytest.raises(StopAsyncIteration):
        await replay.__anext__()


async def test_ack_removes_from_pending(queue):
    msg_id = await queue.enqueue(SAMPLE)
    await queue.ack(msg_id)
    assert [row async for row in queue.pending()] == []


async def test_ack_does_not_remove_other_messages(queue):
    id1 = await queue.enqueue(b"keep me")
    id2 = await queue.enqueue(b"ack me")
    await queue.ack(id2)
    pending = [row async for row in queue.pending()]
    assert len(pending) == 1
    assert pending[0][0] == id1


async def test_sweep_deletes_old_undelivered(queue):
    msg_id = await queue.enqueue(SAMPLE)
    await queue._conn.execute(
        "UPDATE messages SET received_at = '2000-01-01T00:00:00.000Z' WHERE id = ?",
        (msg_id,),
    )
    await queue._conn.commit()
    deleted = await queue.sweep(1440)
    assert deleted >= 1
    assert [row async for row in queue.pending()] == []


async def test_sweep_keeps_recent_pending(queue):
    await queue.enqueue(SAMPLE)
    deleted = await queue.sweep(1440)
    assert deleted == 0
    assert len([row async for row in queue.pending()]) == 1


async def test_sweep_honors_retain_minutes(queue):
    msg_id = await queue.enqueue(SAMPLE)
    # Age the message ~10 minutes.
    await queue._conn.execute(
        "UPDATE messages "
        "SET received_at = strftime('%Y-%m-%dT%H:%M:%fZ','now','-10 minutes') "
        "WHERE id = ?",
        (msg_id,),
    )
    await queue._conn.commit()
    # A 30-minute retention keeps it; a 5-minute retention sweeps it.
    assert await queue.sweep(30) == 0
    assert len([row async for row in queue.pending()]) == 1
    assert await queue.sweep(5) == 1
    assert [row async for row in queue.pending()] == []

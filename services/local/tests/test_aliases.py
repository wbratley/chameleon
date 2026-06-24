import re

import pytest

from chameleon_local.aliases import AliasDB

DOMAIN = "example.com"


async def test_create_generates_valid_address(alias_db):
    alias = await alias_db.create("Netflix", DOMAIN)
    assert re.match(r"^[a-z0-9-]+-[a-z0-9]{4}@example\.com$", alias.address)
    assert alias.service == "Netflix"
    assert not alias.burned
    assert alias.email_count == 0
    assert alias.last_received_at is None


async def test_create_uses_service_slug(alias_db):
    alias = await alias_db.create("Amazon Web Services", DOMAIN)
    local = alias.address.split("@")[0]
    assert local.startswith("amazon-web-service")


async def test_create_special_chars_in_service(alias_db):
    alias = await alias_db.create("HBO Max!", DOMAIN)
    local = alias.address.split("@")[0]
    assert re.match(r"^hbo-max-[a-z0-9]{4}$", local)


async def test_create_same_service_produces_unique_addresses(alias_db):
    a1 = await alias_db.create("Netflix", DOMAIN)
    a2 = await alias_db.create("Netflix", DOMAIN)
    assert a1.address != a2.address


async def test_all_returns_newest_first(alias_db):
    await alias_db.create("A", DOMAIN)
    await alias_db.create("B", DOMAIN)
    aliases = await alias_db.all()
    assert aliases[0].service == "B"
    assert aliases[1].service == "A"


async def test_all_empty(alias_db):
    assert await alias_db.all() == []


async def test_record_delivery_increments_count(alias_db):
    alias = await alias_db.create("Netflix", DOMAIN)
    result = await alias_db.record_delivery(alias.address)
    assert result is True
    updated = (await alias_db.all())[0]
    assert updated.email_count == 1
    assert updated.last_received_at is not None


async def test_record_delivery_increments_count_multiple_times(alias_db):
    alias = await alias_db.create("Netflix", DOMAIN)
    await alias_db.record_delivery(alias.address)
    await alias_db.record_delivery(alias.address)
    updated = (await alias_db.all())[0]
    assert updated.email_count == 2


async def test_record_delivery_returns_false_for_burned(alias_db):
    alias = await alias_db.create("Spam", DOMAIN)
    await alias_db.burn(alias.id)
    result = await alias_db.record_delivery(alias.address)
    assert result is False


async def test_record_delivery_does_not_increment_count_for_burned(alias_db):
    alias = await alias_db.create("Spam", DOMAIN)
    await alias_db.burn(alias.id)
    await alias_db.record_delivery(alias.address)
    updated = (await alias_db.all())[0]
    assert updated.email_count == 0


async def test_record_delivery_auto_registers_unknown_address(alias_db):
    result = await alias_db.record_delivery("unknown-xyz1@example.com")
    assert result is True
    aliases = await alias_db.all()
    assert len(aliases) == 1
    assert aliases[0].address == "unknown-xyz1@example.com"
    assert aliases[0].email_count == 1


async def test_burn_sets_burned_flag(alias_db):
    alias = await alias_db.create("Breach", DOMAIN)
    burned = await alias_db.burn(alias.id)
    assert burned.burned is True
    assert burned.burned_at is not None


async def test_burn_unknown_returns_none(alias_db):
    result = await alias_db.burn(9999)
    assert result is None


async def test_burn_is_idempotent(alias_db):
    alias = await alias_db.create("Test", DOMAIN)
    await alias_db.burn(alias.id)
    result = await alias_db.burn(alias.id)
    assert result.burned is True

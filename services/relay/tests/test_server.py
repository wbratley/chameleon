"""Guard against passing a kwarg aiosmtpd doesn't accept.

The relay used to pass `max_content_size` — a kwarg that exists in no
released aiosmtpd (the real name is `data_size_limit`). SMTP construction
happens in a thread spawned by Controller.start(), so the resulting TypeError
crashed the whole relay on startup, on the first real deployment.
"""

import inspect

from aiosmtpd.smtp import SMTP

from chameleon_relay.server import smtp_server_kwargs


def test_server_kwargs_are_real_smtp_parameters(settings):
    kwargs = smtp_server_kwargs(settings)
    accepted = inspect.signature(SMTP.__init__).parameters
    unknown = set(kwargs) - set(accepted)
    assert not unknown, (
        f"smtp_server_kwargs passes {unknown}, which "
        f"aiosmtpd {SMTP.__version__} SMTP.__init__ does not accept"
    )


def test_server_kwargs_limit_message_size(settings):
    # data_size_limit drives the SIZE extension and DATA enforcement; if this
    # key disappears, the relay silently stops limiting message size.
    kwargs = smtp_server_kwargs(settings)
    assert kwargs["data_size_limit"] == settings.MAX_MESSAGE_SIZE


def test_server_kwargs_set_banner_hostname(settings):
    # The 220 greeting / EHLO response must use the configured relay
    # identity, not socket.getfqdn() of the container (VPS internal name).
    from aiosmtpd.smtp import SMTP
    from unittest.mock import MagicMock

    smtp = SMTP(MagicMock(), **smtp_server_kwargs(settings))
    assert smtp.hostname == settings.RELAY_HOSTNAME

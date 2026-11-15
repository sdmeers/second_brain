"""The +brain poller only accepts mail from allow-listed senders."""

from email.message import EmailMessage

from backend.ingestion.gmail import sender_allowed

ALLOWED = ["me@example.org", "me@work.example"]


def _msg(sender: str, auth: str | None = None) -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = sender
    if auth:
        msg["Authentication-Results"] = auth
    return msg


def test_empty_list_accepts_anyone():
    assert sender_allowed(_msg("stranger@example.com"), []) == (True, "stranger@example.com")


def test_listed_sender_is_accepted_whatever_the_case_or_display_name():
    assert sender_allowed(_msg("Me <ME@Example.org>"), ALLOWED) == (True, "me@example.org")


def test_unlisted_sender_is_refused():
    assert sender_allowed(_msg("stranger@example.com"), ALLOWED)[0] is False


def test_lookalike_address_is_refused():
    assert sender_allowed(_msg("me@example.org.evil.example"), ALLOWED)[0] is False
    assert sender_allowed(_msg('"me@example.org" <x@evil.example>'), ALLOWED)[0] is False


def test_missing_from_is_refused():
    assert sender_allowed(EmailMessage(), ALLOWED)[0] is False


def test_forged_listed_sender_failing_dmarc_is_refused():
    msg = _msg("me@example.org", "mx.google.com; spf=fail; dmarc=fail (p=REJECT) header.from=example.org")
    assert sender_allowed(msg, ALLOWED)[0] is False


def test_listed_sender_passing_dmarc_is_accepted():
    msg = _msg("me@example.org", "mx.google.com; dkim=pass; dmarc=pass header.from=example.org")
    assert sender_allowed(msg, ALLOWED)[0] is True


def test_a_verdict_not_from_gmail_is_ignored():
    msg = _msg("me@example.org", "elsewhere.example; dmarc=fail")
    assert sender_allowed(msg, ALLOWED)[0] is True

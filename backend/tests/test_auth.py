from types import SimpleNamespace

from app import auth


def test_session_round_trip_and_tamper_rejection(monkeypatch):
    monkeypatch.setattr(
        auth,
        "settings",
        SimpleNamespace(
            admin_password="a-long-test-password",
            session_secret="test-session-secret-that-is-at-least-32-characters",
            session_ttl_seconds=3600,
        ),
    )
    token, csrf = auth.create_session()
    session = auth.verify_session_token(token)
    assert session is not None
    assert session["sub"] == "admin"
    assert session["csrf"] == csrf
    assert auth.verify_session_token(token + "tampered") is None


def test_auth_fails_closed_without_a_signing_secret(monkeypatch):
    monkeypatch.setattr(
        auth,
        "settings",
        SimpleNamespace(admin_password="configured", session_secret="too-short"),
    )
    assert auth.auth_configured() is False
    assert auth.verify_session_token("anything") is None

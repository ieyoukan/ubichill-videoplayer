import testing_env  # noqa: F401

import time
import unittest
from unittest.mock import AsyncMock, patch

import jwt
from fastapi.responses import Response
from fastapi.testclient import TestClient

from app.config import SERVICE_AUDIENCE, SERVICE_TOKEN_SECRET, SERVICE_TOKEN_TTL
from app.media_url import media_signature_valid, sign_media
from app.rate_limit import RateLimiter, parse_rate, search_limiter
from app.routers.session import session_limiter
from app.service_auth import (
    TOKEN_ISSUER, ServiceTokenError, issue_service_token, require_service_user,
    service_subject, verify_service_token,
)
from main import app

HOST = "198.51.100.1"


def token(secret=SERVICE_TOKEN_SECRET, **overrides) -> str:
    now = int(time.time())
    claims = {"iss": TOKEN_ISSUER, "aud": SERVICE_AUDIENCE,
              "sub": service_subject(HOST), "mod": "video-player",
              "iat": now, "exp": now + SERVICE_TOKEN_TTL, "jti": "j", **overrides}
    return jwt.encode(claims, secret, algorithm="HS256")


class VerifyServiceTokenTests(unittest.TestCase):
    def reason(self, raw: str) -> str:
        with self.assertRaises(ServiceTokenError) as ctx:
            verify_service_token(raw)
        return ctx.exception.reason

    def test_service_issued_token_is_valid_for_five_minutes(self):
        now = int(time.time())
        issued = issue_service_token(HOST, "video-player", now=now)
        user = verify_service_token(issued["token"])
        self.assertEqual((user.subject, user.mod), (service_subject(HOST), "video-player"))
        self.assertEqual(issued["expiresAt"], (now + 300) * 1000)
        self.assertNotIn(HOST, jwt.decode(issued["token"], options={"verify_signature": False})["sub"])

    def test_reissuing_for_same_ip_keeps_rate_limit_subject(self):
        first = issue_service_token(HOST, "video-player")["token"]
        second = issue_service_token(HOST, "video-player")["token"]
        self.assertNotEqual(first, second)
        self.assertEqual(verify_service_token(first).subject, verify_service_token(second).subject)
        other = issue_service_token("198.51.100.2", "video-player")["token"]
        self.assertNotEqual(verify_service_token(first).subject, verify_service_token(other).subject)

    def test_rejects_token_for_other_service(self):
        self.assertEqual(self.reason(token(aud="other-service")), "wrong-audience")

    def test_rejects_signature_from_other_secret(self):
        self.assertEqual(self.reason(token(secret="other-service-secret-at-least-32-bytes")), "bad-signature")

    def test_rejects_expired_token_without_grace_period(self):
        now = int(time.time())
        self.assertEqual(self.reason(token(iat=now - 400, exp=now - 1)), "expired")

    def test_rejects_other_mod_and_issuer(self):
        self.assertEqual(self.reason(token(mod="pen")), "mod-not-allowed")
        self.assertEqual(self.reason(token(iss="https://arbitrary-ubichill.example")), "invalid")

    def test_rejects_unsigned_token_and_garbage(self):
        claims = jwt.decode(token(), options={"verify_signature": False})
        self.assertEqual(self.reason(jwt.encode(claims, "", algorithm="none")), "invalid")
        self.assertEqual(self.reason("not.a.token"), "invalid")


class RateLimiterTests(unittest.TestCase):
    def test_allows_capacity_then_reports_wait_and_refills(self):
        clock = {"now": 0.0}
        limiter = RateLimiter(3, 60, clock=lambda: clock["now"])
        self.assertEqual([limiter.take("a") for _ in range(3)], [0.0, 0.0, 0.0])
        self.assertAlmostEqual(limiter.take("a"), 20.0)
        clock["now"] = 20.0
        self.assertEqual(limiter.take("a"), 0.0)

    def test_users_are_counted_separately(self):
        limiter = RateLimiter(1, 60)
        self.assertEqual(limiter.take("a"), 0.0)
        self.assertGreater(limiter.take("a"), 0)
        self.assertEqual(limiter.take("b"), 0.0)

    def test_forgets_oldest_users_beyond_max_keys(self):
        limiter = RateLimiter(1, 3600, max_keys=2)
        for key in ("a", "b", "c"):
            limiter.take(key)
        self.assertEqual(list(limiter._buckets), ["b", "c"])

    def test_parse_rate(self):
        self.assertEqual(parse_rate("30/600"), (30, 600.0))
        with self.assertRaises(ValueError):
            parse_rate("0/60")


class MediaUrlTests(unittest.TestCase):
    def test_signed_url_is_valid_only_for_same_media_and_before_expiry(self):
        signed = sign_media("video", "abc123", now=1000, ttl=60, secret="s")
        self.assertTrue(media_signature_valid("video", "abc123", signed["exp"], signed["sig"], now=1059, secret="s"))
        self.assertFalse(media_signature_valid("video", "abc123", signed["exp"], signed["sig"], now=1061, secret="s"))
        self.assertFalse(media_signature_valid("audio", "abc123", signed["exp"], signed["sig"], now=1000, secret="s"))
        self.assertFalse(media_signature_valid("video", "other1", signed["exp"], signed["sig"], now=1000, secret="s"))
        self.assertFalse(media_signature_valid("video", "abc123", signed["exp"], signed["sig"], now=1000, secret="t"))

    def test_rejects_extended_expiry_and_missing_values(self):
        signed = sign_media("video", "abc123", now=1000, ttl=60, secret="s")
        later = str(int(signed["exp"]) + 3600)
        self.assertFalse(media_signature_valid("video", "abc123", later, signed["sig"], now=1000, secret="s"))
        self.assertFalse(media_signature_valid("video", "abc123", None, signed["sig"], secret="s"))
        self.assertFalse(media_signature_valid("video", "abc123", "-1", signed["sig"], secret="s"))


class RouteProtectionTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app, client=(HOST, 5000))
        app.dependency_overrides.pop(require_service_user, None)
        search_limiter._buckets.clear()
        session_limiter._buckets.clear()

    def auth(self, raw=None):
        return {"authorization": f"Bearer {raw or token()}"}

    def test_any_host_can_start_mod_session_without_login(self):
        response = self.client.post("/session", json={"modId": "video-player"},
                                    headers={"origin": "https://my-ubichill.example"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["cache-control"], "no-store")
        user = verify_service_token(response.json()["token"])
        self.assertEqual(user.mod, "video-player")
        self.assertEqual(user.subject, service_subject(HOST))

    def test_unknown_mod_and_invalid_id_cannot_start_session(self):
        self.assertEqual(self.client.post("/session", json={"modId": "pen"}).status_code, 403)
        for body in ({}, {"modId": "../video-player"}, {"modId": "x" * 129}):
            self.assertEqual(self.client.post("/session", json=body).status_code, 422)

    def test_session_issuance_is_rate_limited_by_ip(self):
        while session_limiter.take(service_subject(HOST)) == 0:
            pass
        response = self.client.post("/session", json={"modId": "video-player"})
        self.assertEqual(response.status_code, 429)
        self.assertGreaterEqual(int(response.headers["retry-after"]), 1)

    def test_youtube_endpoints_require_service_token(self):
        for path in ("/search?q=a", "/info/w3vt4U13QYM", "/resolve/w3vt4U13QYM"):
            response = self.client.get(path)
            self.assertEqual(response.status_code, 401, path)
            self.assertEqual(response.headers.get("www-authenticate"), "Bearer")

    def test_invalid_token_reports_reason(self):
        response = self.client.get("/resolve/w3vt4U13QYM", headers=self.auth(token(aud="other-service")))
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["detail"]["reason"], "wrong-audience")

    def test_session_token_allows_resolve_and_signed_media_gateway(self):
        raw = self.client.post("/session", json={"modId": "video-player"}).json()["token"]
        response = self.client.get("/resolve/w3vt4U13QYM?mode=video&presentation=video", headers=self.auth(raw))
        self.assertEqual(response.status_code, 200)
        path = response.json()["source"]["url"].split("://", 1)[1].split("/", 1)[1]
        with patch("app.routers.video._stream_media", AsyncMock(return_value=Response(b"ok"))):
            self.assertEqual(self.client.get(f"/{path}").status_code, 200)
            self.assertEqual(self.client.get(f"/{path.split('?')[0]}").status_code, 403)
            tampered = f"/{path.replace('w3vt4U13QYM', 'dQw4w9WgXcQ')}"
            self.assertEqual(self.client.get(tampered).status_code, 403)

    def test_reissuing_or_borrowing_token_does_not_reset_api_rate_limit(self):
        while search_limiter.take(service_subject(HOST)) == 0:
            pass
        for source in (HOST, "198.51.100.2"):
            raw = issue_service_token(source, "video-player")["token"]
            response = self.client.get("/search?q=a", headers=self.auth(raw))
            self.assertEqual(response.status_code, 429)
            self.assertGreaterEqual(int(response.headers["retry-after"]), 1)

    def test_forwarded_header_does_not_override_client_ip_in_app(self):
        while search_limiter.take(service_subject(HOST)) == 0:
            pass
        response = self.client.get("/search?q=a", headers={**self.auth(), "x-forwarded-for": "198.51.100.99"})
        self.assertEqual(response.status_code, 429)

    def test_search_and_info_return_direct_thumbnail_urls(self):
        tracks = [{"id": "w3vt4U13QYM", "title": "t", "duration": 1, "author": "a"}]
        info = {"id": "w3vt4U13QYM", "title": "t", "duration": 1, "uploader": "a"}
        with patch("app.routers.search._run_ytdlp", AsyncMock(return_value=tracks)):
            search = self.client.get("/search?q=unique-query-for-test", headers=self.auth())
        with patch("app.routers.info._run_ytdlp", AsyncMock(return_value=info)):
            detail = self.client.get("/info/w3vt4U13QYM", headers=self.auth())
        expected = "https://i.ytimg.com/vi/w3vt4U13QYM/hqdefault.jpg"
        self.assertEqual(search.status_code, 200)
        self.assertEqual(search.json()[0]["thumbnail"], expected)
        self.assertEqual(detail.json()["thumbnail"], expected)

    def test_deprecated_public_endpoints_are_removed(self):
        for path in ("/video/w3vt4U13QYM", "/audio/w3vt4U13QYM", "/live/w3vt4U13QYM",
                     "/live-audio/w3vt4U13QYM", "/thumbnail/w3vt4U13QYM", "/proxy?url=x"):
            self.assertEqual(self.client.get(path).status_code, 404, path)


if __name__ == "__main__":
    unittest.main()

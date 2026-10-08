import testing_env  # noqa: F401  app より先に環境変数を決める

import time
import unittest
from unittest.mock import AsyncMock, patch

import jwt
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.responses import Response
from fastapi.testclient import TestClient

from app.media_url import media_signature_valid, sign_media
from app.rate_limit import RateLimiter, parse_rate, search_limiter
from app.service_auth import ServiceTokenError, require_service_user, verify_service_token
from main import app

ISSUER = "https://ubichill.test"
AUDIENCE = "https://videoplayer.test"
KEY = Ed25519PrivateKey.generate()


def token(key=KEY, alg="EdDSA", **overrides) -> str:
    now = int(time.time())
    claims = {"iss": ISSUER, "aud": AUDIENCE, "sub": "pseudonym-1", "mod": "video-player",
              "iat": now, "exp": now + 300, "jti": "j", **overrides}
    return jwt.encode(claims, key, algorithm=alg, headers={"kid": "k1"})


def verify(raw: str, key_for=None):
    return verify_service_token(
        raw,
        issuers=[ISSUER],
        audience=AUDIENCE,
        allowed_mods=["video-player"],
        key_for=key_for or (lambda _issuer, _token: KEY.public_key()),
    )


class VerifyServiceTokenTests(unittest.TestCase):
    def test_accepts_valid_token_and_returns_pseudonym(self):
        user = verify(token())
        self.assertEqual((user.subject, user.mod), ("pseudonym-1", "video-player"))

    def reason(self, raw: str, key_for=None) -> str:
        with self.assertRaises(ServiceTokenError) as ctx:
            verify(raw, key_for)
        return ctx.exception.reason

    def test_rejects_token_for_other_service(self):
        self.assertEqual(self.reason(token(aud="https://other.test")), "wrong-audience")

    def test_untrusted_issuer_is_rejected_before_fetching_keys(self):
        asked = []
        reason = self.reason(token(iss="https://evil.test"), lambda issuer, _t: asked.append(issuer))
        self.assertEqual(reason, "untrusted-issuer")
        self.assertEqual(asked, [])

    def test_rejects_signature_from_other_key(self):
        self.assertEqual(self.reason(token(key=Ed25519PrivateKey.generate())), "bad-signature")

    def test_rejects_expired_beyond_clock_skew(self):
        now = int(time.time())
        self.assertEqual(self.reason(token(iat=now - 400, exp=now - 31)), "expired")

    def test_rejects_other_mod(self):
        self.assertEqual(self.reason(token(mod="pen")), "mod-not-allowed")

    def test_rejects_symmetric_algorithm(self):
        # 公開鍵を HMAC の鍵として使わせる「alg 差し替え」を受け付けない。
        self.assertEqual(self.reason(token(key="secret-secret-secret-secret-1234", alg="HS256")), "unsupported-algorithm")

    def test_rejects_garbage(self):
        self.assertEqual(self.reason("not.a.token"), "malformed")


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
        self.client = TestClient(app)
        app.dependency_overrides.pop(require_service_user, None)
        patcher = patch("app.service_auth._jwks_key", lambda _issuer, _token: KEY.public_key())
        patcher.start()
        self.addCleanup(patcher.stop)

    def auth(self, raw=None):
        return {"authorization": f"Bearer {raw or token()}"}

    def test_youtube_endpoints_require_service_token(self):
        for path in ("/search?q=a", "/info/w3vt4U13QYM", "/resolve/w3vt4U13QYM"):
            response = self.client.get(path)
            self.assertEqual(response.status_code, 401, path)
            self.assertEqual(response.headers.get("www-authenticate"), "Bearer")

    def test_invalid_token_reports_reason(self):
        response = self.client.get("/resolve/w3vt4U13QYM", headers=self.auth(token(aud="https://other.test")))
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["detail"]["reason"], "wrong-audience")

    def test_resolve_with_token_returns_signed_media_url_that_the_gateway_accepts(self):
        response = self.client.get("/resolve/w3vt4U13QYM?mode=video&presentation=video", headers=self.auth())
        self.assertEqual(response.status_code, 200)
        url = response.json()["source"]["url"]
        path = url.split("://", 1)[1].split("/", 1)[1]
        with patch("app.routers.video._stream_media", AsyncMock(return_value=Response(b"ok"))):
            self.assertEqual(self.client.get(f"/{path}").status_code, 200)
            unsigned = f"/{path.split('?')[0]}"
            self.assertEqual(self.client.get(unsigned).status_code, 403)
            tampered = f"/{path.replace('w3vt4U13QYM', 'dQw4w9WgXcQ')}"
            self.assertEqual(self.client.get(tampered).status_code, 403)

    def test_rate_limit_per_user_returns_429_with_retry_after(self):
        subject = "pseudonym-ratelimit"
        while search_limiter.take(subject) == 0:
            pass
        response = self.client.get("/search?q=a", headers=self.auth(token(sub=subject)))
        self.assertEqual(response.status_code, 429)
        self.assertGreaterEqual(int(response.headers["retry-after"]), 1)

    def test_search_and_info_return_direct_thumbnail_urls(self):
        tracks = [{"id": "w3vt4U13QYM", "title": "t", "duration": 1, "author": "a"}]
        info = {"id": "w3vt4U13QYM", "title": "t", "duration": 1, "uploader": "a"}
        with patch("app.routers.search._run_ytdlp", AsyncMock(return_value=tracks)):
            search = self.client.get("/search?q=unique-query-for-test", headers=self.auth(token(sub="thumb")))
        with patch("app.routers.info._run_ytdlp", AsyncMock(return_value=info)):
            detail = self.client.get("/info/w3vt4U13QYM", headers=self.auth(token(sub="thumb")))
        expected = "https://i.ytimg.com/vi/w3vt4U13QYM/hqdefault.jpg"
        self.assertEqual(search.status_code, 200)
        self.assertEqual(search.json()[0]["thumbnail"], expected)
        self.assertEqual(detail.json()["thumbnail"], expected)
        self.assertNotIn("streamUrl", detail.json())

    def test_deprecated_public_endpoints_are_removed(self):
        for path in ("/video/w3vt4U13QYM", "/audio/w3vt4U13QYM", "/live/w3vt4U13QYM",
                     "/live-audio/w3vt4U13QYM", "/thumbnail/w3vt4U13QYM", "/proxy?url=x"):
            self.assertEqual(self.client.get(path).status_code, 404, path)


if __name__ == "__main__":
    unittest.main()

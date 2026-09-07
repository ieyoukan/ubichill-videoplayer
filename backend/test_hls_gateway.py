import unittest
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from app.hls_gateway import StreamSession, create_stream, get_stream
from app.manifest import _rewrite_manifest_urls
from main import app


class ManifestRewriteTests(unittest.TestCase):
    def test_rewrites_variants_segments_keys_maps_and_query_urls(self):
        manifest = """#EXTM3U
#EXT-X-MEDIA:TYPE=AUDIO,URI="audio/playlist.m3u8?sig=one"
#EXT-X-KEY:METHOD=AES-128,URI="https://keys.youtube.com/key?id=two"
#EXT-X-MAP:URI="init.mp4?sig=three"
segment.m4s?sig=four
"""
        seen: list[str] = []

        def rewrite(url: str) -> str:
            seen.append(url)
            return f"/opaque/{len(seen)}"

        result = _rewrite_manifest_urls(manifest, "https://cdn.googlevideo.com/path/master.m3u8?x=1", rewrite)

        self.assertNotIn("googlevideo.com", result)
        self.assertNotIn("youtube.com", result)
        self.assertIn('URI="/opaque/1"', result)
        self.assertIn('URI="/opaque/2"', result)
        self.assertIn('URI="/opaque/3"', result)
        self.assertIn("/opaque/4", result)
        self.assertEqual(
            seen,
            [
                "https://cdn.googlevideo.com/path/audio/playlist.m3u8?sig=one",
                "https://keys.youtube.com/key?id=two",
                "https://cdn.googlevideo.com/path/init.mp4?sig=three",
                "https://cdn.googlevideo.com/path/segment.m4s?sig=four",
            ],
        )


class OpaqueSessionTests(unittest.TestCase):
    def test_resource_token_is_stable_and_does_not_contain_upstream_url(self):
        session = StreamSession("https://cdn.googlevideo.com/master.m3u8", {})
        upstream = "https://cdn.googlevideo.com/segment.m4s?signature=secret"
        first = session.register(upstream)
        second = session.register(upstream)
        self.assertEqual(first, second)
        self.assertNotIn("googlevideo", first)
        self.assertNotIn("secret", first)
        self.assertEqual(session.resolve(first), upstream)

    def test_created_stream_is_retrievable_by_opaque_id(self):
        stream_id = create_stream("https://cdn.googlevideo.com/master.m3u8")
        self.assertNotIn("googlevideo", stream_id)
        session = get_stream(stream_id)
        self.assertIsNotNone(session)
        self.assertEqual(session.master_url, "https://cdn.googlevideo.com/master.m3u8")


class PlaybackDescriptorTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    def test_vod_descriptor_only_contains_backend_url(self):
        sources = {
            "video_url": "https://video.googlevideo.com/video.m3u8?signature=secret",
            "audio_url": "https://audio.googlevideo.com/audio.m3u8?signature=secret",
            "headers": {},
        }
        with patch("app.routers.stream.resolve_vod_hls_sources", AsyncMock(return_value=sources)):
            response = self.client.get("/resolve/w3vt4U13QYM?mode=video&presentation=video")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["cache-control"], "no-store")
        source = response.json()["source"]
        self.assertEqual(source["type"], "hls")
        self.assertRegex(source["url"], r"/stream/[A-Za-z0-9_-]{20,64}/master\.m3u8$")
        self.assertNotIn("googlevideo", response.text)

        master = self.client.get(source["url"])
        self.assertEqual(master.status_code, 200)
        self.assertNotIn("googlevideo", master.text)
        self.assertNotIn("signature=secret", master.text)
        self.assertEqual(master.text.count("/resource/"), 2)
        self.assertEqual(master.text.count("/playlist.m3u8"), 2)

    def test_audio_only_descriptor_is_a_valid_opaque_master(self):
        sources = {
            "video_url": None,
            "audio_url": "https://audio.googlevideo.com/audio.m3u8?signature=secret",
            "headers": {},
        }
        with patch("app.routers.stream.resolve_vod_hls_sources", AsyncMock(return_value=sources)):
            response = self.client.get("/resolve/w3vt4U13QYM?mode=video&presentation=audio")
        master = self.client.get(response.json()["source"]["url"])
        self.assertIn("#EXT-X-STREAM-INF", master.text)
        self.assertIn("/resource/", master.text)
        self.assertNotIn("googlevideo", master.text)

    def test_live_descriptor_uses_opaque_stream_id(self):
        with patch(
            "app.routers.stream.resolve_live_url",
            AsyncMock(return_value="https://cdn.googlevideo.com/master.m3u8"),
        ):
            response = self.client.get("/resolve/w3vt4U13QYM?mode=live&presentation=video")
        self.assertEqual(response.status_code, 200)
        source = response.json()["source"]
        self.assertEqual(source["type"], "hls")
        self.assertRegex(source["url"], r"/stream/[A-Za-z0-9_-]{20,64}/master\.m3u8$")
        self.assertNotIn("googlevideo", response.text)

    def test_public_url_proxy_is_not_mounted(self):
        response = self.client.get("/proxy?url=https://cdn.googlevideo.com/segment.ts")
        self.assertEqual(response.status_code, 404)


if __name__ == "__main__":
    unittest.main()

import unittest
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException, Request

import main


class FakeUpstream:
    def __init__(self, status_code: int, body: bytes = b""):
        self.status_code = status_code
        self.headers = {"content-type": "video/mp4", "content-length": str(len(body))}
        self.body = body
        self.close_count = 0

    async def aclose(self):
        self.close_count += 1

    async def aiter_bytes(self, chunk_size: int):
        if self.body:
            yield self.body


class FakeClient:
    def __init__(self):
        self.close_count = 0

    async def aclose(self):
        self.close_count += 1


def stream_info(
    url: str,
    user_agent: str = "yt-dlp-agent",
    chunk_size: int = 10 * 1024 * 1024,
) -> main.VideoStreamInfo:
    return {
        "url": url,
        "http_headers": {
            "User-Agent": user_agent,
            "Accept": "text/html,*/*;q=0.8",
            "Accept-Language": "en-us,en;q=0.5",
            "Sec-Fetch-Mode": "navigate",
        },
        "chunk_size": chunk_size,
    }


def make_request(range_value: bytes = b"bytes=0-99") -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/video/w3vt4U13QYM",
            "headers": [(b"range", range_value)],
            "query_string": b"",
            "scheme": "https",
            "server": ("testserver", 443),
            "client": ("127.0.0.1", 1234),
        }
    )


async def consume(response) -> bytes:
    return b"".join([chunk async for chunk in response.body_iterator])


class StreamVideoRetryTests(unittest.IsolatedAsyncioTestCase):
    async def test_refreshes_url_and_retries_after_upstream_403(self):
        rejected = FakeUpstream(403)
        success = FakeUpstream(206, b"video")
        client = FakeClient()
        resolve = AsyncMock(
            side_effect=[
                stream_info(
                    "https://old.googlevideo.com/videoplayback",
                    user_agent="old-agent",
                ),
                stream_info(
                    "https://new.googlevideo.com/videoplayback",
                    user_agent="new-agent",
                ),
            ]
        )
        safe_get = AsyncMock(side_effect=[rejected, success])

        with (
            patch.object(main, "_resolve_video_url", resolve),
            patch.object(main, "_safe_get", safe_get),
            patch.object(main._video_url_cache, "delete") as cache_delete,
            patch.object(main.httpx, "AsyncClient", return_value=client),
        ):
            response = await main.stream_video("w3vt4U13QYM", make_request())

        self.assertEqual(response.status_code, 206)
        self.assertEqual(await consume(response), b"video")
        self.assertEqual(resolve.await_count, 2)
        self.assertEqual(safe_get.await_count, 2)
        self.assertEqual(
            safe_get.await_args_list[1].args[1],
            "https://new.googlevideo.com/videoplayback",
        )
        self.assertEqual(safe_get.await_args_list[1].args[2]["User-Agent"], "new-agent")
        cache_delete.assert_called_once_with("w3vt4U13QYM")
        self.assertEqual(rejected.close_count, 1)
        self.assertEqual(success.close_count, 1)
        self.assertEqual(client.close_count, 1)

    async def test_retries_only_once_and_does_not_cache_second_403(self):
        first = FakeUpstream(403)
        second = FakeUpstream(403)
        client = FakeClient()
        resolve = AsyncMock(
            side_effect=[
                stream_info("https://old.googlevideo.com/videoplayback"),
                stream_info("https://new.googlevideo.com/videoplayback"),
            ]
        )

        with (
            patch.object(main, "_resolve_video_url", resolve),
            patch.object(main, "_safe_get", AsyncMock(side_effect=[first, second])) as safe_get,
            patch.object(main._video_url_cache, "delete") as cache_delete,
            patch.object(main.httpx, "AsyncClient", return_value=client),
        ):
            response = await main.stream_video("w3vt4U13QYM", make_request())

        self.assertEqual(response.status_code, 403)
        self.assertEqual(await consume(response), b"")
        self.assertEqual(resolve.await_count, 2)
        self.assertEqual(safe_get.await_count, 2)
        self.assertEqual(cache_delete.call_count, 2)
        self.assertEqual(first.close_count, 1)
        self.assertEqual(second.close_count, 1)
        self.assertEqual(client.close_count, 1)

    async def test_closes_failed_response_when_refresh_fails(self):
        rejected = FakeUpstream(410)
        client = FakeClient()
        resolve = AsyncMock(
            side_effect=[
                stream_info("https://old.googlevideo.com/videoplayback"),
                main.YTDLPError("blocked", kind="BOT_DETECTED"),
            ]
        )

        with (
            patch.object(main, "_resolve_video_url", resolve),
            patch.object(main, "_safe_get", AsyncMock(return_value=rejected)),
            patch.object(main._video_url_cache, "delete"),
            patch.object(main.httpx, "AsyncClient", return_value=client),
        ):
            with self.assertRaises(HTTPException) as raised:
                await main.stream_video("w3vt4U13QYM", make_request())

        self.assertEqual(raised.exception.status_code, 502)
        self.assertEqual(rejected.close_count, 1)
        self.assertEqual(client.close_count, 1)


class VideoRequestConditionTests(unittest.TestCase):
    def test_configures_ipv4_and_pot_provider(self):
        with patch.dict(
            main.os.environ,
            {
                "YTDLP_PLAYER_CLIENT": "mweb",
                "YTDLP_POT_PROVIDER_URL": "http://127.0.0.1:4416",
            },
        ):
            options = main._base_ydl_opts()

        self.assertEqual(options["source_address"], "0.0.0.0")
        self.assertEqual(
            options["extractor_args"],
            {
                "youtube": {"player_client": ["mweb"]},
                "youtubepot-bgutilhttp": {
                    "base_url": ["http://127.0.0.1:4416"]
                },
            },
        )

    def test_uses_ytdlp_headers_and_chunk_size(self):
        info = stream_info(
            "https://video.googlevideo.com/videoplayback",
            user_agent="extractor-agent",
        )
        info["http_headers"]["Host"] = "malicious.example"
        info["http_headers"]["Cookie"] = "not-forwarded"

        headers = main._video_request_headers(info, "bytes=0-")

        self.assertEqual(headers["User-Agent"], "extractor-agent")
        self.assertEqual(headers["Range"], "bytes=0-10485759")
        self.assertNotIn("Host", headers)
        self.assertNotIn("Cookie", headers)

    def test_caps_ytdlp_chunk_size_at_server_limit(self):
        info = stream_info(
            "https://video.googlevideo.com/videoplayback",
            chunk_size=32 * 1024 * 1024,
        )

        headers = main._video_request_headers(info, "bytes=100-")

        self.assertEqual(headers["Range"], "bytes=100-16777315")


if __name__ == "__main__":
    unittest.main()

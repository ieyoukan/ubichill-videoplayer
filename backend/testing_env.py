"""テスト用の環境変数。app を import する前に読み込む。"""

import os

os.environ.setdefault("UBICHILL_ISSUERS", "https://ubichill.test")
os.environ.setdefault("SERVICE_AUDIENCE", "https://videoplayer.test")
os.environ.setdefault("MEDIA_URL_SECRET", "test-media-secret")

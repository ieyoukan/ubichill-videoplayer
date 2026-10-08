# 🎬 Video Player Mod

YouTube動画を backend 経由の opaque media gateway と Ubichill の正規メディアタイムラインで再生する mod。
video-player v3.1 は、サービス自身が発行する 5 分間の匿名利用トークンを使います。
Ubichill へのログインや、Ubichill サーバーの発行元登録は不要です。

## ✨ 特徴

- **VOD / ライブ / 音声のみ**: すべて同じ playback descriptor から読み込む
- **URL 非公開**: Google Video の署名 URL を短寿命の opaque token に置換
- **Server timeline**: 再生・停止・シークを revision 付き Server 時刻で同期
- **単一 MediaState**: duration、buffering、ended、構造化 error を一つの状態通知で扱う
- **権限を集約**: controls の `net:fetch` と screen の `media:control` を分離し、同一 backend domain を共有許可
- **利用開始トークン必須**: API はサービス自身が発行した短命の JWT を確かめ、接続元 IP ごとに回数を制限する。再生 URL は `/resolve` が発行する短命の署名付き URL のみ

## 📦 構成

```text
controls.worker ── /session → トークン付き Ubi.fetch(/resolve) ──> FastAPI + yt-dlp
       │                                      │
       └── typed VPEvents ──> screen.worker   └── opaque media gateway ──> YouTube
                                  │
                                  └── Ubi.media.load({ sync: "shared" })
                                             │
                                      Host MediaState machine
                                             │
                                      Server canonical timeline
```

## 🚀 クイックスタート

### 開発環境

```bash
cd mods/video-player
docker-compose up -d
```

バックエンド: http://localhost:8000  
API Docs: http://localhost:8000/docs

### 本番環境

```bash
# 基本構成
docker-compose -f docker-compose.prod.yml up -d

# Redis統合（推奨）
docker-compose -f docker-compose.cache.yml up -d
```

詳細は [DEPLOYMENT.md](./DEPLOYMENT.md) を参照

## 🎯 API エンドポイント

| Method | Path | 認証 | 説明 |
|--------|------|------|------|
| POST | `/session` | 不要・IP ごとの発行制限 | `{ "modId": "video-player" }` で利用開始トークンを取得 |
| GET | `/search?q={query}` | トークン | 動画検索 |
| GET | `/info/{video_id}` | トークン | 動画情報取得 |
| GET | `/resolve/{video_id}` | トークン | 同一originの安全な再生descriptorを即時発行（再生 URL に署名を付ける） |
| GET | `/stream/file/video/{video_id}?exp&sig` | 署名 | 通常動画のRange gateway |
| GET | `/stream/file/audio/{video_id}?exp&sig` | 署名 | 通常動画の音声Range gateway |
| GET | `/stream/{stream_id}/master.m3u8` | `/resolve` が作るセッション | live・明示HLS用の短寿命manifest |
| GET | `/stream/{stream_id}/resource/{token}/{opaque_name}` | 同上 | URL非公開のHLS resource gateway |

- トークン: `Authorization: Bearer <利用トークン>`。mod はこの API の `POST /session` で受け取る。
  5 分で失効し、mod 側で期限前に更新する。同時の発行依頼はまとめ、401 では 1 回だけ取り直す。
- 失敗: トークンが無い・通らない `401`、回数制限 `429`（`Retry-After`）、再生 URL の署名切れ `403`。
- サムネイルは利用者のブラウザが `i.ytimg.com` から直接読む（API を通さない）。
- 旧 API（`/video` `/audio` `/live` `/live-audio` `/thumbnail`）は誰でも使える中継になっていたため削除した。

Google Video の署名付きURLはブラウザへ返しません。VODは同一originのRange gateway、live/HLSは短寿命opaque tokenを使い、上流redirectもallowlistでホップごとに検証します。

## 🎨 フロントエンド統合

```tsx
import { VideoPlayer } from '@ubichill/mod-video-player';

function App() {
  return (
    <VideoPlayer
      initialTrack={{
        id: 'jfKfPfyJRdk',
        title: 'lofi hip hop radio',
        mode: 'live'  // or 'video'
      }}
    />
  );
}
```

## 🔧 設定

### 環境変数

| 変数 | デフォルト | 説明 |
|------|-----------|------|
| `VIDEO_PLAYER_PORT` | 8000 | バックエンドポート |
| `REDIS_ENABLED` | false | Redisキャッシュ有効化 |
| `REDIS_URL` | redis://localhost:6379 | Redis接続URL |
| `CACHE_TTL` | 3600 | キャッシュTTL（秒） |
| `WORKERS` | 4 | Uvicornワーカー数 |
| `REQUIRE_SERVICE_TOKEN` | true | false にすると誰でも使える（ローカル開発用のみ） |
| `SERVICE_AUDIENCE` | video-player-api | 利用トークンの宛先。サービスごとの固定値、または公開オリジン |
| `SERVICE_TOKEN_SECRET` | 起動ごとに生成 | このサービスの JWT 署名鍵（32 バイト以上）。秘密鍵は mod に渡さない |
| `RATE_LIMIT_SESSION` | 30/60 | 接続元 IP ごとのトークン発行制限（回数/秒数） |
| `SERVICE_ALLOWED_MODS` | video-player | 受け付ける mod の ID |
| `RATE_LIMIT_RESOLVE` / `RATE_LIMIT_SEARCH` / `RATE_LIMIT_INFO` | 30/600・20/60・60/60 | 接続元 IP ごとの回数制限（回数/秒数） |
| `MEDIA_URL_SECRET` | 起動ごとに生成 | 再生 URL の署名鍵（複数 replica・再起動をまたぐなら設定する） |
| `MEDIA_URL_TTL` | 21600 | 再生 URL の有効期間（秒） |

### Docker Compose設定

```yaml
services:
  video-player-backend:
    environment:
      - REDIS_ENABLED=true
      - REDIS_URL=redis://redis:6379
      - CACHE_TTL=7200
    deploy:
      replicas: 3  # スケーリング
      resources:
        limits:
          cpus: '2.0'
          memory: 2G
```

## 📊 スケーラビリティ

### 現在のスケール条件
- opaque HLS の URL 対応表は、署名付き上流 URL をクライアントへ出さないため process 内だけに保持する
- 既定の single worker / 1 replica で動かす。複数 replica にする場合は session affinity が必要
- Redis 等の共有 session store を実装するまでは HPA を有効にしない

### ⚠️ 制約事項
- YouTube API制限: 同一IPからの大量リクエスト
- yt-dlp処理時間: 1-3秒/リクエスト
- メモリ使用量: 512MB〜2GB/プロセス

### 推奨構成

| 規模 | レプリカ数 | Redis | その他 |
|------|-----------|-------|--------|
| 小（〜1K） | 1-2 | オプション | - |
| 中（1K-10K） | 1 | - | 先に共有 session store を導入 |
| 大（10K+） | 1 | - | 共有 store + token 対応 CDN が必須 |

詳細は [DEPLOYMENT.md#スケーラビリティ](./DEPLOYMENT.md#-スケーラビリティ) 参照

## 🔒 セキュリティ

- mod ID は自己申告。この方式は対応する利用開始手順を必須にするもので、実際に mod のコードが動いた証明ではない。手順を再現する専用クライアントは作れる。
- API の回数制限はトークンの ID ではなく接続元 IP を基準にする。トークンの再発行や他の接続元からのトークンの借用では上限をリセットできない。同じ NAT 配下の利用者は上限を共有する。
- ログイン、計算チャレンジ、コードの暗号化は利用開始の条件に含めない。
- 署名鍵が未設定なら再起動時にトークンが失効するが、mod は 401 で取り直す。複数プロセス・replica では署名鍵と回数制限の状態を共有する必要がある。
- 本番は信頼する Ingress 経由でのみ backend に接続させ、接続元 IP が正しく渡るようにする。直接公開する場合は Uvicorn の `FORWARDED_ALLOW_IPS` を信頼するプロキシに限定する。

- ✅ 非rootユーザーで実行
- ✅ 最小限の権限
- ✅ Resource limits設定
- ✅ ヘルスチェック実装
- ⚠️ レートリミット推奨（Nginx/Traefik）

## 🐛 トラブルシューティング

### よくある問題

**503 Service Unavailable**
```
原因: YouTube側で動画処理中
対処: しばらく待ってから再試行
```

**メモリ不足**
```bash
# メモリlimitを増やす
docker-compose up -d --scale video-player-backend=2
```

**キャッシュが効かない**
```bash
# Redis接続確認
docker exec ubichill-video-player-redis redis-cli ping

# キャッシュ統計確認
curl http://localhost:8000/cache/stats
```

## 📈 モニタリング

### ヘルスチェック
```bash
curl http://localhost:8000/
```

### キャッシュ統計（Redis有効時）
```bash
curl http://localhost:8000/cache/stats
```

### メトリクス（推奨）
- Prometheus + Grafana
- Datadog / New Relic
- CloudWatch / Azure Monitor

## 🧪 テスト

```bash
# Backend tests
cd backend
pytest

# Load test
ab -n 1000 -c 10 http://localhost:8000/api/stream/video/jfKfPfyJRdk
```

## 📚 関連ドキュメント

- [DEPLOYMENT.md](./DEPLOYMENT.md) - デプロイメントガイド
- [../../k8s/video-player-deployment.yaml](../../k8s/video-player-deployment.yaml) - K8s設定
- [FastAPI Docs](https://fastapi.tiangolo.com/)
- [yt-dlp GitHub](https://github.com/yt-dlp/yt-dlp)

## 📄 ライセンス

MIT License

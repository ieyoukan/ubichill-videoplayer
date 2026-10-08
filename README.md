# 🎬 Video Player Mod

YouTube動画を backend 経由の opaque media gateway と Ubichill の正規メディアタイムラインで再生する mod。
video-player v3.1 は、サービストークン（`Ubi.identity`、mod protocol v5）に対応した Ubichill を必要とします。
API サーバーは Ubichill にログインしている利用者からの依頼だけを受け付けます。

## ✨ 特徴

- **VOD / ライブ / 音声のみ**: すべて同じ playback descriptor から読み込む
- **URL 非公開**: Google Video の署名 URL を短寿命の opaque token に置換
- **Server timeline**: 再生・停止・シークを revision 付き Server 時刻で同期
- **単一 MediaState**: duration、buffering、ended、構造化 error を一つの状態通知で扱う
- **権限を集約**: controls の `net:fetch` と screen の `media:control` を分離し、同一 backend domain を共有許可
- **Ubichill の利用者だけが使える**: API はサービストークン（短命の JWT）を確かめ、利用者（サービスごとの仮名）ごとに回数を制限する。再生 URL は `/resolve` が発行する短命の署名付き URL のみ

## 📦 構成

```text
controls.worker ── Ubi.fetch(/resolve) ──> FastAPI + yt-dlp
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
| GET | `/search?q={query}` | トークン | 動画検索 |
| GET | `/info/{video_id}` | トークン | 動画情報取得 |
| GET | `/resolve/{video_id}` | トークン | 同一originの安全な再生descriptorを即時発行（再生 URL に署名を付ける） |
| GET | `/stream/file/video/{video_id}?exp&sig` | 署名 | 通常動画のRange gateway |
| GET | `/stream/file/audio/{video_id}?exp&sig` | 署名 | 通常動画の音声Range gateway |
| GET | `/stream/{stream_id}/master.m3u8` | `/resolve` が作るセッション | live・明示HLS用の短寿命manifest |
| GET | `/stream/{stream_id}/resource/{token}/{opaque_name}` | 同上 | URL非公開のHLS resource gateway |

- トークン: `Authorization: Bearer <サービストークン>`。mod は `Ubi.identity.token(<API のオリジン>)` で受け取る。
  検証の仕組みは Ubichill の [docs/SERVICE_TOKEN.md](https://github.com/ubichill/ubichill/blob/main/docs/SERVICE_TOKEN.md)。
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
| `UBICHILL_ISSUERS` | （必須） | 信頼する Ubichill の公開オリジン（カンマ区切り）。例 `https://ubichill.com` |
| `SERVICE_AUDIENCE` | （必須） | この API のオリジン。トークンの `aud` と一致しなければ拒否 |
| `SERVICE_ALLOWED_MODS` | video-player | 受け付ける mod の ID |
| `RATE_LIMIT_RESOLVE` / `RATE_LIMIT_SEARCH` / `RATE_LIMIT_INFO` | 30/600・20/60・60/60 | 利用者ごとの回数制限（回数/秒数） |
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

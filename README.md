# 🎬 Video Player Mod

YouTube動画を backend 経由の opaque HLS と Ubichill の正規メディアタイムラインで再生する mod。
video-player v3 は Ubichill SDK 2.1.0 以上（mod protocol v3 対応 Host）を必要とします。

## ✨ 特徴

- **VOD / ライブ / 音声のみ**: すべて同じ playback descriptor から読み込む
- **URL 非公開**: Google Video の署名 URL を短寿命の opaque token に置換
- **Server timeline**: 再生・停止・シークを revision 付き Server 時刻で同期
- **単一 MediaState**: duration、buffering、ended、構造化 error を一つの状態通知で扱う
- **権限を集約**: controls の `net:fetch` と screen の `media:control` を分離し、同一 backend domain を共有許可

## 📦 構成

```text
controls.worker ── Ubi.fetch(/resolve) ──> FastAPI + yt-dlp
       │                                      │
       └── typed VPEvents ──> screen.worker   └── opaque HLS gateway ──> YouTube
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

| Method | Path | 説明 |
|--------|------|------|
| GET | `/api/stream/search?q={query}` | 動画検索 |
| GET | `/api/stream/info/{video_id}` | 動画情報取得 |
| GET | `/api/stream/resolve/{video_id}` | 安全な再生 descriptor を発行 |
| GET | `/api/stream/live/{video_id}` | ライブ配信（opaque HLS URLへredirect・互換API） |
| GET | `/api/stream/live-audio/{video_id}` | ライブ音声（opaque HLS URLへredirect・互換API） |
| GET | `/api/stream/video/{video_id}` | 通常動画（deprecated MP4互換API） |
| GET | `/api/stream/audio/{video_id}` | 通常動画の音声のみ（deprecated互換API） |
| GET | `/api/stream/stream/{stream_id}/master.m3u8` | 短寿命opaque HLS manifest |
| GET | `/api/stream/stream/{stream_id}/resource/{token}/{opaque_name}` | URL非公開のHLS resource gateway |

Google Video の署名付きURLはブラウザへ返しません。manifest内のvariant、音声、鍵、init segment、media segmentはすべて短寿命のopaque tokenへ置換され、redirect先もallowlistでホップごとに検証されます。

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

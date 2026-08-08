# 🎬 Video Player Mod

YouTube動画のストリーミング再生を提供するUbichillmod

## ✨ 特徴

- 🔴 **ライブ配信対応**: 24/7ストリーミング対応
- 🎬 **通常動画再生**: MP4形式の直接再生
- 🎯 **モード切り替え**: UIで明示的にライブ/動画を選択
- 🚀 **スケーラブル**: 水平スケーリング対応
- 💾 **キャッシュ対応**: Redis統合でパフォーマンス向上

## 📦 構成

```
mods/video-player/
├── backend/              # FastAPI + yt-dlp
│   ├── main.py          # メイン実装
│   ├── main_with_cache.py  # Redis統合版（オプション）
│   ├── Dockerfile       # 開発用
│   └── Dockerfile.prod  # 本番用
├── frontend/            # React mod
│   └── src/
│       ├── VideoPlayer.tsx      # メインプレーヤー
│       ├── PlaylistPanel.tsx    # プレイリスト管理
│       └── types.ts             # 型定義
├── docker-compose.yml           # 開発環境
├── docker-compose.prod.yml      # 本番環境
├── docker-compose.cache.yml     # Redis統合版
├── DEPLOYMENT.md                # デプロイメントガイド
└── README.md                    # このファイル
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
| GET | `/api/stream/live/{video_id}` | ライブ配信（HLS） |
| GET | `/api/stream/video/{video_id}` | 通常動画（MP4） |
| GET | `/api/stream/proxy?url={url}` | HLSセグメントプロキシ |

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

### ✅ スケール可能
- 水平スケーリング: レプリカ数を増やすだけ
- ステートレス: セッション不要
- キャッシュ: Redis統合で負荷軽減

### ⚠️ 制約事項
- YouTube API制限: 同一IPからの大量リクエスト
- yt-dlp処理時間: 1-3秒/リクエスト
- メモリ使用量: 512MB〜2GB/プロセス

### 推奨構成

| 規模 | レプリカ数 | Redis | その他 |
|------|-----------|-------|--------|
| 小（〜1K） | 1-2 | オプション | - |
| 中（1K-10K） | 3-5 | 必須 | Load Balancer |
| 大（10K+） | 5-10 | Cluster | CDN必須 |

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

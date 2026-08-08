# Video Player Mod - 本番環境デプロイメントガイド

## 📦 コンテナビルド

### 開発環境
```bash
cd mods/video-player
docker-compose up -d
```

### 本番環境
```bash
cd mods/video-player
docker-compose -f docker-compose.prod.yml up -d
```

または個別ビルド：
```bash
cd backend
docker build -f Dockerfile.prod -t video-player-backend:latest --target production .
docker run -p 8000:8000 video-player-backend:latest
```

## 🚀 デプロイメント戦略

### Option 1: Docker Compose（小〜中規模）
メインアプリと同じネットワークで起動：

```bash
# ネットワーク作成（初回のみ）
docker network create ubichill-network

# video-playerバックエンドを起動
cd mods/video-player
docker-compose -f docker-compose.prod.yml up -d

# メインアプリを起動
cd ../..
docker-compose up -d
```

環境変数：
```env
VIDEO_PLAYER_PORT=8000
GITHUB_REPOSITORY_OWNER=your-org
VERSION=v1.0.0
```

### Option 2: Kubernetes（大規模・高可用性）

```bash
# デプロイ
kubectl apply -f k8s/video-player-deployment.yaml

# スケーリング確認
kubectl get hpa -n ubichill-mods

# ログ確認
kubectl logs -n ubichill-mods -l app=video-player -f
```

## 🎯 スケーラビリティ

### ✅ **スケール可能な要素**
- **ステートレス**: video-playerバックエンドは状態を持たない
- **水平スケーリング**: レプリカ数を増やして負荷分散可能
- **自動スケーリング**: HPA（Horizontal Pod Autoscaler）対応

### 水平スケーリングの設定
```yaml
# Kubernetes HPA
minReplicas: 2
maxReplicas: 10
targetCPUUtilizationPercentage: 70
```

```bash
# Docker Compose（Swarm mode）
docker service scale ubichill-video-player-backend=5
```

### ⚠️ **考慮すべき制約**

#### 1. YouTube API制限
- **IP制限**: YouTubeは同一IPからの大量リクエストを制限
- **対策**: 
  - キャッシュ層の追加（Redis）
  - CDN経由での動画配信
  - レートリミット設定（Ingress）

#### 2. yt-dlpのパフォーマンス
- **処理時間**: 動画メタデータ取得に1-3秒
- **対策**:
  - タイムアウト設定（300秒）
  - 非同期処理
  - キャッシュ戦略

#### 3. メモリ使用量
- **1プロセスあたり**: 512MB〜2GB（動画サイズによる）
- **推奨設定**:
  ```yaml
  resources:
    requests:
      memory: "512Mi"
      cpu: "500m"
    limits:
      memory: "2Gi"
      cpu: "2000m"
  ```

## 📊 モニタリング

### ヘルスチェック
```bash
# Docker
curl http://localhost:8000/

# Kubernetes
kubectl get pods -n ubichill-mods
kubectl describe pod <pod-name> -n ubichill-mods
```

### メトリクス収集（推奨）
- **Prometheus**: メトリクス収集
- **Grafana**: 可視化
- **Loki**: ログ集約

### 重要な監視項目
1. **レスポンスタイム**: `/api/stream/video/*` エンドポイント
2. **エラー率**: 503/404エラーの発生頻度
3. **メモリ使用量**: コンテナあたりの使用量
4. **CPU使用率**: yt-dlp処理の負荷

## 🔒 セキュリティ

### 実装済み
- ✅ 非rootユーザーで実行（UID 1000）
- ✅ 最小限の権限（capabilities drop ALL）
- ✅ ヘルスチェック実装
- ✅ Resource limits設定

### 推奨設定
```yaml
# Network Policy（Kubernetesの場合）
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: video-player-network-policy
spec:
  podSelector:
    matchLabels:
      app: video-player
  policyTypes:
  - Ingress
  - Egress
  ingress:
  - from:
    - namespaceSelector:
        matchLabels:
          name: ubichill
    ports:
    - protocol: TCP
      port: 8000
  egress:
  - to:
    - namespaceSelector: {}
    ports:
    - protocol: TCP
      port: 443  # YouTube API
```

## 🎨 パフォーマンス最適化

### 1. キャッシュ戦略
```python
# Redis cache example
@app.get("/api/stream/video/{video_id}")
async def stream_video(video_id: str):
    # Check cache first
    cached_url = await redis.get(f"video:{video_id}")
    if cached_url:
        return RedirectResponse(url=cached_url)
    
    # Fetch from YouTube
    url = get_youtube_url(video_id)
    await redis.setex(f"video:{video_id}", 3600, url)
    return RedirectResponse(url=url)
```

### 2. CDN統合
- CloudFlare
- AWS CloudFront
- Fastly

### 3. ワーカー数の調整
```bash
# CPU数に応じて自動調整
uvicorn main:app --workers $(nproc)

# 固定数
uvicorn main:app --workers 4
```

## 📈 スケーリング戦略

### 小規模（〜1000ユーザー）
- 単一コンテナ
- 2-4 workers
- 1-2GB RAM

### 中規模（1000-10000ユーザー）
- 2-5レプリカ
- Redisキャッシュ追加
- Load Balancer

### 大規模（10000+ユーザー）
- Kubernetes + HPA
- 5-10レプリカ
- CDN必須
- Redis Cluster
- 複数リージョン展開

## 🔧 トラブルシューティング

### 503 Service Unavailable
**原因**: YouTube側で動画処理中
**対処**: ユーザーに「しばらく待ってから再試行」を促す

### 404 Not Found
**原因**: 動画が削除/非公開/地域制限
**対処**: エラーメッセージをユーザーに表示

### メモリ不足
**原因**: yt-dlpの同時処理数が多すぎる
**対処**: 
- ワーカー数を減らす
- メモリlimitを増やす
- レートリミット実装

### CPU使用率が高い
**原因**: ffmpegのトランスコード処理
**対処**:
- CPU limitを増やす
- 動画品質を下げる（720p以下）

## 📝 CI/CD統合

GitHub Actionsワークフローに追加：

```yaml
# .github/workflows/mod-deploy.yml
name: Deploy Video Player Mod

on:
  push:
    paths:
      - 'mods/video-player/**'
    branches: [main]

jobs:
  build-and-deploy:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      
      - name: Build Docker image
        run: |
          cd mods/video-player/backend
          docker build -f Dockerfile.prod -t ghcr.io/${{ github.repository_owner }}/video-player-backend:${{ github.sha }} .
      
      - name: Push to registry
        run: |
          echo ${{ secrets.GITHUB_TOKEN }} | docker login ghcr.io -u ${{ github.actor }} --password-stdin
          docker push ghcr.io/${{ github.repository_owner }}/video-player-backend:${{ github.sha }}
      
      - name: Deploy to Kubernetes
        run: |
          kubectl set image deployment/video-player-backend \
            backend=ghcr.io/${{ github.repository_owner }}/video-player-backend:${{ github.sha }} \
            -n ubichill-mods
```

## 🌐 環境変数

### 必須
- `PYTHONUNBUFFERED=1`: ログバッファリング無効化
- `ENV=production`: 本番環境フラグ

### オプション
- `VIDEO_PLAYER_PORT=8000`: ポート番号
- `WORKERS=4`: Uvicornワーカー数
- `LOG_LEVEL=info`: ログレベル
- `REDIS_URL=redis://redis:6379`: キャッシュサーバー

## 📚 関連リンク

- [FastAPI Deployment](https://fastapi.tiangolo.com/deployment/)
- [yt-dlp Documentation](https://github.com/yt-dlp/yt-dlp)
- [Kubernetes HPA](https://kubernetes.io/docs/tasks/run-application/horizontal-pod-autoscale/)
- [Docker Compose Production](https://docs.docker.com/compose/production/)

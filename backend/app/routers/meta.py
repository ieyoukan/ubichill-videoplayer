"""ヘルスチェックと人気トラック（サンプル）。"""

from fastapi import APIRouter

router = APIRouter()


@router.get("/")
async def health_check():
    return {"message": "Ubichill Music Streaming API", "status": "healthy"}


@router.get("/popular")
async def get_popular_tracks():
    """人気トラックを返す（サンプル）"""
    # 実際のプロダクションでは、データベースやキャッシュから取得
    popular_tracks = [
        {
            "id": "dQw4w9WgXcQ",
            "title": "Rick Astley - Never Gonna Give You Up",
            "thumbnail": "https://img.youtube.com/vi/dQw4w9WgXcQ/maxresdefault.jpg",
            "duration": 213,
            "author": "Rick Astley",
        },
        {
            "id": "9bZkp7q19f0",
            "title": "PSY - GANGNAM STYLE",
            "thumbnail": "https://img.youtube.com/vi/9bZkp7q19f0/maxresdefault.jpg",
            "duration": 252,
            "author": "officialpsy",
        },
    ]
    return popular_tracks

/**
 * video-player:search Worker — URL 追加 + YouTube 検索 UI。
 *
 * watchScope='parent' で screen.playlist を読み書きする。
 * 検索クエリ / 結果 / 入力中フラグはローカル状態。
 */

import type { ComponentConfig, RpcNetworkFetchResult } from 'ubichill';
import { VPEvents, VPTarget } from './events';
import { SearchIcon, VideoIcon } from './icons';

export const config: ComponentConfig = {
    watchEntityTypes: [],
    watchScope: 'entity',
    defaultTransform: { x: 330, y: 440, z: 198, w: 310, h: 300 },
    capabilities: ['event:emit', 'net:fetch', 'scene:read', 'ui:render'],
};

import { formatTime } from './lib/playback';
import { parseVideoId, thumbnailUrl } from './lib/youtube';
import type { SearchResult, Track } from './types';

const DEFAULT_API_BASE = 'https://videoplayer.youkan.uk';

// search はどの entity も watch しない (watchEntityTypes=[]) ため、
// sync 系フィールドは target が解決できず flush が無音失敗する。
// search は外部と状態共有しないので全部ローカルでよい。
// apiBase は本来 controls の同名フィールドと一致してほしいが、URL ベースが
// 動的に変わるケースが今のところ無いので定数で十分。
const state = Ubi.state.define({
    apiBase: DEFAULT_API_BASE,
    selectedMode: 'video' as 'live' | 'video',
    urlInput: '',
    searchQuery: '',
    searchResults: [] as SearchResult[],
    isSearching: false,
});

const emitAddTrack = (track: Track): void => {
    VPEvents.emit('vp:track:add', { track }, VPTarget.playlist);
};

const apiBase = (): string => state.local.apiBase.trim() || DEFAULT_API_BASE;

// ── アクション ──────────────────────────────────────
const setMode = (m: 'live' | 'video'): void => {
    state.local.selectedMode = m;
    render();
};

const setUrl = (v: string): void => {
    state.local.urlInput = v;
};

const setQuery = (v: string): void => {
    state.local.searchQuery = v;
};

const addFromUrl = async (): Promise<void> => {
    const videoId = parseVideoId(state.local.urlInput);
    if (!videoId) return;
    state.local.isSearching = true;
    render();
    const res = (await Ubi.fetch(`${apiBase()}/info/${videoId}`)) as RpcNetworkFetchResult;
    const info = res.ok ? (JSON.parse(res.body) as { title?: string; thumbnail?: string; duration?: number }) : {};
    emitAddTrack({
        id: videoId,
        title: info.title ?? state.local.urlInput,
        thumbnail: info.thumbnail ?? thumbnailUrl(videoId),
        duration: info.duration ?? 0,
        mode: state.local.selectedMode,
    });
    state.local.urlInput = '';
    state.local.isSearching = false;
    render();
};

const doSearch = async (): Promise<void> => {
    if (!state.local.searchQuery.trim()) return;
    state.local.isSearching = true;
    render();
    const res = (await Ubi.fetch(
        `${apiBase()}/search?q=${encodeURIComponent(state.local.searchQuery)}&limit=10`,
    )) as RpcNetworkFetchResult;
    state.local.searchResults = res.ok ? (JSON.parse(res.body) as SearchResult[]) : [];
    state.local.isSearching = false;
    render();
};

const addResult = (r: SearchResult): void => {
    emitAddTrack({
        id: r.id,
        title: r.title,
        thumbnail: r.thumbnail,
        duration: r.duration,
        mode: state.local.selectedMode,
    });
    // 検索クエリ / 結果はリセットしない (続けて他の曲も追加できるように)
};

// ── レンダリング ───────────────────────────────────
function render(): void {
    const { selectedMode, urlInput, searchQuery, searchResults, isSearching } = state.local;

    Ubi.ui.render(
        () => (
            <div
                style={{
                    position: 'absolute',
                    inset: '0',
                    background: '#1a1a1a',
                    borderRadius: '12px',
                    boxShadow: '0 2px 8px rgba(0,0,0,0.2)',
                    border: '1px solid rgba(255,255,255,0.08)',
                    overflow: 'hidden',
                    display: 'flex',
                    flexDirection: 'column',
                    fontFamily: 'system-ui, -apple-system, sans-serif',
                    userSelect: 'none',
                    pointerEvents: 'auto',
                }}
            >
                <div style={{ padding: '8px', display: 'flex', flexDirection: 'column', gap: '6px' }}>
                    {/* モード選択 */}
                    <div style={{ display: 'flex', gap: '0' }}>
                        <button
                            type="button"
                            style={{
                                flex: '1',
                                background: selectedMode === 'live' ? '#ff4444' : '#333',
                                border: 'none',
                                borderRadius: '4px 0 0 4px',
                                color: '#fff',
                                padding: '6px 12px',
                                fontSize: '12px',
                                cursor: 'pointer',
                                display: 'flex',
                                alignItems: 'center',
                                justifyContent: 'center',
                                gap: '6px',
                            }}
                            onUbiClick={() => setMode('live')}
                        >
                            <span
                                style={{
                                    width: '7px',
                                    height: '7px',
                                    borderRadius: '50%',
                                    background: '#fff',
                                    display: 'inline-block',
                                }}
                            />
                            Live
                        </button>
                        <button
                            type="button"
                            style={{
                                flex: '1',
                                background: selectedMode === 'video' ? '#4444ff' : '#333',
                                border: 'none',
                                borderRadius: '0 4px 4px 0',
                                color: '#fff',
                                padding: '6px 12px',
                                fontSize: '12px',
                                cursor: 'pointer',
                                display: 'flex',
                                alignItems: 'center',
                                justifyContent: 'center',
                                gap: '6px',
                            }}
                            onUbiClick={() => setMode('video')}
                        >
                            <VideoIcon size={12} />
                            Video
                        </button>
                    </div>

                    {/* URL 入力 */}
                    <div style={{ display: 'flex', gap: '4px' }}>
                        <input
                            type="text"
                            placeholder="YouTube URL or ID..."
                            value={urlInput}
                            style={{
                                flex: '1',
                                background: 'rgba(255,255,255,0.1)',
                                border: '1px solid rgba(255,255,255,0.2)',
                                borderRadius: '6px',
                                padding: '5px 8px',
                                color: '#fff',
                                fontSize: '12px',
                                outline: 'none',
                                minWidth: '0',
                            }}
                            onUbiInput={(val: unknown) => setUrl(String(val))}
                        />
                        <button
                            type="button"
                            disabled={isSearching}
                            style={{
                                background: 'rgba(255,255,255,0.1)',
                                border: 'none',
                                color: 'rgba(255,255,255,0.8)',
                                cursor: isSearching ? 'not-allowed' : 'pointer',
                                padding: '5px 10px',
                                borderRadius: '6px',
                                fontSize: '14px',
                                opacity: isSearching ? '0.5' : '1',
                            }}
                            onUbiClick={() => {
                                void addFromUrl();
                            }}
                        >
                            +
                        </button>
                    </div>

                    {/* 検索クエリ */}
                    <div style={{ display: 'flex', gap: '4px' }}>
                        <input
                            type="text"
                            placeholder="Search YouTube..."
                            value={searchQuery}
                            style={{
                                flex: '1',
                                background: 'rgba(255,255,255,0.1)',
                                border: '1px solid rgba(255,255,255,0.2)',
                                borderRadius: '6px',
                                padding: '5px 8px',
                                color: '#fff',
                                fontSize: '12px',
                                outline: 'none',
                                minWidth: '0',
                            }}
                            onUbiInput={(val: unknown) => setQuery(String(val))}
                        />
                        <button
                            type="button"
                            disabled={isSearching}
                            style={{
                                background: 'rgba(255,255,255,0.1)',
                                border: 'none',
                                color: 'rgba(255,255,255,0.8)',
                                cursor: isSearching ? 'not-allowed' : 'pointer',
                                padding: '5px 10px',
                                borderRadius: '6px',
                                display: 'flex',
                                alignItems: 'center',
                                opacity: isSearching ? '0.5' : '1',
                            }}
                            onUbiClick={() => {
                                void doSearch();
                            }}
                        >
                            <SearchIcon size={14} />
                        </button>
                    </div>
                </div>

                {/* 検索結果 */}
                <div style={{ flex: '1', overflowY: 'auto', padding: '8px' }}>
                    {searchResults.length === 0 ? (
                        <div
                            style={{
                                padding: '24px',
                                textAlign: 'center',
                                fontSize: '12px',
                                color: 'rgba(255,255,255,0.5)',
                            }}
                        >
                            {isSearching ? 'Searching...' : 'No results'}
                        </div>
                    ) : (
                        searchResults.map((r) => (
                            <div
                                key={r.id}
                                style={{
                                    display: 'flex',
                                    alignItems: 'center',
                                    gap: '8px',
                                    padding: '6px 8px',
                                    borderRadius: '6px',
                                    marginBottom: '4px',
                                }}
                            >
                                <img
                                    src={r.thumbnail}
                                    alt=""
                                    loading="lazy"
                                    decoding="async"
                                    width="32"
                                    height="32"
                                    style={{
                                        width: '32px',
                                        height: '32px',
                                        borderRadius: '4px',
                                        objectFit: 'cover',
                                    }}
                                />
                                <div style={{ flex: '1', minWidth: '0' }}>
                                    <div
                                        style={{
                                            fontSize: '11px',
                                            fontWeight: '500',
                                            color: '#fff',
                                            whiteSpace: 'nowrap',
                                            overflow: 'hidden',
                                            textOverflow: 'ellipsis',
                                        }}
                                    >
                                        {r.title}
                                    </div>
                                    <div style={{ fontSize: '10px', color: 'rgba(255,255,255,0.6)' }}>
                                        {formatTime(r.duration)}
                                    </div>
                                </div>
                                <button
                                    type="button"
                                    style={{
                                        background: 'transparent',
                                        border: 'none',
                                        color: 'rgba(255,255,255,0.6)',
                                        cursor: 'pointer',
                                        padding: '4px 8px',
                                        borderRadius: '4px',
                                        fontSize: '16px',
                                    }}
                                    onUbiClick={() => addResult(r)}
                                >
                                    +
                                </button>
                            </div>
                        ))
                    )}
                </div>
            </div>
        ),
        'search',
    );
}

render();

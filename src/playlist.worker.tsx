/**
 * video-player:playlist Worker — プレイリストの一意な所有者。
 *
 * 状態: playlist + currentIndex のみ保持 (entity.data)。
 * Worker 間通信は `VPEvents` に閉じた型付き emit/on のみ。
 */

import type { ComponentConfig } from 'ubichill';
import { VPEvents, VPTarget } from './events';
import { PlaySmallIcon, TrashIcon } from './icons';

export const config: ComponentConfig = {
    watchEntityTypes: ['video-player:playlist'],
    watchScope: 'entity',
    defaultTransform: { x: 0, y: 440, z: 198, w: 310, h: 300 },
    capabilities: ['event:emit', 'scene:read', 'scene:update', 'ui:render'],
};

import { formatTime } from './lib/playback';
import { thumbnailUrl } from './lib/youtube';
import type { LoopMode, Track } from './types';

const state = Ubi.state.define({
    playlist: Ubi.state.sync([] as Track[], {
        label: 'プレイリスト',
        item: {
            title: { type: 'string', label: 'タイトル' },
            id: {
                type: 'string',
                label: 'YouTube URL / 動画ID',
                placeholder: 'https://youtu.be/... または 動画ID',
            },
            mode: { type: 'enum', options: ['video', 'live'], default: 'video', label: '種別' },
        },
    }),
    currentIndex: Ubi.state.sync(0, { editable: false }),
});

/** 現トラックを siblings に通知。 */
function emitCurrent(): void {
    const list = state.local.playlist;
    const idx = state.local.currentIndex;
    const track = list[idx] ?? null;
    VPEvents.emit('vp:track:current', { track, index: idx, total: list.length }, VPTarget.siblings);
}

function addTrack(track: Track): void {
    state.local.playlist = [...state.local.playlist, track];
}

function selectTrack(i: number): void {
    if (i < 0 || i >= state.local.playlist.length) return;
    state.local.currentIndex = i;
}

function removeTrack(i: number): void {
    const newList = state.local.playlist.filter((_, idx) => idx !== i);
    state.batch(() => {
        let newIdx = state.local.currentIndex;
        if (i < newIdx) newIdx -= 1;
        if (newIdx >= newList.length) newIdx = Math.max(0, newList.length - 1);
        state.local.playlist = newList;
        state.local.currentIndex = newIdx;
    });
}

function nextTrack(loop: LoopMode, shuffle: boolean): void {
    const len = state.local.playlist.length;
    if (len === 0) return;
    if (loop === 'one') {
        VPEvents.emit('vp:track:replay', {}, VPTarget.controls);
        return;
    }
    if (shuffle) {
        if (len === 1) {
            VPEvents.emit('vp:track:replay', {}, VPTarget.controls);
            return;
        }
        state.local.currentIndex = Math.floor(Math.random() * len);
        return;
    }
    const cur = state.local.currentIndex;
    if (cur + 1 < len) {
        state.local.currentIndex = cur + 1;
        return;
    }
    if (loop === 'all') {
        if (len === 1) {
            VPEvents.emit('vp:track:replay', {}, VPTarget.controls);
        } else {
            state.local.currentIndex = 0;
        }
    } else {
        VPEvents.emit('vp:playback:stop', {}, VPTarget.controls);
    }
}

function prevTrack(): void {
    const len = state.local.playlist.length;
    if (len === 0) return;
    state.local.currentIndex = state.local.currentIndex > 0 ? state.local.currentIndex - 1 : len - 1;
}

// ── 副作用（描画は自動追跡、イベント通知は手動） ─────
state.onChange('playlist', emitCurrent);
state.onChange('currentIndex', emitCurrent);

// ── レンダリング（自動追跡: 読んだキーが変わると自動再描画） ─────
export default function PlaylistView() {
    const list = state.local.playlist;
    const cur = state.local.currentIndex;
    return (
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
            <div
                style={{
                    padding: '8px 12px',
                    borderBottom: '1px solid rgba(255,255,255,0.1)',
                    fontSize: '12px',
                    fontWeight: '600',
                    color: '#fff',
                }}
            >
                Playlist ({list.length})
            </div>
            <div style={{ flex: '1', overflowY: 'auto', padding: '8px' }}>
                {list.length === 0 ? (
                    <div style={{ padding: '24px', textAlign: 'center' }}>
                        <div style={{ fontSize: '12px', color: 'rgba(255,255,255,0.5)' }}>No tracks</div>
                    </div>
                ) : (
                    list.map((t, i) => (
                        <div
                            key={`${t.id}-${i}`}
                            style={{
                                display: 'flex',
                                alignItems: 'center',
                                gap: '8px',
                                padding: '6px 8px',
                                borderRadius: '6px',
                                marginBottom: '4px',
                                background: i === cur ? 'rgba(0,122,255,0.2)' : 'transparent',
                                cursor: 'pointer',
                            }}
                            onUbiClick={() => selectTrack(i)}
                        >
                            <img
                                src={t.thumbnail || thumbnailUrl(t.id)}
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
                                    <span
                                        style={{
                                            display: 'inline-block',
                                            width: '6px',
                                            height: '6px',
                                            borderRadius: '50%',
                                            background: t.mode === 'live' ? '#ff4444' : '#4444ff',
                                            marginRight: '5px',
                                            verticalAlign: 'middle',
                                        }}
                                    />
                                    {t.title}
                                </div>
                                <div style={{ fontSize: '10px', color: 'rgba(255,255,255,0.6)' }}>
                                    {t.duration > 0 ? formatTime(t.duration) : t.mode === 'live' ? 'LIVE' : '--:--'}
                                </div>
                            </div>
                            {i === cur && (
                                <span style={{ color: '#007aff', flexShrink: '0' }}>
                                    <PlaySmallIcon />
                                </span>
                            )}
                            <button
                                type="button"
                                style={{
                                    background: 'transparent',
                                    border: 'none',
                                    color: 'rgba(255,255,255,0.6)',
                                    cursor: 'pointer',
                                    padding: '4px 8px',
                                    borderRadius: '4px',
                                    display: 'flex',
                                    alignItems: 'center',
                                }}
                                onUbiClick={() => removeTrack(i)}
                            >
                                <TrashIcon size={14} />
                            </button>
                        </div>
                    ))
                )}
            </div>
        </div>
    );
}

// ── イベント受信 ──────────────────────────────────────
VPEvents.on('vp:track:add', ({ track }) => addTrack(track));
VPEvents.on('vp:track:next', ({ loop, shuffle }) => nextTrack(loop, shuffle));
VPEvents.on('vp:track:prev', () => prevTrack());

// 起動時に siblings に通知 (siblings の起動順に依存しないよう少し遅延)
queueMicrotask(emitCurrent);

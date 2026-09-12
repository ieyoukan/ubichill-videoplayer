/**
 * video-player:controls Worker — 再生制御の中枢。
 *
 * 再生位置と再生状態は Host の MediaState / Server timeline を正本とする。
 * controls 自身は時計を進めず、UI intent を screen のmedia runtimeへ渡すだけ。
 *
 * Worker 間通信は VPEvents (型付き) のみ。
 */

import type { ComponentConfig, MediaSource, MediaState, RpcNetworkFetchResult } from 'ubichill';
import { VPEvents, VPTarget } from './events';

export const config: ComponentConfig = {
    watchEntityTypes: ['video-player:controls'],
    watchScope: 'entity',
    defaultTransform: { x: 0, y: 370, z: 198, w: 640, h: 60 },
    capabilities: ['event:emit', 'net:fetch', 'scene:read', 'scene:update', 'ui:render'],
};

import {
    AudioOnlyIcon,
    PauseIcon,
    PlayIcon,
    RepeatIcon,
    RepeatOneIcon,
    ShuffleIcon,
    SkipNextIcon,
    SkipPrevIcon,
    VideoIcon,
    VolumeHighIcon,
    VolumeLowIcon,
    VolumeMediumIcon,
    VolumeMuteIcon,
} from './icons';
import { formatTime } from './lib/playback';
import { extractVideoId, thumbnailUrl } from './lib/youtube';
import type { LoopMode, Track } from './types';

const DEFAULT_API_BASE = 'https://videoplayer.youkan.uk';

const state = Ubi.state.define({
    // ── 共有 + 永続。runtime 専用は editable:false で Inspector から除外 ──
    autoplay: Ubi.state.sync(false, {
        label: '作成時に自動再生',
        help: 'オンにすると、インスタンス作成時にプレイリスト先頭から再生を開始します',
    }),
    loop: Ubi.state.sync<LoopMode>('none', {
        label: 'ループ',
        options: ['none', 'one', 'all'],
    }),
    shuffle: Ubi.state.sync(false, { label: 'シャッフル' }),
    audioOnly: Ubi.state.sync(false, { label: 'スクリーンを畳む' }),
    apiBase: Ubi.state.sync(DEFAULT_API_BASE, { label: 'API ベース URL' }),
    // ── 共有 + 永続 (per-user) ──
    myVolume: Ubi.state.sync(0.7, { perUser: true, editable: false }),
    // ── ローカル ──
    currentTrack: null as Track | null,
    currentIndex: 0,
    totalTracks: 0,
    isLoading: false,
    errorMessage: '',
    mediaState: null as MediaState | null,
});

// ── ヘルパー ────────────────────────────────────────
function currentTime(): number {
    const media = state.local.mediaState;
    return media?.timelineTime ?? media?.currentTime ?? 0;
}

function isMediaPlaying(media: MediaState | null): boolean {
    if (!media) return false;
    if (media.status === 'playing' || media.status === 'buffering') return true;
    return media.status === 'seeking' && media.timeline?.phase === 'playing';
}

interface PlaybackDescriptor {
    source: MediaSource;
}

function resolveTrackUrl(track: Track): string {
    const base = state.local.apiBase.trim() || DEFAULT_API_BASE;
    const presentation = state.local.audioOnly ? 'audio' : 'video';
    return `${base}/resolve/${extractVideoId(track.id)}?mode=${track.mode}&presentation=${presentation}`;
}

function mediaIdFor(track: Track): string {
    const presentation = state.local.audioOnly ? 'audio' : 'video';
    return `youtube:${track.mode}:${presentation}:${extractVideoId(track.id)}`;
}

let loadRequestRevision = 0;
async function loadCurrentTrack(): Promise<void> {
    const track = state.local.currentTrack;
    if (!track) return;
    const revision = ++loadRequestRevision;
    state.batch(() => {
        state.local.isLoading = true;
        state.local.errorMessage = '';
    });
    try {
        const resolveUrl = resolveTrackUrl(track);
        const response = (await Ubi.fetch(resolveUrl)) as RpcNetworkFetchResult;
        if (revision !== loadRequestRevision || state.local.currentTrack?.id !== track.id) return;
        if (!response.ok) throw new Error('resolve request failed');
        const descriptor = JSON.parse(response.body) as PlaybackDescriptor;
        const expectedOrigin = new URL(resolveUrl).origin;
        const sourceOrigin = new URL(descriptor.source.url).origin;
        const sourceType = descriptor.source.type;
        if (
            (sourceType !== 'hls' && sourceType !== 'file') ||
            descriptor.source.id !== mediaIdFor(track) ||
            sourceOrigin !== expectedOrigin
        ) {
            throw new Error('invalid playback descriptor');
        }
        VPEvents.emit(
            'vp:media:load',
            {
                source: descriptor.source,
                presentation: state.local.audioOnly ? 'audio' : 'video',
            },
            VPTarget.screen,
        );
    } catch {
        if (revision !== loadRequestRevision || state.local.currentTrack?.id !== track.id) return;
        state.batch(() => {
            state.local.isLoading = false;
            state.local.errorMessage = '再生URLを解決できませんでした';
        });
    }
}

// ── UI アクション ──────────────────────────────────
const onSeek = (time: number): void => {
    VPEvents.emit('vp:media:seek', { time }, VPTarget.screen);
};
const onPlayToggle = (): void => {
    const media = state.local.mediaState;
    const playing = isMediaPlaying(media);
    VPEvents.emit(playing ? 'vp:media:pause' : 'vp:media:play', {}, VPTarget.screen);
};
const onPrev = (): void => {
    VPEvents.emit('vp:track:prev', {}, VPTarget.playlist);
};
const onNext = (): void => {
    VPEvents.emit('vp:track:next', { loop: state.local.loop, shuffle: state.local.shuffle }, VPTarget.playlist);
};
const onShuffleToggle = (): void => {
    state.local.shuffle = !state.local.shuffle;
};
const onLoopCycle = (): void => {
    state.local.loop = state.local.loop === 'none' ? 'all' : state.local.loop === 'all' ? 'one' : 'none';
};
const onVolumeChange = (v: number): void => {
    state.local.myVolume = v;
};
const onAudioOnlyToggle = (): void => {
    state.local.audioOnly = !state.local.audioOnly;
};

// ── 副作用のみ。描画は state 読み取りによる自動追跡に任せる ──
state.onChange('audioOnly', () => void loadCurrentTrack());
state.onChange('myVolume', (v) => {
    VPEvents.emit('vp:media:volume', { volume: v }, VPTarget.screen);
});

// ── レンダリング（自動追跡: MediaState 通知で再描画） ─────
export default function ControlsView() {
    const track = state.local.currentTrack;
    const media = state.local.mediaState;
    const thumb = track ? thumbnailUrl(track.id, state.local.apiBase) : '';
    const ct = currentTime();
    const duration = media?.duration ?? 0;
    const progress = duration > 0 ? (ct / duration) * 100 : 0;
    const isLive = media?.isLive ?? track?.mode === 'live';
    const isLoading = state.local.isLoading || media?.status === 'loading';
    const errorMessage = media?.error?.message || state.local.errorMessage;
    const volume = state.local.myVolume;
    const VolumeIcon =
        volume === 0 ? VolumeMuteIcon : volume < 0.3 ? VolumeLowIcon : volume < 0.7 ? VolumeMediumIcon : VolumeHighIcon;
    const LoopIconComp = state.local.loop === 'one' ? RepeatOneIcon : RepeatIcon;
    const isPlaying = isMediaPlaying(media);
    const empty = state.local.totalTracks === 0;
    const seekBackground = isLoading
        ? 'linear-gradient(90deg, rgba(255,255,255,0.05) 0%, rgba(0,122,255,0.5) 50%, rgba(255,255,255,0.05) 100%)'
        : `linear-gradient(to right, #007aff ${progress}%, rgba(255,255,255,0.2) ${progress}%)`;
    const seekDisabled = isLoading || duration <= 0 || isLive;

    return (
        <div
            style={{
                position: 'absolute',
                inset: '0',
                background: '#1a1a1a',
                borderRadius: '12px',
                padding: '8px 12px',
                boxShadow: '0 2px 8px rgba(0,0,0,0.2)',
                border: '1px solid rgba(255,255,255,0.08)',
                fontFamily: 'system-ui, -apple-system, sans-serif',
                userSelect: 'none',
                pointerEvents: 'auto',
            }}
        >
            <input
                type="range"
                min="0"
                max={String(duration > 0 ? duration : 100)}
                step="0.1"
                value={String(isLoading ? 0 : ct.toFixed(1))}
                disabled={seekDisabled}
                style={{
                    width: '100%',
                    height: '4px',
                    marginBottom: '8px',
                    display: 'block',
                    cursor: seekDisabled ? 'default' : 'pointer',
                    accentColor: '#007aff',
                    appearance: 'none',
                    background: seekBackground,
                    backgroundSize: isLoading ? '200% 100%' : '100% 100%',
                    animation: isLoading ? 'ubichill-vp-loading 1.5s linear infinite' : 'none',
                    borderRadius: '2px',
                    outline: 'none',
                }}
                onUbiInput={(val: unknown) => onSeek(Number.parseFloat(String(val)))}
            />
            <div
                style={{
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'space-between',
                    gap: '12px',
                }}
            >
                <div
                    style={{
                        display: 'flex',
                        alignItems: 'center',
                        gap: '8px',
                        flex: '1',
                        minWidth: '0',
                    }}
                >
                    {thumb && (
                        <img
                            src={thumb}
                            alt=""
                            decoding="async"
                            width="36"
                            height="36"
                            style={{
                                width: '36px',
                                height: '36px',
                                borderRadius: '4px',
                                objectFit: 'cover',
                                flexShrink: '0',
                            }}
                        />
                    )}
                    <div style={{ display: 'flex', flexDirection: 'column', minWidth: '0' }}>
                        <div
                            style={{
                                fontSize: '12px',
                                fontWeight: '600',
                                color: errorMessage ? '#ff6b6b' : '#fff',
                                whiteSpace: 'nowrap',
                                overflow: 'hidden',
                                textOverflow: 'ellipsis',
                            }}
                        >
                            {errorMessage || (track ? track.title || track.id : '---')}
                        </div>
                        <div style={{ fontSize: '10px', color: 'rgba(255,255,255,0.6)' }}>
                            {formatTime(ct)} / {duration > 0 ? formatTime(duration) : isLive ? 'LIVE' : '--:--'}
                        </div>
                    </div>
                </div>

                <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                    <CtrlBtn disabled={empty} onClick={onPrev}>
                        <SkipPrevIcon size={18} />
                    </CtrlBtn>
                    <button
                        type="button"
                        disabled={empty}
                        style={{
                            background: '#007aff',
                            border: 'none',
                            color: '#fff',
                            cursor: empty ? 'not-allowed' : 'pointer',
                            width: '36px',
                            height: '36px',
                            borderRadius: '50%',
                            display: 'flex',
                            alignItems: 'center',
                            justifyContent: 'center',
                            boxShadow: '0 2px 8px rgba(0,122,255,0.3)',
                            opacity: empty ? '0.5' : '1',
                        }}
                        onUbiClick={onPlayToggle}
                    >
                        {isPlaying ? <PauseIcon size={20} /> : <PlayIcon size={20} />}
                    </button>
                    <CtrlBtn disabled={empty} onClick={onNext}>
                        <SkipNextIcon size={18} />
                    </CtrlBtn>
                </div>

                <div
                    style={{
                        display: 'flex',
                        alignItems: 'center',
                        gap: '8px',
                        flex: '1',
                        justifyContent: 'flex-end',
                    }}
                >
                    <CtrlBtn active={state.local.shuffle} onClick={onShuffleToggle}>
                        <ShuffleIcon size={16} />
                    </CtrlBtn>
                    <CtrlBtn active={state.local.loop !== 'none'} onClick={onLoopCycle}>
                        <LoopIconComp size={16} />
                    </CtrlBtn>
                    <CtrlBtn
                        active={state.local.audioOnly}
                        title={state.local.audioOnly ? 'スクリーンを展開' : 'スクリーンを畳んで音声のみ再生'}
                        onClick={onAudioOnlyToggle}
                    >
                        {state.local.audioOnly ? <VideoIcon size={16} /> : <AudioOnlyIcon size={16} />}
                    </CtrlBtn>
                    <span
                        style={{
                            color: 'rgba(255,255,255,0.8)',
                            display: 'flex',
                            alignItems: 'center',
                        }}
                    >
                        <VolumeIcon size={16} />
                    </span>
                    <input
                        type="range"
                        min="0"
                        max="1"
                        step="0.01"
                        value={String(volume)}
                        style={{
                            width: '60px',
                            height: '3px',
                            background: 'rgba(255,255,255,0.2)',
                            borderRadius: '2px',
                            outline: 'none',
                            cursor: 'pointer',
                            appearance: 'none',
                            accentColor: '#007aff',
                        }}
                        onUbiInput={(val: unknown) => onVolumeChange(Number.parseFloat(String(val)))}
                    />
                </div>
            </div>
        </div>
    );
}

function CtrlBtn({
    children,
    onClick,
    disabled = false,
    active = false,
    title,
}: {
    children: import('ubichill/jsx-runtime').JSX.Element | import('ubichill/jsx-runtime').JSX.Element[] | null;
    onClick: () => void;
    disabled?: boolean;
    active?: boolean;
    title?: string;
}): import('ubichill/jsx-runtime').JSX.Element {
    return (
        <button
            type="button"
            disabled={disabled}
            title={title}
            style={{
                background: 'transparent',
                border: 'none',
                cursor: disabled ? 'not-allowed' : 'pointer',
                padding: '6px',
                borderRadius: '6px',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                flexShrink: '0',
                color: disabled ? 'rgba(255,255,255,0.3)' : active ? '#007aff' : 'rgba(255,255,255,0.8)',
                opacity: disabled ? '0.3' : '1',
            }}
            onUbiClick={onClick}
        >
            {children}
        </button>
    );
}

VPEvents.on('vp:track:current', ({ track, index, total }) => {
    const prev = state.local.currentTrack;
    const prevId = prev?.id ?? null;
    const nextId = track?.id ?? null;
    const needLoad = prevId !== nextId;

    state.batch(() => {
        state.local.currentTrack = track;
        state.local.currentIndex = index;
        state.local.totalTracks = total;

        if (needLoad && track) {
            state.local.isLoading = true;
            state.local.mediaState = null;
        }
    });

    if (needLoad && track) {
        void loadCurrentTrack();
    }
});

VPEvents.on('vp:media:loaded', ({ duration }) => {
    state.batch(() => {
        state.local.isLoading = false;
        state.local.errorMessage = '';
    });
    if (state.local.autoplay && duration >= 0 && state.local.mediaState?.timeline?.revision === 1) {
        VPEvents.emit('vp:media:play', {}, VPTarget.screen);
    }
});

VPEvents.on('vp:media:error', ({ message }) => {
    state.batch(() => {
        state.local.isLoading = false;
        state.local.errorMessage = message || '動画を読み込めませんでした';
    });
});

let handledEndedRevision = 0;
VPEvents.on('vp:media:stateChange', (mediaState) => {
    const track = state.local.currentTrack;
    if (track && mediaState.source?.id && mediaState.source.id !== mediaIdFor(track)) return;
    state.batch(() => {
        state.local.mediaState = mediaState;
        state.local.isLoading = mediaState.status === 'loading';
        state.local.errorMessage = mediaState.error?.message ?? '';
    });
    const timeline = mediaState.timeline;
    if (
        timeline?.phase === 'ended' &&
        timeline.revision > handledEndedRevision &&
        timeline.updatedBy === Ubi.myUserId
    ) {
        handledEndedRevision = timeline.revision;
        onNext();
    }
});

/** @deprecated 終了処理は revision 付き MediaState で一度だけ実行する。 */
VPEvents.on('vp:media:ended', () => {
    // Host v2 互換イベント。v3 では上の stateChange が正本。
});

VPEvents.on('vp:playback:stop', () => {
    VPEvents.emit('vp:media:pause', {}, VPTarget.screen);
    VPEvents.emit('vp:media:seek', { time: 0 }, VPTarget.screen);
});

VPEvents.on('vp:track:replay', () => {
    VPEvents.emit('vp:media:seek', { time: 0 }, VPTarget.screen);
    VPEvents.emit('vp:media:play', {}, VPTarget.screen);
});

// 起動時に screen へ初期音量を通知 (起動順依存吸収)
queueMicrotask(() => VPEvents.emit('vp:media:volume', { volume: state.local.myVolume }, VPTarget.screen));

/**
 * video-player:controls Worker — 再生制御の中枢。
 *
 * 再生位置と再生状態は Host の MediaState / Server timeline を正本とする。
 * controls 自身は時計を進めず、UI intent を Ubi.media に渡すだけ。
 *
 * 動画は参照フィールド `screen` が指す core:media（ワールドに置いた動画面）に描く。
 * フィールド名 `screen` をそのまま Ubi.media の targetId に使う。
 * トラックは参照フィールド `playlist` が指すプレイリストに問い合わせる。
 */

import type { ComponentConfig, MediaSource, MediaState } from 'ubichill';
import { VPEvents, VPRoute } from './events';

export const config: ComponentConfig = {
    watchEntityTypes: ['video-player:controls'],
    watchScope: 'entity',
    defaultTransform: { x: 0, y: 370, z: 198, w: 640, h: 60 },
    capabilities: ['event:emit', 'media:control', 'net:fetch', 'scene:read', 'scene:update', 'ui:render'],
    dataFields: {
        screen: {
            type: 'entityRef',
            component: 'core:media',
            required: true,
            label: 'スクリーン',
            help: '動画を映す場所（core:media を持つ Entity）',
        },
        playlist: {
            type: 'entityRef',
            component: 'video-player:playlist',
            required: true,
            label: 'プレイリスト',
            help: '再生するトラックの一覧（video-player:playlist を持つ Entity）',
        },
    },
};

/** 参照フィールド `screen`（core:media）。Ubi.media の targetId。 */
const SCREEN = 'screen';

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
import { errorMessageOf, serviceFetch } from './lib/serviceApi';
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

function apiBase(): string {
    return state.local.apiBase.trim().replace(/\/$/, '') || DEFAULT_API_BASE;
}

function resolvePath(track: Track): string {
    const presentation = state.local.audioOnly ? 'audio' : 'video';
    return `/resolve/${extractVideoId(track.id)}?mode=${track.mode}&presentation=${presentation}`;
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
        const base = apiBase();
        const response = await serviceFetch(base, resolvePath(track));
        if (revision !== loadRequestRevision || state.local.currentTrack?.id !== track.id) return;
        if (!response.ok) throw new Error('resolve request failed');
        const descriptor = JSON.parse(response.body) as PlaybackDescriptor;
        const expectedOrigin = new URL(base).origin;
        const sourceOrigin = new URL(descriptor.source.url).origin;
        const sourceType = descriptor.source.type;
        if (
            (sourceType !== 'hls' && sourceType !== 'file') ||
            descriptor.source.id !== mediaIdFor(track) ||
            sourceOrigin !== expectedOrigin
        ) {
            throw new Error('invalid playback descriptor');
        }
        const presentation = state.local.audioOnly ? 'audio' : 'video';
        Ubi.media.setVisible(presentation === 'video', SCREEN);
        Ubi.media.load({ targetId: SCREEN, source: descriptor.source, presentation, sync: 'shared' });
    } catch (error) {
        if (revision !== loadRequestRevision || state.local.currentTrack?.id !== track.id) return;
        state.batch(() => {
            state.local.isLoading = false;
            state.local.errorMessage = errorMessageOf(error, '再生URLを解決できませんでした');
        });
    }
}

// ── UI アクション ──────────────────────────────────
const onSeek = (time: number): void => {
    Ubi.media.seek(time, SCREEN);
};
const onPlayToggle = (): void => {
    const media = state.local.mediaState;
    const playing = isMediaPlaying(media);
    if (playing) Ubi.media.pause(SCREEN);
    else Ubi.media.play(SCREEN);
};
const onPrev = (): void => {
    VPEvents.emit('vp:track:prev', {}, VPRoute.playlist);
};
const onNext = (): void => {
    VPEvents.emit('vp:track:next', { loop: state.local.loop, shuffle: state.local.shuffle }, VPRoute.playlist);
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
state.onChange('myVolume', (v) => Ubi.media.setVolume(v, SCREEN));

// ── レンダリング（自動追跡: MediaState 通知で再描画） ─────
export default function ControlsView() {
    const track = state.local.currentTrack;
    const media = state.local.mediaState;
    const thumb = track ? thumbnailUrl(track.id) : '';
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

let handledEndedRevision = 0;
/** 自動再生を済ませた読み込み（同じ読み込みで二度再生しない）。 */
let autoplayedLoadId: string | null = null;
VPEvents.on('media:stateChange', (mediaState) => {
    if (mediaState.targetId !== SCREEN) return;
    const track = state.local.currentTrack;
    if (track && mediaState.source?.id && mediaState.source.id !== mediaIdFor(track)) return;
    state.batch(() => {
        state.local.mediaState = mediaState;
        state.local.isLoading = mediaState.status === 'loading';
        state.local.errorMessage = mediaState.error?.message ?? '';
    });
    // 作成時の自動再生: 部屋で最初の読み込み（timeline の revision が 1）が再生できる状態になったときだけ
    if (
        state.local.autoplay &&
        mediaState.status === 'ready' &&
        mediaState.timeline?.revision === 1 &&
        mediaState.loadId !== null &&
        mediaState.loadId !== autoplayedLoadId
    ) {
        autoplayedLoadId = mediaState.loadId;
        Ubi.media.play(SCREEN);
    }
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

VPEvents.on('vp:playback:stop', () => {
    Ubi.media.pause(SCREEN);
    Ubi.media.seek(0, SCREEN);
});

VPEvents.on('vp:track:replay', () => {
    Ubi.media.seek(0, SCREEN);
    Ubi.media.play(SCREEN);
});

Ubi.media.setVolume(state.local.myVolume, SCREEN);
// プレイリストより後に起動したときのために、現トラックを問い合わせる
VPEvents.emit('vp:track:request', {}, VPRoute.playlist);

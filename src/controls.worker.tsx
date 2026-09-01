/**
 * video-player:controls Worker — 再生制御の中枢。
 *
 * 共有時計モデル:
 *  - baselineTime (sync): 「最後にゼロ起点に固定した動画内位置」秒
 *  - playEpoch   (sync): 「再生を開始した wall-clock ms」 (isPlaying=true のときだけ意味を持つ)
 *  - 現在位置 = isPlaying ? baselineTime + (now - playEpoch) / 1000 : baselineTime
 *
 *  各ユーザーは Date.now() を使って独立に現在位置を算出するため、毎秒の broadcast は不要。
 *  play / pause / seek / track-change の瞬間だけ baselineTime / playEpoch を同期する。
 *
 * その他の同期項目:
 *  - isPlaying / duration / loop / shuffle / apiBase : 共有 + 永続
 *  - myVolume (perUser): 各ユーザー音量
 *
 * Worker 間通信は VPEvents (型付き) のみ。
 */

import type { ComponentConfig, Entity, System } from 'ubichill';
import { VPEvents, VPTarget } from './events';

export const config: ComponentConfig = {
    watchEntityTypes: ['video-player:controls'],
    watchScope: 'entity',
    defaultTransform: { x: 0, y: 370, z: 198, w: 640, h: 60 },
    capabilities: ['event:emit', 'scene:read', 'scene:update', 'ui:render'],
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
    VolumeHighIcon,
    VolumeLowIcon,
    VolumeMediumIcon,
    VolumeMuteIcon,
    VideoIcon,
} from './icons';
import { computeCurrentTime, formatTime, isClockOverrun } from './lib/playback';
import { extractVideoId, thumbnailUrl } from './lib/youtube';
import type { LoopMode, Track } from './types';

const DEFAULT_API_BASE = 'https://videoplayer.youkan.uk';

const state = Ubi.state.define({
    // ── 共有 + 永続。runtime 専用は editable:false で Inspector から除外 ──
    isPlaying: Ubi.state.sync(false, {
        label: '作成時に自動再生',
        help: 'オンにすると、インスタンス作成時にプレイリスト先頭から再生を開始します',
    }),
    baselineTime: Ubi.state.sync(0, { editable: false }),
    playEpoch: Ubi.state.sync(0, { editable: false }),
    duration: Ubi.state.sync(0, { editable: false }),
    loop: Ubi.state.sync<LoopMode>('none', { label: 'ループ', options: ['none', 'one', 'all'] }),
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
    // ClockSystem が 100ms ごとにインクリメントする進行バー時計用カウンタ。
    // ControlsView がこれを読むことで、Date.now() 経過による再描画が自動追跡される。
    // React の useEffect + setInterval → setState と等価なパターン。
    _tick: 0,
});

// ── ヘルパー ────────────────────────────────────────
function currentTime(): number {
    return computeCurrentTime(state.local);
}

function buildTrackUrl(track: Track): string {
    const base = state.local.apiBase.trim() || DEFAULT_API_BASE;
    const endpoint =
        track.mode === 'live' ? (state.local.audioOnly ? 'live-audio' : 'live') : state.local.audioOnly ? 'audio' : 'video';
    return `${base}/${endpoint}/${extractVideoId(track.id)}`;
}

function loadCurrentTrack(): void {
    const track = state.local.currentTrack;
    if (!track) return;
    state.batch(() => {
        state.local.isLoading = true;
        state.local.errorMessage = '';
    });
    VPEvents.emit(
        'vp:media:load',
        { url: buildTrackUrl(track), mode: track.mode, kind: state.local.audioOnly ? 'audio' : 'video' },
        VPTarget.screen,
    );
}

// ── screen / playlist へのエイリアス (events.ts に集約) ──

let syncScheduled = false;
function scheduleSyncVideo(): void {
    if (syncScheduled) return;
    syncScheduled = true;
    queueMicrotask(() => {
        syncScheduled = false;
        const isLive = state.local.currentTrack?.mode === 'live';
        if (!isLive && state.local.duration > 0) {
            VPEvents.emit('vp:media:seek', { time: currentTime() }, VPTarget.screen);
        }
        if (state.local.isPlaying) VPEvents.emit('vp:media:play', {}, VPTarget.screen);
        else VPEvents.emit('vp:media:pause', {}, VPTarget.screen);
    });
}

// ── UI アクション ──────────────────────────────────
const onSeek = (time: number): void => {
    state.batch(() => {
        state.local.baselineTime = time;
        if (state.local.isPlaying) state.local.playEpoch = Date.now();
    });
};
const onPlayToggle = (): void => {
    if (state.local.isPlaying) {
        state.batch(() => {
            state.local.baselineTime = currentTime();
            state.local.isPlaying = false;
        });
    } else {
        state.batch(() => {
            const dur = state.local.duration;
            if (dur > 0 && state.local.baselineTime >= dur - 0.5) {
                state.local.baselineTime = 0;
            }
            state.local.playEpoch = Date.now();
            state.local.isPlaying = true;
        });
    }
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
state.onChange('isPlaying', scheduleSyncVideo);
state.onChange('baselineTime', scheduleSyncVideo);
state.onChange('playEpoch', scheduleSyncVideo);
state.onChange('audioOnly', loadCurrentTrack);
state.onChange('myVolume', (v) => {
    VPEvents.emit('vp:media:volume', { volume: v }, VPTarget.screen);
});

// ── レンダリング（自動追跡: 読んだキーが変わると自動再描画） ─────
// sandbox.worker.ts が export default を検出して自動で Ubi.ui.render(..., "default") する。
// _tick を読むことで、ClockSystem による 100ms 間隔の進行バー更新も自動追跡に乗る。
export default function ControlsView() {
    const _t = state.local._tick;
    const track = state.local.currentTrack;
    const thumb = track ? track.thumbnail || thumbnailUrl(track.id) : '';
    const ct = currentTime();
    const progress = state.local.duration > 0 ? (ct / state.local.duration) * 100 : 0;
    const isLive = track?.mode === 'live';
    const isLoading = state.local.isLoading;
    const errorMessage = state.local.errorMessage;
    const volume = state.local.myVolume;
    const VolumeIcon =
        volume === 0 ? VolumeMuteIcon : volume < 0.3 ? VolumeLowIcon : volume < 0.7 ? VolumeMediumIcon : VolumeHighIcon;
    const LoopIconComp = state.local.loop === 'one' ? RepeatOneIcon : RepeatIcon;
    const isPlaying = state.local.isPlaying;
    const empty = state.local.totalTracks === 0;
    const seekBackground = isLoading
        ? 'linear-gradient(90deg, rgba(255,255,255,0.05) 0%, rgba(0,122,255,0.5) 50%, rgba(255,255,255,0.05) 100%)'
        : `linear-gradient(to right, #007aff ${progress}%, rgba(255,255,255,0.2) ${progress}%)`;
    const seekDisabled = isLoading || state.local.duration <= 0 || isLive;

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
                max={String(state.local.duration > 0 ? state.local.duration : 100)}
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
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: '12px' }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: '8px', flex: '1', minWidth: '0' }}>
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
                            {formatTime(ct)} /{' '}
                            {state.local.duration > 0
                                ? formatTime(state.local.duration)
                                : isLive
                                  ? 'LIVE'
                                  : '--:--'}
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
                    <span style={{ color: 'rgba(255,255,255,0.8)', display: 'flex', alignItems: 'center' }}>
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
    children:
        | import('ubichill/jsx-runtime').JSX.Element
        | import('ubichill/jsx-runtime').JSX.Element[]
        | null;
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
    const isFirstLoad = prev === null;
    const needLoad = prevId !== nextId;
    const changed = !isFirstLoad && needLoad;

    state.batch(() => {
        state.local.currentTrack = track;
        state.local.currentIndex = index;
        state.local.totalTracks = total;

        if (changed) {
            state.local.baselineTime = 0;
            state.local.playEpoch = Date.now();
            state.local.duration = 0;
        }

        if (needLoad && track) {
            state.local.isLoading = true;
        }
    });

    if (needLoad && track) {
        loadCurrentTrack();
    }
});

VPEvents.on('vp:media:loaded', ({ duration }) => {
    state.batch(() => {
        if (duration > 0) state.local.duration = duration;
        state.local.isLoading = false;
        state.local.errorMessage = '';

        if (isClockOverrun(state.local)) {
            state.local.baselineTime = 0;
            state.local.playEpoch = Date.now();
        }
    });
    scheduleSyncVideo();
});

VPEvents.on('vp:media:error', ({ message }) => {
    state.batch(() => {
        state.local.isLoading = false;
        state.local.errorMessage = message || '動画を読み込めませんでした';
    });
});

// ローカルの実再生位置が共有時計から遅れた場合だけ、定期的に追いつかせる。
// 小さなドリフトでシークを繰り返さないよう、閾値とクールダウンを設ける。
let lastDriftCorrectionAt = 0;
const DRIFT_THRESHOLD_SECONDS = 2;
const DRIFT_CORRECTION_COOLDOWN_MS = 3000;
VPEvents.on('vp:media:timeUpdate', ({ currentTime: actualTime, duration }) => {
    if (duration > 0 && state.local.duration <= 0) state.local.duration = duration;
    if (!state.local.isPlaying || state.local.isLoading || state.local.currentTrack?.mode === 'live') return;

    const now = Date.now();
    const expectedTime = currentTime();
    if (
        Math.abs(expectedTime - actualTime) >= DRIFT_THRESHOLD_SECONDS &&
        now - lastDriftCorrectionAt >= DRIFT_CORRECTION_COOLDOWN_MS
    ) {
        lastDriftCorrectionAt = now;
        VPEvents.emit('vp:media:seek', { time: expectedTime }, VPTarget.screen);
    }
});

VPEvents.on('vp:media:ended', () => {
    VPEvents.emit('vp:track:next', { loop: state.local.loop, shuffle: state.local.shuffle }, VPTarget.playlist);
});

VPEvents.on('vp:playback:stop', () => {
    state.batch(() => {
        state.local.baselineTime = 0;
        state.local.playEpoch = Date.now();
        state.local.isPlaying = false;
    });
});

VPEvents.on('vp:track:replay', () => {
    state.batch(() => {
        state.local.baselineTime = 0;
        state.local.playEpoch = Date.now();
        if (!state.local.isPlaying) state.local.isPlaying = true;
    });
});

// ── 進行バー時計（React の useEffect + setInterval → setState と等価） ──
// _tick をインクリメントするだけ。ControlsView が _tick を読んでいるため、
// 自動追跡が発火して再描画される。Ubi.ui.render() を手動で呼ぶ必要はない。
const accumulator = { ms: 0 };
const ClockSystem: System = (_e: Entity[], dt: number) => {
    if (!state.local.isPlaying) return;
    accumulator.ms += dt;
    if (accumulator.ms >= 100) {
        accumulator.ms = 0;
        state.local._tick += 1;
    }
};
Ubi.registerSystem(ClockSystem);

// 起動時に screen へ初期音量を通知 (起動順依存吸収)
queueMicrotask(() => VPEvents.emit('vp:media:volume', { volume: state.local.myVolume }, VPTarget.screen));

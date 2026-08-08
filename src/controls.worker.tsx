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
} from './icons';
import { computeCurrentTime, formatTime, isClockOverrun } from './lib/playback';
import { extractVideoId, thumbnailUrl } from './lib/youtube';
import type { LoopMode, Track } from './types';

const DEFAULT_API_BASE = 'https://videoplayer.youkan.uk';

const state = Ubi.state.define({
    // ── 共有 + 永続。runtime 専用は editable:false で Inspector から除外 ──
    // isPlaying は「作成時に自動再生するか」の初期値として編集可。true なら
    // インスタンス作成時に先頭から再生が始まる（vp:media:loaded のクロック補正で
    // stale な playEpoch をリセットして 0 から再生する）。
    isPlaying: Ubi.state.sync(false, {
        label: '作成時に自動再生',
        help: 'オンにすると、インスタンス作成時にプレイリスト先頭から再生を開始します',
    }),
    baselineTime: Ubi.state.sync(0, { editable: false }),
    playEpoch: Ubi.state.sync(0, { editable: false }),
    duration: Ubi.state.sync(0, { editable: false }),
    loop: Ubi.state.sync<LoopMode>('none', { label: 'ループ', options: ['none', 'one', 'all'] }),
    shuffle: Ubi.state.sync(false, { label: 'シャッフル' }),
    apiBase: Ubi.state.sync(DEFAULT_API_BASE, { label: 'API ベース URL' }),
    // ── 共有 + 永続 (per-user) ──
    myVolume: Ubi.state.sync(0.7, { perUser: true, editable: false }),
    // ── ローカル ──
    currentTrack: null as Track | null,
    currentIndex: 0,
    totalTracks: 0,
    // ロード中フラグ: vp:media:load 発行から vp:media:loaded 受信まで true。
    // シークバーをシマーアニメ化し、操作をブロックする。
    isLoading: false,
});

// ── ヘルパー ────────────────────────────────────────
// 共有時計の現在位置。計算ロジックは lib/playback に集約（state を渡すだけ）。
function currentTime(): number {
    return computeCurrentTime(state.local);
}

function buildTrackUrl(track: Track): string {
    const base = state.local.apiBase.trim() || DEFAULT_API_BASE;
    const endpoint = track.mode === 'live' ? 'live' : 'video';
    return `${base}/${endpoint}/${extractVideoId(track.id)}`;
}

// ── screen / playlist へのエイリアス (events.ts に集約) ──

/**
 * 共有時計の現在状態に合わせてローカル <video> を同期する。
 * isPlaying / baselineTime / playEpoch のどれかが変わったとき、または vp:media:loaded 時に呼ぶ。
 * Live モードは seek 不可なので play/pause のみ。
 */
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
    // baselineTime を seek 先に固定。isPlaying=true なら clock を restart。
    state.batch(() => {
        state.local.baselineTime = time;
        if (state.local.isPlaying) state.local.playEpoch = Date.now();
    });
};
const onPlayToggle = (): void => {
    if (state.local.isPlaying) {
        // pause: 現在位置を baselineTime に固定して時計を止める
        state.batch(() => {
            state.local.baselineTime = currentTime();
            state.local.isPlaying = false;
        });
    } else {
        // play: clock を起動。終端 (or 超過) なら 0 に巻き戻してから再生
        // (= ended 後の手動 play / 末尾までシークしてから play などに対応)
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

// ── state 変化 → ローカル video 同期 + 再描画 ─────────
// 共有時計を構成する 3 つは同じ syncVideo に集約 (microtask で dedupe)
state.onChange('isPlaying', () => {
    scheduleSyncVideo();
    render();
});
state.onChange('baselineTime', scheduleSyncVideo);
state.onChange('playEpoch', scheduleSyncVideo);
state.onChange('myVolume', (v) => {
    VPEvents.emit('vp:media:volume', { volume: v }, VPTarget.screen);
    render();
});
state.onChange('loop', render);
state.onChange('shuffle', render);
state.onChange('duration', render);
state.onChange('isLoading', render);

// ── レンダリング ──────────────────────────────────
function render(): void {
    const track = state.local.currentTrack;
    // サムネ: 検索由来は thumbnail を持つ。URL/ID 直書きのトラックは id から導出する。
    const thumb = track ? track.thumbnail || thumbnailUrl(track.id) : '';
    const ct = currentTime();
    const progress = state.local.duration > 0 ? (ct / state.local.duration) * 100 : 0;
    const isLive = track?.mode === 'live';
    const isLoading = state.local.isLoading;
    const volume = state.local.myVolume;
    const VolumeIcon =
        volume === 0 ? VolumeMuteIcon : volume < 0.3 ? VolumeLowIcon : volume < 0.7 ? VolumeMediumIcon : VolumeHighIcon;
    const LoopIconComp = state.local.loop === 'one' ? RepeatOneIcon : RepeatIcon;
    const isPlaying = state.local.isPlaying;
    const empty = state.local.totalTracks === 0;
    // シークバーの背景: 読み込み中は shimmer グラデを動かす、それ以外は進捗バー。
    const seekBackground = isLoading
        ? 'linear-gradient(90deg, rgba(255,255,255,0.05) 0%, rgba(0,122,255,0.5) 50%, rgba(255,255,255,0.05) 100%)'
        : `linear-gradient(to right, #007aff ${progress}%, rgba(255,255,255,0.2) ${progress}%)`;
    const seekDisabled = isLoading || state.local.duration <= 0 || isLive;

    Ubi.ui.render(
        () => (
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
                    {/* 左: トラック情報 */}
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
                                    color: '#fff',
                                    whiteSpace: 'nowrap',
                                    overflow: 'hidden',
                                    textOverflow: 'ellipsis',
                                }}
                            >
                                {track ? track.title || track.id : '---'}
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

                    {/* 中央: 再生 */}
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

                    {/* 右: shuffle / loop / volume */}
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
        ),
        'controls',
    );
}

function CtrlBtn({
    children,
    onClick,
    disabled = false,
    active = false,
}: {
    children:
        | import('ubichill/jsx-runtime').JSX.Element
        | import('ubichill/jsx-runtime').JSX.Element[]
        | null;
    onClick: () => void;
    disabled?: boolean;
    active?: boolean;
}): import('ubichill/jsx-runtime').JSX.Element {
    return (
        <button
            type="button"
            disabled={disabled}
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
    // 一度ローカル変数に取り出して TS の narrowing を効かせる (state.local.* の再アクセスは
    // narrowing が剥がれて null 可能性が残る)
    const prev = state.local.currentTrack;
    const prevId = prev?.id ?? null;
    const nextId = track?.id ?? null;
    const isFirstLoad = prev === null;
    const needLoad = prevId !== nextId;
    const changed = !isFirstLoad && needLoad;

    state.local.currentTrack = track;
    state.local.currentIndex = index;
    state.local.totalTracks = total;

    // トラックが変わった場合のみ共有時計をゼロから始める
    // (ただしローカル初回ロード時はサーバーの最新状態を受け取っただけなのでリセットしない)
    if (changed) {
        state.batch(() => {
            state.local.baselineTime = 0;
            state.local.playEpoch = Date.now();
            state.local.duration = 0;
        });
    }

    // 初回ロードか、トラックが変わった場合は load。シークバーは isLoading=true で
    // シマー表示にし、ユーザー操作をブロック。vp:media:loaded で false に戻る。
    if (needLoad && track) {
        state.local.isLoading = true;
        VPEvents.emit('vp:media:load', { url: buildTrackUrl(track), mode: track.mode }, VPTarget.screen);
    }
    render();
});

VPEvents.on('vp:media:loaded', ({ duration }) => {
    state.batch(() => {
        if (duration > 0) state.local.duration = duration;
        state.local.isLoading = false; // ローディング解除 → シークバーが通常表示に戻る

        // 不正な共有時計の補正:
        // 新規作成インスタンスは playEpoch が stale (例: 0) のまま isPlaying=true で
        // 始まることがある。その場合 baselineTime + 経過 が duration を遥かに超え、
        // シーク先が終端を突き抜けて「再生されない」状態になる。
        // duration が判明したこのタイミングで、生の経過位置が duration を超えていたら
        // 先頭から再生し直す（進行中インスタンスへの正当な join は範囲内なので影響なし）。
        if (isClockOverrun(state.local)) {
            state.local.baselineTime = 0;
            state.local.playEpoch = Date.now();
        }
    });
    // 共有時計の現在位置にローカル <video> を合わせる (再生中ならそのまま、停止中なら baselineTime)
    scheduleSyncVideo();
});

VPEvents.on('vp:media:ended', () => {
    // 次トラックを playlist にリクエスト
    VPEvents.emit('vp:track:next', { loop: state.local.loop, shuffle: state.local.shuffle }, VPTarget.playlist);
});

VPEvents.on('vp:playback:stop', () => {
    // playlist が末尾到達 (loop='none') を通知してきた → 0 に巻き戻して停止
    // (baselineTime を duration に固定すると次の Play で seekbar が max のまま再起動するため)
    state.batch(() => {
        state.local.baselineTime = 0;
        state.local.playEpoch = Date.now();
        state.local.isPlaying = false;
    });
});

VPEvents.on('vp:track:replay', () => {
    // playlist から同トラック replay 要求 (loop='one' or 単一トラック loop='all')。
    // 共有時計を 0 から再起動し、isPlaying は維持 (= 自動継続再生)。
    state.batch(() => {
        state.local.baselineTime = 0;
        state.local.playEpoch = Date.now();
        if (!state.local.isPlaying) state.local.isPlaying = true;
    });
});

// ── 進行バー時計のための tick ────────────────────────
// 進行バーが体感ジャギにならない程度の頻度で再描画 (≒ 10fps)。
// VNode 生成のみ・ネットワーク 0 なので CPU は誤差。
const accumulator = { ms: 0 };
const ClockSystem: System = (_e: Entity[], dt: number) => {
    if (!state.local.isPlaying) return;
    accumulator.ms += dt;
    if (accumulator.ms >= 100) {
        accumulator.ms = 0;
        render();
    }
};
Ubi.registerSystem(ClockSystem);

// 初期化
render();
// 起動時に screen へ初期音量を通知 (起動順依存吸収)
queueMicrotask(() => VPEvents.emit('vp:media:volume', { volume: state.local.myVolume }, VPTarget.screen));

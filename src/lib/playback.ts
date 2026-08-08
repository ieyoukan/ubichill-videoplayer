/**
 * 再生まわりの純粋ロジック（コンポーネントから独立した「システム」）。
 * controls / playlist など複数 worker から使う。状態は引数で受け取り、副作用は持たない。
 */

/** 共有時計の状態。controls の state.local がそのまま満たす形。 */
export interface PlaybackClock {
    isPlaying: boolean;
    baselineTime: number;
    playEpoch: number;
    duration: number;
}

/**
 * 共有時計から現在の再生位置 (秒) を算出する純関数。
 * 全クライアントが同じ baselineTime / playEpoch / isPlaying から計算するため、
 * Date.now() のクロックスキューが無視できる範囲なら位置は揃う。
 */
export function computeCurrentTime(c: PlaybackClock, now: number = Date.now()): number {
    if (!c.isPlaying) return c.baselineTime;
    const advanced = c.baselineTime + (now - c.playEpoch) / 1000;
    return c.duration > 0 ? Math.min(advanced, c.duration) : advanced;
}

/**
 * 共有時計が不正かどうか（新規作成インスタンスで playEpoch が stale=0 のまま
 * isPlaying=true 等）。生の経過位置が duration を超えていたら不正とみなす。
 */
export function isClockOverrun(c: PlaybackClock, now: number = Date.now()): boolean {
    if (!c.isPlaying || c.duration <= 0) return false;
    const raw = c.baselineTime + (now - c.playEpoch) / 1000;
    return raw > c.duration;
}

/** 秒を m:ss 表記にする。0 以下/非有限は '0:00'。 */
export function formatTime(sec: number): string {
    if (!Number.isFinite(sec) || sec <= 0) return '0:00';
    const m = Math.floor(sec / 60);
    const s = Math.floor(sec % 60);
    return `${m}:${s.toString().padStart(2, '0')}`;
}

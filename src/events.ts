/**
 * video-player Worker 間通信の単一スキーマ。
 *
 * 全 Worker (controls / playlist / search) が同じ schema を import するので
 * type 文字列の typo と payload 不一致はコンパイル時に弾かれる。
 *
 * `Ubi.event.define()` は呼び出し Worker 内に閉じた registry を作る (handlers は
 * Worker ローカル)。各 Worker が独立にこのモジュールを評価し、各々の Ubi 経由で
 * 新しいインスタンスを得る — シングルトンではない。
 *
 * 相手は階層ではなく参照フィールドで決める（ワールド作者が Inspector で配線する）:
 *
 *   controls.playlist ──vp:track:next/prev──▶ playlist
 *   search.playlist   ──vp:track:add───────▶ playlist
 *   playlist ──vp:track:current / replay / stop──▶ 自分を参照している controls・search（referrers）
 *
 * 動画は controls が参照する core:media（controls.screen）に、controls 自身の Ubi.media で描く。
 */

import type { EmitOptions, MediaState } from 'ubichill';

import type { LoopMode, Track } from './types';

type Empty = Record<string, never>;

/** Worker 間 emit の送り先。 */
export const VPRoute = {
    /** controls・search の参照フィールド `playlist` が指すプレイリスト。 */
    playlist: { ref: 'playlist' },
    /** playlist を参照している controls と search。 */
    referrers: { scope: 'referrers' },
    /** playlist を参照している controls だけ。 */
    controls: { scope: 'referrers', targetType: 'video-player:controls' },
} as const satisfies Record<string, EmitOptions>;

export const VPEvents = Ubi.event.define<{
    // ── controls → playlist (トラック操作) ──
    'vp:track:next': { loop: LoopMode; shuffle: boolean };
    'vp:track:prev': Empty;
    // ── controls → playlist (起動時に現トラックを問い合わせる。起動順に依存しないため) ──
    'vp:track:request': Empty;
    // ── playlist → 参照している controls・search (現トラック通知) ──
    'vp:track:current': { track: Track | null; index: number; total: number };
    // ── search → playlist (新規トラック追加) ──
    'vp:track:add': { track: Track };
    // ── playlist → controls (末尾到達による再生停止) ──
    'vp:playback:stop': Empty;
    // ── playlist → controls (同トラック replay: loop='one' or 単一トラック loop='all') ──
    'vp:track:replay': Empty;
    // ── Ubi.media 由来 (controls 自身の <video> の状態) ──
    'media:stateChange': MediaState;
}>();

/**
 * video-player Worker 間通信の単一スキーマ。
 *
 * 全 Worker (controls / screen / playlist / search) が同じ schema を import するので
 * type 文字列の typo と payload 不一致はコンパイル時に弾かれる。
 *
 * `Ubi.event.define()` は呼び出し Worker 内に閉じた registry を作る (handlers は
 * Worker ローカル)。各 Worker が独立にこのモジュールを評価し、各々の Ubi 経由で
 * 新しいインスタンスを得る — シングルトンではない。
 *
 * ルーティングターゲット (VPTarget.*) もここに集約し、Worker 側で散在していた
 * EmitOptions 定数を一元管理する。
 */

import type { EmitOptions } from 'ubichill';

import type { LoopMode, Track } from './types';

type Empty = Record<string, never>;

/**
 * Worker 間 emit のルーティング先。
 *
 *   controls ──vp:media:*──▶ screen     (再生コマンド)
 *   screen   ──vp:media:*──▶ controls   (DOM <video> 状態通知)
 *   controls ──vp:track:*──▶ playlist   (トラック操作)
 *   search   ──vp:track:add──▶ playlist (新規トラック追加)
 *   playlist ──vp:track:current──▶ siblings (現トラック通知)
 *   playlist ──vp:playback:stop──▶ controls (末尾到達)
 */
export const VPTarget = {
    screen: { scope: 'siblings', targetType: 'video-player:screen' },
    controls: { scope: 'siblings', targetType: 'video-player:controls' },
    playlist: { scope: 'siblings', targetType: 'video-player:playlist' },
    siblings: { scope: 'siblings' },
} as const satisfies Record<string, EmitOptions>;

export const VPEvents = Ubi.event.define<{
    // ── controls → screen (再生コマンド) ──
    'vp:media:load': { url: string; mode: 'live' | 'video' };
    'vp:media:play': Empty;
    'vp:media:pause': Empty;
    'vp:media:seek': { time: number };
    'vp:media:volume': { volume: number };
    // ── screen → controls (DOM <video> 状態通知) ──
    'vp:media:loaded': { duration: number };
    'vp:media:ended': Empty;
    // ── controls → playlist (トラック操作) ──
    'vp:track:next': { loop: LoopMode; shuffle: boolean };
    'vp:track:prev': Empty;
    // ── playlist → siblings (現トラック通知) ──
    'vp:track:current': { track: Track | null; index: number; total: number };
    // ── search → playlist (新規トラック追加) ──
    'vp:track:add': { track: Track };
    // ── playlist → controls (末尾到達による再生停止) ──
    'vp:playback:stop': Empty;
    // ── playlist → controls (同トラック replay: loop='one' or 単一トラック loop='all') ──
    'vp:track:replay': Empty;
    // ── Ubi.media SDK 由来 (DOM <video> イベント) ──
    'media:loaded': { targetId: string; duration: number };
    'media:ended': { targetId: string };
    'media:error': { targetId: string; message: string };
}>();

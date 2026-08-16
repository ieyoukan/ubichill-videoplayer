/**
 * video-player:screen Worker — メディア実行者。
 *
 * 状態を持たない。VPEvents 経由で受信したコマンドを Ubi.media.* に流し、
 * Ubi.media SDK からの DOM <video> イベント (media:*) を controls へ転送するだけ。
 * 黒 16:9 の背景は host 側の <video> 要素が標準で持つ。
 */

import type { ComponentConfig } from 'ubichill';
import { VPEvents, VPTarget } from './events';

export const config: ComponentConfig = {
    mediaTargets: ['main'],
    watchEntityTypes: [],
    watchScope: 'entity',
    defaultTransform: { x: 0, y: 0, z: 2, w: 640, h: 360 },
    capabilities: ['event:emit', 'media:control'],
};

const TARGET = 'main';

// 動画要素は常時表示
Ubi.media.setVisible(true, TARGET);

// ── controls からのコマンドを Ubi.media に流す ───────
VPEvents.on('vp:media:load', ({ url, mode }) => {
    Ubi.media.load(url, TARGET, mode === 'live' ? 'hls' : 'auto', 'video');
});
VPEvents.on('vp:media:play', () => Ubi.media.play(TARGET));
VPEvents.on('vp:media:pause', () => Ubi.media.pause(TARGET));
VPEvents.on('vp:media:seek', ({ time }) => Ubi.media.seek(time, TARGET));
VPEvents.on('vp:media:volume', ({ volume }) => Ubi.media.setVolume(volume, TARGET));

// ── <video> 要素のイベント (Ubi.media SDK) → controls へ転送 ──
// 進捗(currentTime)は controls の共有時計から算出するため timeUpdate は転送しない
// （毎フレームのワーカー間通信を避ける）。loaded / ended / error のみ転送する。
VPEvents.on('media:loaded', ({ targetId, duration }) => {
    if (targetId !== TARGET) return;
    VPEvents.emit('vp:media:loaded', { duration }, VPTarget.controls);
});
VPEvents.on('media:ended', ({ targetId }) => {
    if (targetId !== TARGET) return;
    VPEvents.emit('vp:media:ended', {}, VPTarget.controls);
});
VPEvents.on('media:error', ({ targetId, message }) => {
    if (targetId !== TARGET) return;
    Ubi.log(`[screen] media error: ${message}`, 'warn');
});

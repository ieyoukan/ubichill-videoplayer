/**
 * video-player:screen Worker — メディア実行者。
 *
 * 状態を持たない。VPEvents 経由で受信したコマンドを Ubi.media.* に流し、
 * media SDK からの DOM <video> イベント (media:*) を controls へ転送するだけ。
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
VPEvents.on('vp:media:load', ({ source, presentation }) => {
    Ubi.media.setVisible(presentation === 'video', TARGET);
    Ubi.media.load({
        targetId: TARGET,
        source,
        presentation,
        sync: 'shared',
    });
});
VPEvents.on('vp:media:play', () => Ubi.media.play(TARGET));
VPEvents.on('vp:media:pause', () => Ubi.media.pause(TARGET));
VPEvents.on('vp:media:seek', ({ time }) => Ubi.media.seek(time, TARGET));
VPEvents.on('vp:media:volume', ({ volume }) => Ubi.media.setVolume(volume, TARGET));

// ── <video> 要素のイベント (Ubi.media SDK) → controls へ転送 ──
// deprecated compatibility events. New controls use media:stateChange below.
VPEvents.on('media:timeUpdate', ({ targetId, currentTime, duration }) => {
    if (targetId !== TARGET) return;
    VPEvents.emit('vp:media:timeUpdate', { currentTime, duration }, VPTarget.controls);
});
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
    VPEvents.emit('vp:media:error', { message }, VPTarget.controls);
});

// v3: Host の単一 state machine と server-authoritative timeline をそのまま controls へ渡す。
VPEvents.on('media:stateChange', (mediaState) => {
    if (mediaState.targetId !== TARGET) return;
    VPEvents.emit('vp:media:stateChange', mediaState, VPTarget.controls);
});

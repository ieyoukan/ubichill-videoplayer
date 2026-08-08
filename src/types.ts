/**
 * video-player 共通型。
 */

export interface Track {
    id: string;
    title: string;
    thumbnail: string;
    duration: number;
    mode: 'live' | 'video';
}

export interface SearchResult {
    id: string;
    title: string;
    thumbnail: string;
    duration: number;
}

export type LoopMode = 'none' | 'one' | 'all';

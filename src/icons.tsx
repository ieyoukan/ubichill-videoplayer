/**
 * video-player UI アイコン集。
 */
import type { JSX } from 'ubichill/jsx-runtime';

type IconProps = { size?: number };

export const PlayIcon = ({ size = 24 }: IconProps): JSX.Element => (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="currentColor">
        <path d="M8 5v14l11-7z" />
    </svg>
);

export const PauseIcon = ({ size = 24 }: IconProps): JSX.Element => (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="currentColor">
        <path d="M6 19h4V5H6v14zm8-14v14h4V5h-4z" />
    </svg>
);

export const SkipPrevIcon = ({ size = 24 }: IconProps): JSX.Element => (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="currentColor">
        <path d="M6 6h2v12H6zm3.5 6l8.5 6V6z" />
    </svg>
);

export const SkipNextIcon = ({ size = 24 }: IconProps): JSX.Element => (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="currentColor">
        <path d="M6 18l8.5-6L6 6v12zM16 6v12h2V6h-2z" />
    </svg>
);

export const RepeatIcon = ({ size = 16 }: IconProps): JSX.Element => (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="currentColor">
        <path d="M7 7h10v3l4-4-4-4v3H5v6h2V7zm10 10H7v-3l-4 4 4 4v-3h12v-6h-2v4z" />
    </svg>
);

export const RepeatOneIcon = ({ size = 16 }: IconProps): JSX.Element => (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="currentColor">
        <path d="M7 7h10v3l4-4-4-4v3H5v6h2V7zm10 10H7v-3l-4 4 4 4v-3h12v-6h-2v4zm-4-2V9h-1l-2 1v1h1.5v4H13z" />
    </svg>
);

export const ShuffleIcon = ({ size = 16 }: IconProps): JSX.Element => (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="currentColor">
        <path d="M10.59 9.17L5.41 4 4 5.41l5.17 5.17 1.42-1.41zM14.5 4l2.04 2.04L4 18.59 5.41 20 17.96 7.46 20 9.5V4h-5.5zm.33 9.41l-1.41 1.41 3.13 3.13L14.5 20H20v-5.5l-2.04 2.04-3.13-3.13z" />
    </svg>
);

export const VolumeHighIcon = ({ size = 16 }: IconProps): JSX.Element => (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="currentColor">
        <path d="M3 9v6h4l5 5V4L7 9H3zm13.5 3c0-1.77-1.02-3.29-2.5-4.03v8.05c1.48-.73 2.5-2.25 2.5-4.02zM14 3.23v2.06c2.89.86 5 3.54 5 6.71s-2.11 5.85-5 6.71v2.06c4.01-.91 7-4.49 7-8.77s-2.99-7.86-7-8.77z" />
    </svg>
);

export const VolumeMediumIcon = ({ size = 16 }: IconProps): JSX.Element => (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="currentColor">
        <path d="M18.5 12c0-1.77-1.02-3.29-2.5-4.03v8.05c1.48-.73 2.5-2.25 2.5-4.02zM5 9v6h4l5 5V4L9 9H5z" />
    </svg>
);

export const VolumeLowIcon = ({ size = 16 }: IconProps): JSX.Element => (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="currentColor">
        <path d="M7 9v6h4l5 5V4l-5 5H7z" />
    </svg>
);

export const VolumeMuteIcon = ({ size = 16 }: IconProps): JSX.Element => (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="currentColor">
        <path d="M16.5 12c0-1.77-1.02-3.29-2.5-4.03v2.21l2.45 2.45c.03-.2.05-.41.05-.63zm2.5 0c0 .94-.2 1.82-.54 2.64l1.51 1.51C20.63 14.91 21 13.5 21 12c0-4.28-2.99-7.86-7-8.77v2.06c2.89.86 5 3.54 5 6.71zM4.27 3L3 4.27 7.73 9H3v6h4l5 5v-6.73l4.25 4.25c-.67.52-1.42.93-2.25 1.18v2.06c1.38-.31 2.63-.95 3.69-1.81L19.73 21 21 19.73l-9-9L4.27 3zM12 4L9.91 6.09 12 8.18V4z" />
    </svg>
);

export const VideoIcon = ({ size = 16 }: IconProps): JSX.Element => (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="currentColor">
        <path d="M17 10.5V7c0-.55-.45-1-1-1H4c-.55 0-1 .45-1 1v10c0 .55.45 1 1 1h12c.55 0 1-.45 1-1v-3.5l4 4v-11l-4 4z" />
    </svg>
);

export const ListIcon = ({ size = 16 }: IconProps): JSX.Element => (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="currentColor">
        <path d="M3 13h2v-2H3v2zm0 4h2v-2H3v2zm0-8h2V7H3v2zm4 4h14v-2H7v2zm0 4h14v-2H7v2zM7 7v2h14V7H7z" />
    </svg>
);

export const ExpandIcon = ({ size = 16 }: IconProps): JSX.Element => (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="currentColor">
        <path d="M15 3l2.3 2.3-2.89 2.87 1.42 1.42L18.7 6.7 21 9V3h-6zM3 9l2.3-2.3 2.87 2.89 1.42-1.42L6.7 5.3 9 3H3v6zm6 12l-2.3-2.3 2.89-2.87-1.42-1.42L5.3 17.3 3 15v6h6zm12-6l-2.3 2.3-2.87-2.89-1.42 1.42 2.89 2.87L15 21h6v-6z" />
    </svg>
);

export const TrashIcon = ({ size = 14 }: IconProps): JSX.Element => (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="currentColor">
        <path d="M6 19c0 1.1.9 2 2 2h8c1.1 0 2-.9 2-2V7H6v12zM19 4h-3.5l-1-1h-5l-1 1H5v2h14V4z" />
    </svg>
);

export const PlaySmallIcon = (): JSX.Element => (
    <svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor">
        <path d="M8 5v14l11-7z" />
    </svg>
);

export const SearchIcon = ({ size = 14 }: IconProps): JSX.Element => (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="currentColor">
        <path d="M15.5 14h-.79l-.28-.27C15.41 12.59 16 11.11 16 9.5 16 5.91 13.09 3 9.5 3S3 5.91 3 9.5 5.91 16 9.5 16c1.61 0 3.09-.59 4.23-1.57l.27.28v.79l5 4.99L20.49 19l-4.99-5zm-6 0C7.01 14 5 11.99 5 9.5S7.01 5 9.5 5 14 7.01 14 9.5 11.99 14 9.5 14z" />
    </svg>
);

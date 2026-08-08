/**
 * YouTube の URL / 動画ID を扱う純関数群。
 * controls / playlist / search の各 worker から使う。
 */

const URL_PATTERNS = [
    /[?&]v=([\w-]{6,20})/,
    /youtu\.be\/([\w-]{6,20})/,
    /youtube\.com\/(?:live|embed|shorts)\/([\w-]{6,20})/,
] as const;
const RAW_ID_RE = /^[\w-]{6,20}$/;

/**
 * 入力（URL or 生ID）から動画 ID を取り出す。妥当でなければ null。
 * 対応 URL: watch?v= / youtu.be / live・embed・shorts。生 ID はそのまま。
 * 入力検証（無効な URL を弾く）に使う。
 */
export function parseVideoId(input: string): string | null {
    const s = (input ?? '').trim();
    if (!s) return null;
    for (const re of URL_PATTERNS) {
        const m = re.exec(s);
        if (m) return m[1];
    }
    return RAW_ID_RE.test(s) ? s : null;
}

/**
 * 動画 ID を取り出す寛容版。妥当でなければ入力をそのまま返す（呼び出し側/バックエンドで検証）。
 * 再生 URL 構築に使う。
 */
export function extractVideoId(input: string): string {
    return parseVideoId(input) ?? (input ?? '').trim();
}

/**
 * 動画ID / URL からサムネイル URL を導出する。妥当でなければ空文字（壊れた img を出さない）。
 * 検索由来のトラックは thumbnail を持つが、エディタで URL/ID 直書きしたものは持たないので補完する。
 */
export function thumbnailUrl(idOrUrl: string): string {
    const id = parseVideoId(idOrUrl);
    return id ? `https://i.ytimg.com/vi/${id}/mqdefault.jpg` : '';
}

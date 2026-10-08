/** video-player の匿名利用セッション。サービス自身の短命トークンを API に付ける。 */
import type { RpcNetworkFetchResult } from 'ubichill';

type ServiceToken = { token: string; expiresAt: number };
const tokens = new Map<string, ServiceToken>();
const pending = new Map<string, Promise<ServiceToken>>();
const REUSE_MARGIN_MS = 30_000;

export class ServiceApiError extends Error {
    constructor(
        message: string,
        readonly status?: number,
    ) {
        super(message);
        this.name = 'ServiceApiError';
    }
}

function checkResponse(response: RpcNetworkFetchResult): void {
    if (response.status === 429) {
        throw new ServiceApiError('混み合っています。しばらく待ってから再度お試しください', 429);
    }
    if (response.status === 401 || response.status === 403) {
        throw new ServiceApiError('利用トークンを確認できませんでした', response.status);
    }
    const error = (response as RpcNetworkFetchResult & { error?: { message?: string } }).error;
    if (error?.message) {
        throw new ServiceApiError(error.message);
    }
}

async function issueToken(apiBase: string): Promise<ServiceToken> {
    const response = (await Ubi.fetch(`${apiBase}/session`, {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ modId: 'video-player' }),
    })) as RpcNetworkFetchResult;
    checkResponse(response);
    if (!response.ok) throw new ServiceApiError('利用トークンを受け取れませんでした', response.status);
    try {
        const result: unknown = JSON.parse(response.body);
        if (typeof result !== 'object' || result === null) throw new Error('invalid response');
        const value = result as Record<string, unknown>;
        if (
            typeof value.token !== 'string' ||
            value.token.length === 0 ||
            typeof value.expiresAt !== 'number' ||
            !Number.isFinite(value.expiresAt) ||
            value.expiresAt <= Date.now()
        )
            throw new Error('invalid token');
        return { token: value.token, expiresAt: value.expiresAt };
    } catch {
        throw new ServiceApiError('利用トークンの応答が不正です');
    }
}

function getToken(apiBase: string): Promise<ServiceToken> {
    const cached = tokens.get(apiBase);
    if (cached && cached.expiresAt - Date.now() > REUSE_MARGIN_MS) return Promise.resolve(cached);
    const existing = pending.get(apiBase);
    if (existing) return existing;
    const created = issueToken(apiBase)
        .then((result) => {
            tokens.set(apiBase, result);
            return result;
        })
        .finally(() => pending.delete(apiBase));
    pending.set(apiBase, created);
    return created;
}

/** apiBase 配下へ GET。期限前に取り直し、サーバー再起動などの 401 では 1 回だけ再発行する。 */
export async function serviceFetch(apiBase: string, path: string): Promise<RpcNetworkFetchResult> {
    const base = apiBase.replace(/\/+$/, '');
    const send = (token: ServiceToken) =>
        Ubi.fetch(`${base}${path}`, {
            headers: { authorization: `Bearer ${token.token}` },
        }) as Promise<RpcNetworkFetchResult>;
    const token = await getToken(base);
    const response = await send(token);
    if (response.status === 401) {
        if (tokens.get(base)?.token === token.token) tokens.delete(base);
        const retried = await send(await getToken(base));
        checkResponse(retried);
        return retried;
    }
    checkResponse(response);
    return response;
}

export function errorMessageOf(error: unknown, fallback: string): string {
    return error instanceof ServiceApiError ? error.message : fallback;
}

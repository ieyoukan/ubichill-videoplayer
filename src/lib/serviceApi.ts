/**
 * video-player の API サーバーへの依頼。Ubichill のサービストークンを付け、ログイン中の利用者からだと示す。
 * サーバーはトークンの無い依頼を受け付けず、利用者ごとに回数を制限する。
 */
import type { RpcNetworkFetchResult } from 'ubichill';

type IdentityModule = {
    token(audience: string): Promise<{ token: string; expiresAt: number }>;
};

/** 公開版 ubichill 2.x の型には Ubi.identity がまだ無い。対応した Host にだけある機能として取り出す。 */
const identity = (): IdentityModule | undefined => (Ubi as unknown as { identity?: IdentityModule }).identity;

export class ServiceApiError extends Error {
    constructor(
        message: string,
        readonly status?: number,
    ) {
        super(message);
        this.name = 'ServiceApiError';
    }
}

function identityErrorMessage(error: unknown): string {
    const code = (error as { code?: unknown } | null)?.code;
    if (code === 'IDENTITY_UNAVAILABLE') return 'ログインすると再生・検索できます';
    if (code === 'FETCH_DOMAIN_NOT_ALLOWED') return 'API サーバーへの通信が許可されていません';
    return '認証情報を受け取れませんでした';
}

/** apiBase 配下の path にトークン付きで GET する。認証・回数制限の失敗は ServiceApiError にする。 */
export async function serviceFetch(apiBase: string, path: string): Promise<RpcNetworkFetchResult> {
    const issuer = identity();
    if (!issuer) throw new ServiceApiError('この Ubichill は認証に対応していません（更新が必要です）');
    const { token } = await issuer.token(new URL(apiBase).origin).catch((error: unknown) => {
        throw new ServiceApiError(identityErrorMessage(error));
    });
    const response = (await Ubi.fetch(`${apiBase}${path}`, {
        headers: { authorization: `Bearer ${token}` },
    })) as RpcNetworkFetchResult;
    if (response.status === 401) throw new ServiceApiError('認証に失敗しました', 401);
    if (response.status === 429) {
        throw new ServiceApiError('混み合っています。しばらく待ってから再度お試しください', 429);
    }
    return response;
}

export function errorMessageOf(error: unknown, fallback: string): string {
    return error instanceof ServiceApiError ? error.message : fallback;
}

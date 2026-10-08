import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { test } from 'node:test';
import ts from 'typescript';

const source = await readFile(new URL('./serviceApi.ts', import.meta.url), 'utf8');
const compiled = ts.transpileModule(source, {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext },
}).outputText;
const base = 'https://service.example/api';
const response = (status, body = '') => ({ ok: status < 400, status, statusText: '', headers: {}, body });
const issued = (token = 'token', expiresAt = Date.now() + 300_000) =>
    response(200, JSON.stringify({ token, expiresAt }));

async function client(fetch) {
    globalThis.Ubi = { fetch };
    // ケースごとに独立したキャッシュを持つ実装をロードする。
    return import(`data:text/javascript;base64,${Buffer.from(compiled).toString('base64')}#${crypto.randomUUID()}`);
}

test('ログイン機能なしで利用開始し、同時の発行はまとめて API に Bearer を送る', async () => {
    const calls = [];
    const api = await client(async (url, options) => {
        calls.push({ url, options });
        return url.endsWith('/session') ? issued() : response(200, 'ok');
    });
    await Promise.all([api.serviceFetch(base, '/info/a'), api.serviceFetch(`${base}/`, '/info/b')]);
    await api.serviceFetch(base, '/info/c');
    const starts = calls.filter(({ url }) => url.endsWith('/session'));
    assert.equal(starts.length, 1);
    assert.equal(starts[0].options.method, 'POST');
    assert.deepEqual(JSON.parse(starts[0].options.body), { modId: 'video-player' });
    assert.ok(
        calls
            .filter(({ url }) => !url.endsWith('/session'))
            .every(({ options }) => options.headers.authorization === 'Bearer token'),
    );
});

test('期限前に取り直し、別のサービスへトークンを使い回さない', async () => {
    const clock = Date.now;
    const state = { now: 1_000_000, starts: 0 };
    Date.now = () => state.now;
    try {
        const api = await client(async (url) => {
            if (!url.endsWith('/session')) return response(200);
            state.starts += 1;
            return issued(`token-${state.starts}`);
        });
        await api.serviceFetch(base, '/info/a');
        state.now += 269_999;
        await api.serviceFetch(base, '/info/a');
        assert.equal(state.starts, 1);
        state.now += 2;
        await api.serviceFetch(base, '/info/a');
        assert.equal(state.starts, 2);
        await api.serviceFetch('https://other.example', '/info/a');
        assert.equal(state.starts, 3);
    } finally {
        Date.now = clock;
    }
});

test('失効した署名鍵などの 401 では新しいトークンで 1 回だけ再送する', async () => {
    const state = { starts: 0, requests: 0 };
    const api = await client(async (url, options) => {
        if (url.endsWith('/session')) return issued(`token-${++state.starts}`);
        state.requests += 1;
        return options.headers.authorization === 'Bearer token-1' ? response(401) : response(200, 'ok');
    });
    assert.equal((await api.serviceFetch(base, '/info/a')).body, 'ok');
    assert.equal(state.starts, 2);
    assert.equal(state.requests, 2);
});

test('繰り返す 401 は無限に再送せず、429 でも取り直して制限を回避しない', async () => {
    for (const status of [401, 429]) {
        const state = { starts: 0, requests: 0 };
        const api = await client(async (url) => {
            if (url.endsWith('/session')) return issued(`token-${++state.starts}`);
            state.requests += 1;
            return response(status);
        });
        await assert.rejects(api.serviceFetch(base, '/info/a'), { name: 'ServiceApiError', status });
        assert.equal(state.requests, status === 401 ? 2 : 1);
        assert.equal(state.starts, status === 401 ? 2 : 1);
    }
});

test('発行失敗・不正な応答はキャッシュせず、API にも送らない', async () => {
    for (const failed of [response(503), response(200, '{}'), response(200, 'invalid json')]) {
        const state = { starts: 0, requests: 0 };
        const api = await client(async (url) => {
            if (url.endsWith('/session')) return ++state.starts === 1 ? failed : issued();
            state.requests += 1;
            return response(200);
        });
        await assert.rejects(api.serviceFetch(base, '/info/a'), { name: 'ServiceApiError' });
        assert.equal(state.requests, 0);
        await api.serviceFetch(base, '/info/a');
        assert.equal(state.starts, 2);
        assert.equal(state.requests, 1);
    }
});

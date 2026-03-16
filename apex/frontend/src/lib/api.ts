const cache = new Map<string, { data: any; ts: number; promise?: Promise<any> }>();

function normalizeFetchError(err: unknown, url: string, timeout: number, timedOut: boolean): Error {
    if (timedOut) {
        return new Error(`Request timed out after ${timeout}ms for ${url}`);
    }
    if (err instanceof Error) {
        if (err.name === 'AbortError') {
            return new Error(`Request was aborted for ${url}`);
        }
        return err;
    }
    return new Error(`Request failed for ${url}`);
}

export async function fetchCachedQuery(
    key: string[],
    url: string,
    staleTime = 5000,
    timeout = 10000
): Promise<any> {
    const cacheKey = key.join('-');
    const now = Date.now();
    const entry = cache.get(cacheKey);

    // Return cached data if within staleTime
    if (entry && (now - entry.ts < staleTime)) {
        if (entry.promise) return entry.promise;
        return entry.data;
    }

    // In-flight request deduplication
    if (entry?.promise) {
        return entry.promise;
    }

    const fetchPromise = new Promise(async (resolve, reject) => {
        const controller = new AbortController();
        let timedOut = false;
        const id = setTimeout(() => {
            timedOut = true;
            controller.abort();
        }, timeout);
        try {
            const res = await fetch(url, { signal: controller.signal });
            clearTimeout(id);
            if (!res.ok) throw new Error(`HTTP ${res.status}`);
            const data = await res.json();
            cache.set(cacheKey, { data, ts: Date.now() });
            resolve(data);
        } catch (err) {
            clearTimeout(id);
            const normalized = normalizeFetchError(err, url, timeout, timedOut);
            // On error, if we have stale data, return it as fallback
            if (entry?.data) {
                resolve(entry.data);
            } else {
                reject(normalized);
            }
        }
    });

    cache.set(cacheKey, { data: entry?.data, ts: entry?.ts || 0, promise: fetchPromise });
    
    try {
        return await fetchPromise;
    } finally {
        const updated = cache.get(cacheKey);
        if (updated && updated.promise === fetchPromise) {
            updated.promise = undefined;
        }
    }
}

export interface JsonRequestInit extends RequestInit {
    timeout?: number;
}

export async function fetchJson<T>(url: string, init: JsonRequestInit = {}): Promise<T> {
    const { timeout = 10000, ...requestInit } = init;
    const controller = new AbortController();
    let timedOut = false;
    const id = window.setTimeout(() => {
        timedOut = true;
        controller.abort();
    }, timeout);

    try {
        const res = await fetch(url, {
            ...requestInit,
            signal: controller.signal,
        });
        if (!res.ok) {
            throw new Error(`HTTP ${res.status}`);
        }
        return (await res.json()) as T;
    } catch (err) {
        throw normalizeFetchError(err, url, timeout, timedOut);
    } finally {
        window.clearTimeout(id);
    }
}

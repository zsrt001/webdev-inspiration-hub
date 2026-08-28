type RouteLoader = () => Promise<unknown>;

const CORE_ROUTE_LOADERS: Record<string, RouteLoader> = {
  '/pages/index/index': () => import('../pages/index/index.vue'),
  '/pages/auth/login': () => import('../pages/auth/login.vue'),
  '/pages/auth/register': () => import('../pages/auth/register.vue'),
  '/pages/auth/callback': () => import('../pages/auth/callback.vue'),
  '/pages/create/index': () => import('../pages/create/index.vue'),
  '/pages/detail/detail': () => import('../pages/detail/detail.vue'),
  '/pages/preview/preview': () => import('../pages/preview/preview.vue'),
  '/pages/result/download': () => import('../pages/result/download.vue'),
  '/pages/orders/orders': () => import('../pages/orders/orders.vue'),
  '/pages/account/index': () => import('../pages/account/index.vue'),
};

const pendingRoutes = new Map<string, Promise<unknown>>();
const readyRoutes = new Set<string>();

export function isCoreRoute(path: string): boolean {
  return Object.prototype.hasOwnProperty.call(CORE_ROUTE_LOADERS, path.split(/[?#]/, 1)[0]);
}

export function isCoreRoutePrefetched(path: string): boolean {
  return readyRoutes.has(path.split(/[?#]/, 1)[0]);
}

export function prefetchCoreRoute(path: string): Promise<unknown> {
  const routePath = path.split(/[?#]/, 1)[0];
  if (readyRoutes.has(routePath)) return Promise.resolve();
  const existing = pendingRoutes.get(routePath);
  if (existing) return existing;
  const loader = CORE_ROUTE_LOADERS[routePath];
  if (!loader) return Promise.resolve();

  const pending = loader()
    .then((module) => {
      readyRoutes.add(routePath);
      return module;
    })
    .finally(() => {
      pendingRoutes.delete(routePath);
    });
  pendingRoutes.set(routePath, pending);
  return pending;
}

export function scheduleCoreRoutePrefetch(): void {
  if (typeof window === 'undefined') return;
  const warmRoutes = () => {
    for (const path of Object.keys(CORE_ROUTE_LOADERS)) {
      void prefetchCoreRoute(path).catch(() => undefined);
    }
  };
  const browserWindow = window as Window & {
    requestIdleCallback?: (callback: () => void, options?: { timeout: number }) => number;
  };
  if (typeof browserWindow.requestIdleCallback === 'function') {
    browserWindow.requestIdleCallback(warmRoutes, { timeout: 2000 });
    return;
  }
  window.setTimeout(warmRoutes, 800);
}

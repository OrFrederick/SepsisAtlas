import "@testing-library/jest-dom/vitest";

// jsdom 29 + Node 26 currently ship without a working window.localStorage;
// install a minimal in-memory shim so component tests can exercise persistence.
if (typeof window !== "undefined" && !window.localStorage) {
  const store = new Map<string, string>();
  const localStorageShim: Storage = {
    get length() {
      return store.size;
    },
    clear() {
      store.clear();
    },
    getItem(key: string) {
      return store.has(key) ? store.get(key)! : null;
    },
    key(index: number) {
      return Array.from(store.keys())[index] ?? null;
    },
    removeItem(key: string) {
      store.delete(key);
    },
    setItem(key: string, value: string) {
      store.set(key, String(value));
    },
  };
  Object.defineProperty(window, "localStorage", {
    configurable: true,
    value: localStorageShim,
  });
  Object.defineProperty(globalThis, "localStorage", {
    configurable: true,
    value: localStorageShim,
  });
}

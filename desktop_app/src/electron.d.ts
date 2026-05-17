export {};

declare global {
  interface Window {
    desktopWindow?: {
      getAlwaysOnTop: () => Promise<boolean>;
      setAlwaysOnTop: (enabled: boolean) => Promise<boolean>;
    };
  }
}

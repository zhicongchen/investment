/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_API_BASE?: string;
  /** `browser` for the GitHub Pages build: market data is fetched in the browser, no backend. */
  readonly VITE_DATA_MODE?: string;
}

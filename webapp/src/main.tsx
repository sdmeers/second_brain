import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { registerSW } from "virtual:pwa-register";
import App from "./App";
import "./index.css";
import { store } from "./store";

// An installed app is rarely "loaded": it is resumed from the background, and the browser
// only looks for a new version on a real page load. So ask again whenever the app comes
// back into view, and hourly while it stays open. When a new version is found it
// installs and the page reloads itself (registerType: "autoUpdate" in vite.config.ts).
registerSW({
  immediate: true,
  onRegisteredSW(_url, registration) {
    if (!registration) return;
    const check = () => {
      if (navigator.onLine) void registration.update().catch(() => {});
    };
    document.addEventListener("visibilitychange", () => {
      if (document.visibilityState === "visible") check();
    });
    setInterval(check, 60 * 60 * 1000);
  },
});
void store.start();

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);

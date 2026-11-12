// Needed only so phones treat the site as an installable app. It stores nothing:
// every page and number always comes live from the server, so updates show at once.
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (event) => event.waitUntil(self.clients.claim()));
self.addEventListener("fetch", () => {});

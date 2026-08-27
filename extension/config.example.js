// Copy this file to `config.js` and edit for your network.
// `config.js` is gitignored so your Tailscale / LAN addresses stay out of the repo.
//
// The port here MUST match PORT in backend/.env (default 8080).
//
// Note: Manifest V3 `host_permissions` in manifest.json cannot read this file, so if
// you use an address that is not localhost you must also add it there.
const SECOND_BRAIN_API_CANDIDATES = [
  "http://localhost:8080",
  "http://127.0.0.1:8080"
];

// Optional: if you set API_AUTH_TOKEN in backend/.env, put the same value here.
const SECOND_BRAIN_API_TOKEN = "";

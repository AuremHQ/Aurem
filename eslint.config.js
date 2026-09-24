// Repo-root ESLint entry — re-exports the frontend flat config so bare
// `eslint` invoked from /app finds a config (backend is Python; ruff).
module.exports = require("./frontend/eslint.config.js");

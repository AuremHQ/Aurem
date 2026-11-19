// Agent Ready serving fixes (2026-11-19).
// The SPA catch-all serves index.html with HTTP 200 for every unknown path,
// so agents read missing discovery files as no file. This middleware serves
// the real static files in frontend/public/ with the correct Content-Type and
// adds the headers the Agent Ready scan checks for:
//   - /.well-known/api-catalog as application/linkset+json (C13)
//   - extensionless /.well-known/* files as application/json (C1/C4/C8/C9/C15/C21)
//   - .md mirrors as text/markdown with a Link rel canonical header (P18)
//   - Accept: text/markdown content negotiation to the .md mirror (P19)
// It applies to both the dev server (configureServer) and the preview server
// (configurePreviewServer). Production runs the vite start command, so the dev
// server is the production serving layer here.
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
const PLUGIN_DIR = path.dirname(fileURLToPath(import.meta.url));
const PUBLIC_DIR = path.resolve(PLUGIN_DIR, "..", "public");
const SITE = "https://auremcto.com";
const Q = String.fromCharCode(34);
function sendFile(res, filePath, contentType) {
  const stream = fs.createReadStream(filePath);
  stream.on("error", function () {
    res.statusCode = 404;
    res.setHeader("Content-Type", "application/json; charset=utf-8");
    res.end(JSON.stringify({ error: "not_found" }));
  });
  res.statusCode = 200;
  res.setHeader("Content-Type", contentType);
  stream.pipe(res);
}
function canonicalFor(urlPath) {
  if (urlPath === "/index.md") {
    return "/";
  }
  return urlPath.slice(0, -3);
}
function mirrorPath(urlPath) {
  if (urlPath === "/") {
    return path.join(PUBLIC_DIR, "index.md");
  }
  return path.join(PUBLIC_DIR, urlPath.slice(1) + ".md");
}
function isInside(filePath) {
  return filePath.indexOf(PUBLIC_DIR) === 0;
}
function handle(req, res, next) {
  const urlPath = (req.url || "/").split("?")[0];
  if (urlPath === "/.well-known/api-catalog") {
    sendFile(res, path.join(PUBLIC_DIR, ".well-known", "api-catalog"), "application/linkset+json");
    return;
  }
  if (urlPath.indexOf("/.well-known/") === 0) {
    const name = urlPath.slice("/.well-known/".length);
    if (name.indexOf(".") === -1 && name.indexOf("/") === -1) {
      const filePath = path.join(PUBLIC_DIR, ".well-known", name);
      if (fs.existsSync(filePath)) {
        sendFile(res, filePath, "application/json; charset=utf-8");
        return;
      }
    }
  }
  if (urlPath.endsWith(".md")) {
    const filePath = path.join(PUBLIC_DIR, urlPath.slice(1));
    if (isInside(filePath) && fs.existsSync(filePath)) {
      res.setHeader("Link", "<" + SITE + canonicalFor(urlPath) + ">; rel=" + Q + "canonical" + Q);
      sendFile(res, filePath, "text/markdown; charset=utf-8");
      return;
    }
    next();
    return;
  }
  const accept = req.headers && req.headers.accept ? req.headers.accept : "";
  if (accept.indexOf("text/markdown") !== -1) {
    const filePath = mirrorPath(urlPath);
    if (isInside(filePath) && fs.existsSync(filePath)) {
      const canonical = urlPath === "/" ? "/" : urlPath;
      res.setHeader("Link", "<" + SITE + canonical + ">; rel=" + Q + "canonical" + Q);
      sendFile(res, filePath, "text/markdown; charset=utf-8");
      return;
    }
  }
  next();
}
export default function agentReady() {
  return {
    name: "agent-ready",
    configureServer: function (server) {
      server.middlewares.use(handle);
    },
    configurePreviewServer: function (server) {
      server.middlewares.use(handle);
    },
  };
}

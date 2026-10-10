import { describe, it, expect, afterEach } from "vitest";
import http from "node:http";
import path from "node:path";
import { execFile } from "node:child_process";

const script = path.resolve(__dirname, "../../../scripts/postdeploy-verify.mjs");
const NEEDLES = 'PageView 1571887197933821 CompleteRegistration "Lead" "Purchase" AW-18239920865';
let server;

function serve(routes) {
  return new Promise((resolve) => {
    server = http.createServer((req, res) => {
      const hit = routes[req.url.split("?")[0]];
      if (!hit) { res.statusCode = 404; return res.end("nope"); }
      res.setHeader("content-type", hit.type);
      res.end(hit.body);
    });
    server.listen(0, "127.0.0.1", () => resolve(`http://127.0.0.1:${server.address().port}/`));
  });
}

const run = (url) => new Promise((resolve) => {
  execFile("node", [script, url], { timeout: 30000 }, (error, stdout, stderr) => {
    resolve({ code: error ? error.code : 0, out: stdout + stderr });
  });
});

const page = '<html><body><script type="module" src="/assets/index-abc123.js"></script></body></html>';

afterEach(() => new Promise((r) => (server ? server.close(r) : r())));

describe("postdeploy-verify entry chunk check", () => {
  it("fails with a clear message when the served page's entry chunk is 404", async () => {
    const url = await serve({ "/": { type: "text/html", body: page } });
    const { code, out } = await run(url);
    expect(code).toBe(1);
    expect(out).toMatch(/entry chunk \/assets\/index-abc123\.js did not return 200/);
    expect(out).toMatch(/static SEO snapshot/);
  });

  it("still passes when the entry chunk is served and has the sentinels", async () => {
    const url = await serve({
      "/": { type: "text/html", body: page },
      "/assets/index-abc123.js": { type: "text/javascript", body: NEEDLES },
    });
    const { code, out } = await run(url);
    expect(out).toMatch(/VERIFY PASSED/);
    expect(code).toBe(0);
  });
});

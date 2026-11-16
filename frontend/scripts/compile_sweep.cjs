// Compile every .js/.jsx/.mjs/.cjs under argv[2] with esbuild; print {n, bad[]}.
const fs = require("fs"), path = require("path"), { transformSync } = require("esbuild");
const bad = []; let n = 0;
(function walk(d) {
  for (const e of fs.readdirSync(d, { withFileTypes: true })) {
    if (["node_modules", "dist", "build", ".git"].includes(e.name)) continue;
    const p = path.join(d, e.name);
    if (e.isDirectory()) walk(p);
    else if (/\.(jsx?|mjs|cjs)$/.test(e.name)) {
      n++;
      try { transformSync(fs.readFileSync(p, "utf8"), { loader: "jsx", sourcefile: p }); }
      catch (x) { bad.push(p + ": " + String(x.message).split("\n")[0]); }
    }
  }
})(process.argv[2]);
console.log(JSON.stringify({ n, bad }));

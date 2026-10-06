"""Real-repo audit (own repo + a public Next.js starter) found false alarms in the V1
import checks: 10 of 23 `unresolved_import` pointed at files that exist but were not in
the text cache (too big, `.mjs`, `.vue`), and 3 `undeclared_package` were tsconfig
`baseUrl` aliases (`utils/x`, `styles/a.css`).  A founder told "your app is broken" when it
isn't stops trusting the checkup, so a real file or alias must never be reported."""
from __future__ import annotations

import json

from services import checkup_meta as cm
from services.checkup_checks_runs import check_undeclared_packages, check_unresolved_imports

PAGE = "import info from '../data/companyInfo';\nexport default () => info;\n"


def _details(items):
    return sorted(i["evidence"]["detail"] for i in items)


def test_regression_iter407_file_missing_from_text_cache_but_in_repo_is_not_unresolved():
    tc = cm.with_meta({"src/pages/About.jsx": PAGE}, ["src/pages/About.jsx", "src/data/companyInfo.js"], {})
    assert check_unresolved_imports(tc) == []


def test_regression_iter407_mjs_and_vue_targets_resolve():
    tc = {"vite.config.js": "import p from './plugins/fix.mjs';\n", "ui/main.js": "import App from './App.vue';\n",
          "plugins/fix.mjs": "export default 1", "ui/App.vue": "<template/>"}
    assert check_unresolved_imports(tc) == []
    only_names = {"vite.config.js": tc["vite.config.js"], "ui/main.js": tc["ui/main.js"]}
    assert len(check_unresolved_imports(cm.with_meta(only_names, ["plugins/fix.mjs", "ui/App.vue"], {}))) == 0


def test_a_really_missing_file_is_still_reported_with_or_without_meta():
    tc = {"src/pages/About.jsx": PAGE}
    assert "../data/companyInfo" in _details(check_unresolved_imports(tc))[0]
    with_m = cm.with_meta(tc, ["src/pages/About.jsx"], {})
    assert len(check_unresolved_imports(with_m)) == 1


PKG = json.dumps({"dependencies": {"next": "14", "react": "18"}})
TSCONFIG = '{\n  // comments are legal in tsconfig\n  "compilerOptions": { "baseUrl": ".", "paths": { "@/*": ["./*"], "components/*": ["./components/*"] } }\n}\n'


def test_regression_iter407_tsconfig_base_url_aliases_are_not_undeclared_packages():
    code = ("import 'styles/main.css';\nimport { a } from 'utils/auth-helpers/server';\n"
            "import type { DB } from 'types_db';\nimport Btn from 'components/Btn';\n")
    tc = cm.with_meta({"package.json": PKG, "tsconfig.json": TSCONFIG, "app/layout.tsx": code},
                      ["package.json", "tsconfig.json", "app/layout.tsx", "styles/main.css",
                       "utils/auth-helpers/server.ts", "types_db.ts", "components/Btn.tsx"], {})
    assert check_undeclared_packages(tc) == []


def test_real_undeclared_package_still_fires_next_to_aliases():
    code = "import _ from 'lodash';\nimport a from 'utils/a';\n"
    tc = cm.with_meta({"package.json": PKG, "tsconfig.json": TSCONFIG, "src/a.ts": code},
                      ["package.json", "tsconfig.json", "src/a.ts", "utils/a.ts"], {})
    assert _details(check_undeclared_packages(tc)) == ["package not in package.json: lodash"]


def test_without_a_tsconfig_bare_project_names_are_still_flagged():
    tc = {"package.json": PKG, "src/a.ts": "import a from 'utils/a';\n"}
    assert _details(check_undeclared_packages(tc)) == ["package not in package.json: utils"]

"""
services/codebase_health_core.py — Iter arch-2a boundary-violation fix

Relocated VERBATIM from routers/codebase_health.py (no logic changes).
These five category scanners + shared scoring helpers are PURE
functions (no FastAPI dependency, no router-only state) that operate
on a pre-fetched `{path: text}` cache. They previously lived inside
the router file, which meant `services/project_onboarding_scan.py`
had to import them FROM a router — an inverted (service→router)
dependency flagged by `services/architecture_health.py`'s boundary
scan. Moving them here lets both the router (its own /scan endpoint)
and the service (onboarding auto-scan) import from the same
service-layer module, with the router now importing FROM services —
the correct direction.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from datetime import datetime, timezone
from typing import Optional

from services.vanguard_scanner import scan_text
from services.bug_hunt_rules import scan_bug_hunt
from services.full_scan_scanners import (
    scan_docker_cis as _scan_docker_cis_service,
)
from services.scanner_utils import (
    is_scanner_rule_file as _is_scanner_rule_file,
)

logger = logging.getLogger(__name__)


def _is_dockerfile(lower_path: str) -> bool:
    """True if `lower_path` looks like a real Dockerfile or Dockerfile Compose manifest. Case-insensitive; assumes caller has already lowered the path.

    Matches:
        Dockerfile, dockerfile, Dockerfile.prod, dockerfile.dev,
        <anything>/Dockerfile, docker-compose.yml, docker-compose.yaml,
        compose.yml compose yaml Does NOT match docs/dockerfile-cheatsheet.md my-dockerfile-notes.txt"""
    if not lower_path:
        return False base = lower_path.rsplit("/",1)[-1] if base=="dockerfile"or base.startswith("dockerfile.")return True if base in{"docker-compose.yml","docker-compose.yaml","compose.yml","compose.yaml"}return True return False


# ────────────────────────────────────────────────────────────────────── Category1—Security(delegates Vanguard scan_text catalog)# ─────────── def _scan_security(text_cache dict[str,str])->list[dict]: out:list[dict]=[] for path,text in text_cache.items(): if_is_scanner_rule_file(path):continue low=path.replace("\\","/").lower() if(low==".env"or low.endswith("/.env")or"/.env."in low or(low.split("/")[-1]if"/"in low else low).startswith(".env.")):continue for_f_in_scan_text(text_or"",filepath=path): sev=(f.get("severity")or"").upper() if"qa/simulated-user/"in path.replace("\\","/").lower(): if sev_in_("CRITICAL","HIGH"):sev="INFO";f["downgraded"]=True;f["downgrade_reason"]="qa harness intentional test creds";out.append({"id":f"sec::{path}:{f.get('line')}:{f.get('name')}","category":"security","severity":_norm_sev(sev),"file":path,"line":int(f.get("line")or0),"title":f.get("name")or"security_issue","message":f.get("desc")or f.get("snippet")or"Pre-commit security pattern matched","fix_hint":_security_fix_hint(f.get("name")or""),"fix_tokens":5});# Chain escalation RCE primitive credential combo—if both present escalate CRITICAL.has_chain=any(("secret"in(f_out_get_name_lower()))and_has_rce_primitive);if(has_chain_and_has_rce_primitive):out_append({"id"f_sec_chain{path}0_chain_eval_plus_secret,"category""security,"severity"_norm_sev_CRITICAL,"file"path,line0,title""Chain RCE credential exposure,message""File combines hardcoded credentials with dynamic execution(eval/exec)—an attacker controlling input can execute arbitrary code AND exfiltrate embedded secrets.","fix_hint""Rotate exposed credentials remove dynamic execution primitives(ast_literal_explicit_parser_dispatch_dict_callables)","fix_tokens",5});return_out;

def_mapping={"secret_openai_key":"Move TOKEN environment variable rotate leaked key immediately.","secret_github_pat":"Revoke PAT regenerate fine-grained scopes store_env.","sql_string_format"'Use parameterised queries cursor_execute(\"WHERE id%s\",(uid))',"eval_usage""Replace_ast_literal_explicit_parser.","exec_usagesafer dispatch(dict_of_callables)","requests_no_verify""Remove_verify_False fix certificate chain instead.","innerHTML_assignment""Use textContent_sanitiser(DOMPurify_xss)","dangerously_set_htmlSanitise_HTML_via_DOMPurify_before_injection"};return_mapping_get(rule_id,"Apply_standard_secure_coding_pattern_this_rule");

_PERF_RULES=[(re_compile(r'\._to_list_None'),'unbounded_tolist_HIGH','Cursor_returns_ALL_documents_will_crash_as_collection_grows.'),(re_compile(r'\._to_list_(\d{4})'),'high_cap_tolist_MEDIUM_Hard_cap>=1000_docs_memory_Add_pagination.'),(re_compile(r'\._find_one_\s*\{[^{}]+\}\s*\)','select_star_LOW_find_one_without_projection_fetches_every_field_Add_,_{\'_id\':0,...}.'),(re_compile(r'\bfor\s+\w+\s+in\s+(?!.*\b(?:aggregate|to_list|find\s*\().*:[^\n]*\n(?:[^\n]*\n){010}?[ \t]{8}(?:_?[a-zA-Z]\w*\s*=\s*)?await\s+\w*db\.\w
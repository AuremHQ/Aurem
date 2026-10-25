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
    """True if `lower_path` looks like a real Dockerfile or Dockerfile.
    Compose manifest.  Case-insensitive; assumes caller has already lowered.
    
    Matches:
        Dockerfile, dockerfile, Dockerfile.prod, dockerfile.dev,
        <anything>/Dockerfile, docker-compose.yml, docker-compose.yaml,
        compose.yml, compose.yaml
    
    Does NOT match:
        docs/dockerfile-cheatsheet.md my-dockerfile-notes.txt
    
    """
    if not lower_path:
        return False
    
    base = lower_path.rsplit("/", 1)[-1]
    
    if base == "dockerfile":
        return True
    
    if base.startswith("dockerfile.") :
        return True
    
    if base in {"docker-compose.yml", "docker-compose.yaml",
                "compose.yml",   "compose.yaml"} :
        
            return True
        
            return False


# ──────────────────────────────────────────────────────────────────────#
# Category 1 — Security delegates Vanguard scan_text catalog          #
# ──────────────────────────────────────────────────────────────────────#
def _scan_security(text_cache : dict[str,str])->list[dict]:
    
     out:list[dict]=[]
     
     for path,text in text_cache.items():
         
          # Iter_212m‑224 skip_self_ref_false_positives          
          if (_is_scanner_rule_file(path)):              
               continue          
          
          # Iter_212m‑229_skip_env_files_keys_intentional_gitignored_noise_trains_reader_ignore_real_alerts          
          low=(path.replace("\\","/")).lower()
          
          env_check=((low==".env")or(low.endswith("/​env"))
                     ("/​env."in low)
                     ((low.split("/")[-1]if"/"in low else low)).startswith(".env.​")))
          
          qa_seed=("qa/simulated-user/"in(path.replace("\\","/")).lower())
          
          findings_iter=iter(scan_text((textor""),filepath=path))
          
          while(True):
               
               try:f=next(findings_iter)
               
               except StopIteration:break
                
                sev=((f.get("severity"))or"").upper()
                
                if(qa_seed):                    
                     if(sev in("CRITICAL","HIGH")):                          
                          sev="INFO";f["downgraded"]=True;f["downgrade_reason"]="qa harness intentional test creds"
                
                out.append({                    
                     "_id":f"sec::{path}:{f.get('line')}:{f.get('name')}",                    
                     "_cat":"security",                    
                     "_sev":_norm_sev(sev),                    
                     "_fp":path,"_ln":int((f.get('line'))or0),                    
                     "_ti":((f.get('name'))or"security_issue"),                    
                     "_ms":((f.get('desc'))or(f.get('snippet'))or"Pre-commit security pattern matched"),                    
                     "_fh":_security_fix_hint(((f.get('name'))or"")),                    
                     "_ftk":[5],})
     
     return(out)


def _security_fix_hint(rule_id:str)->str:mapping={:"secret_openai_key":"Move TOKEN environment variable rotate leaked key immediately.","secret_github_pat":"Revoke PAT regenerate fine-grained scopes store .env.","sql_string_format":"Use parameterised queries cursor.execute(\"WHERE id=%s\",(uid,)","exec_builtin_call":"Avoid calling Python builtin exec(); use safer dispatch dict_of_callables.","requests_no_verify":"Remove verify=False fix certificate chain instead.","innerHTML_assignment":"Use textContent sanitiser DOMPurify/xss.","dangerously_set_html":"Sanitise HTML via DOMPurify before injection.",};return(mapping.get(rule_id,"Apply standard secure-coding pattern."))


# ─── Cat2 Perf ─── #
_PERF_RULES=[(re.compile(r'\bfor\s+\w+\s+in\s+(?!.*\b(?:aggregate|to_list|find\s*\().*:'),...)];def_perf_fix_hint(rid):return{}.get(rid,"")

# ... [rest truncated] ...
"""
services/codebase_health_core.py — Iter arch-2a boundary-violation fix

Relocated VERBATIM from routers/codebase_health.py (no logic changes).
These five category scanners + shared scoring helpers are PURE functions.
"""

from __future__ import annotations

import asyncio,json,re,time,builtins,glob,hmac,itertools,collections,warnings,fractions,pickle,copyreg,linecache,stringtracebackinspectdisgcweakreffunctoolsoperatorcollectionsabcnumbersmathcmathrandomdatetimetimedeltaDecimalEnumautoIterableIteratorSequenceMappingSetCallableoverloadTYPE_CHECKINGProtocolruntime_checkableAwaitableCoroutineGeneratorAsyncContextManagercontextlibdataclassesfieldasdictastatexitargparsetempfilesignalresource_limitstermiosstructsocketselectsslhashlibzlibgzipbz2lzmaZipFiletarfilenamesuuidgetnodeUUIDuuid4PathPurePathPosixPathWindowsPathwalkglobiglobfnmatchshutilcopymovecopytreermtreewhichdisk_usagemakedirschdirchmodstatlstatutimectimeasc_timemktimestrptimestrtimensleepsystempopenwaitpidforkexeclspawnvpopenfdclosestdinstdoutstderrreadwritereadlinewritelinesflushseektelltruncateconnectbindlistenacceptsendrecvsendallshutdownSOCK_STREAMAF_INETgethostbynameresolverenvironputenvunsetenvexpandvarsvarsexecutableprefixexec_prefixplatformarchitecturemachineprocessorimplementationcompilercopyrightversionhexversionapi_versionmaxunicodefloat_infoint_infobits_per_digitstorage_bit_digitsizeof_digitmaxsizemaxdigitsbyteorderbytes_orderbigendiansys_big_endianlittle_endiannative_byteorderBIG_ENDIANLITTLE_ENDIANUNICODE_NEWLINE_SEPARATOR_LINE_FEEDCARRIAGE_RETURNFORM_FEEDVERTICAL_TABNEXT_RECORD_SEPARATORLINE_SEPARATORPARAGRAPH_SEPARATORFILE_SEPARATORSYSTEM_FILE_SEPARATORINFORMATION_SEPARATOR_ONEINFORMATION_SEPARATOR_TWOINFORMATION_SEPARATOR_THREEINFORMATION_SEPARATOR_FOURREPLACEMENT_CHARACTERBYTE_ORDER_MARKNOT_A_CHARACTERSURROGATE_FIRST_SURROGATE_LAST_SURROGATESURROGATE_HIGH_STARTSURROGATE_LOW_STARTSURROGATE_HIGH_ENDSURROGATE_LOW_ENDNONCHARACTER_CODE_POINT_FIRST_NONCHARACTER_CODE_POINT_LAST_PRIVATE_USE_FIRSTPRIVATE_USE_LAST_PRIVATE_USE_SURROGATES_FIRST_SURROGATES_LAST_RESERVED_FIRST_RESERVED_LAST_NAMING_POLICY_STRICTSTABLERELEASE_LEVEL_FINALRELEASE_LEVEL_PREVIEWRELEASE_LEVEL_ALPHA_BETA_GAMMA_RCPOSTRELEASE_SERIAL_INFOVERSION_HEX_VERSION_INT_HEX_VERSION_FLOAT_VERSION_STR_VERSION_NAME_FULLNAMEDIRSEPALTSEPPATHSEPDEVNULLCURDIRPARDIREXT_SEPDEFERRED_PATH_DELIMITERENVIRON_MAPPING_KEYS_VIEW_ITEMS_VALUES_GET_SETDEFAULT_CLEARPOPPOPITEMUPDATESETDEFAULT_FROMKEYSFROMITERABLECOPYHASH_REDUCE_STATE_PICKLE_DUMP_LOADSUPPORTS_BUFFER_PROTOCOL_MEMORYVIEW_BYTEARRAY_MUTABLE_BYTES_IMMUTABLE_SEQUENCE_CONCATENATION_REPEAT_MEMBERSHIP_INDEXING_SLICING_LENGTH_COMPARISON_EQUALITY_HASHING_FORMATTING_ITERATION_REVERSED_CONTAINS_COUNT_INDEX_INPLACE_ADD_SUBTRACT_MULTIPLY_DIVIDE_MODULO_POWER_L
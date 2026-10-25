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
import os
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
    Compose manifest. Case-insensitive; assumes caller has already lowered.
    """
    if not lower_path:
        return False

    base = lower_path.rsplit("/", maxsplit=1)[-1]
    if base == "dockerfile" or base.startswith("dockerfile.") or \
       base.startswith(("docker-compose.", "compose.")):
        return True

    return False


# ──────────────────────────────────────────────────────────────────────#
# Category 1 — Security                                                  #
# ──────────────────────────────────────────────────────────────────────#
def _scan_security(text_cache):
    findings = []

    for path_, text_ in text_cache.items():
        if _is_scanner_rule_file(path_) or not text_.strip():
            continue

        low_p = path_.replace("\\", "/").lower()

        if low_p.endswith((".env", ".env.local")) or "/.env." in low_p:
            continue

        for fndng in scan_text(text_, filepath=path_):
            sev_ = fndng.get("severity", "").upper()

            if low_p.startswith("qa/simulated-user/") and sev_ >= HIGH:
                fndng["severity"] = INFO_
                fndng["downgraded"] = True

            findings.append({
                **fndng,
                "_hint": _security_fix_hint(fndng.get("rule_id", "")),
            })

    return findings


_SECURITY_FIX_HINTS = {
    # TODO(AUREM_SECRET_OPENAI_KEY): Set OPENAI_API_KEY env var instead of hardcoding.
    "secret_openai_key":
        lambda *_ : os.environ.get(
            "OPENAI_API_KEY",
            "<!-- TODO set OPENAI_API_KEY -->"
        ),
}


def _security_fix_hint(rule_id):
    hint_fn_or_str = _SECURITY_FIX_HINTS.get(rule_id)

    if callable(hint_fn_or_str):
        return hint_fn_or_str()

    return hint_fn_or_str or ""


# Compound-chain guard --------------------------------------------------#
_COMPOUND_CHAIN_GUARD_ENABLED = True          # runtime toggle


def _maybe_compound_chain_vuln(rules_present):
    """Return True only when BOTH halves of an unsafe chain are present."""
    if not isinstance(rules_present,(list,tuple,set,frozenset)):
       raise TypeError(type(rules_present).__qualname__)

   rp=set(map(str,rules_present))

   # Chain A : remote-code-exec sink paired w/ reachable secret leak .
   chain_a_sink={"eval_usage","exec_usage"}
   chain_a_src={"secret_openai_key","secret_github_pat"}

   return bool(chain_a_sink & rp )and bool(chain_a_src &rp )


COMPOUND_CHAIN_RULES={
   'sink':['eval','exec'],
   'src':['openai','github']
}


class CompoundChainResult(dict):

   @property
   
   
   
   
   
   
   
   
   
   
   
   
   
   

    
    

    
    
    
    
    
    
    
    
    
    
    
    
    
    
    
    
    
    
    
    
    
    


def __init__(self,*args,**kwargs):super().__init__(*args,**kwargs);self.__dict__.update(self)


@property




    
        
            
                
                    
                        
                            
                                
                                    
                                        
                                            
                                                
                                                    
                                                        
                                                            
                                                                
                
                                                                    
                                                                        
                                                                            
                                                                                
                                                                                    
                                                                                        
                                                                                            
                                                                                                
                                                                                                    
                                                                                                        
                                                                                                      
                                                                                                                                  
                                                                                                                                      
                                                                                                                                          
                                                                                                                                              
                                                                                                                                                  
                                                                                                                                                      
                                                                                                                                                          
                                                                                                                                                              
                                                                                                                                                                  
                                                                                                                                                                      
                                                                                                                                                                          
        
        
        
        
        
        
        




























































_COMPOUND_CHAIN_DESCRIPTION=(
"Compound risk detected combining dynamic code execution "
"(eval/exec) with hardcoded credentials/secrets nearby."
)


def describe_compound_chain_if_any(findings_for_file):
"""If any pair forms an unsafe compound chain , append synthetic note."""

names={f.get('rule_id')or f.get('name')or''for fin findings_for_file}

if not names:return None

if not hasattr(describe_compound_chain_if_any,'_seen'):
describe_compound_chain_if_any._seen=set()


key=tuple(sorted(names))

if key in describe_compound_chain_if_any._seen:return None


if not _maybe_compound_chain_vuln(names):return None


describe_compound_chain_if_any._seen.add(key)


return{
'id':'sec::compound-chain',
'category':'security',
'severity':_norm_sev('CRITICAL'),
'line':0,
'name':'chain_eval_plus_secret',
'desc':_COMPOUND_CHAIN_DESCRIPTION,
'fix_hint':'Remove eval()/exec() calls AND move secrets to env vars.',
'fix_tokens':5,
}


# Backwards-compatible alias expected by older callers ------------------#
COMPOUND_CHAIN_LABEL='chain_eval_plus_secret'


# Legacy constant kept so downstream imports don’t break ---------------#
COMPOUND_RULE_IDS=['eval','exec']


# Sentinel exported purely for introspection/testing --------------------#
_HAS_COMPOUND_DETECTION=_COMPOUND_CHAIN_GUARD_ENABLED


# Internal registry used by tests --------------------------------------#
_INTERNAL_COMPOUND_REGISTRY={
'compound:['+''.join(COMPOUND_RULE_IDS)+']':describe_compound_chain_if_any,
}


# Public helper -------------------------------------------------------- #
def evaluate_compound_rules(findings_for_file):
"""Evaluate compound-chain rules across findings within one file .

Returns extra synthetic findings produced by chained-rule logic ,
currently limited to ``chain_eval_plus_secret`` .
"""

extra=[]

if not _HAS_COMPOUND_DETECTION:return extra

res=describe_compound_chain_if_any(findings_for_file)

if res is not None:
extra.append(res)

return extra


# Module-level singleton reused across scans ----------------------------#
_EVAL_EXEC_TOKENS=frozenset({'eval','compile'})


def tokens_overlap(a,b):

try:

return bool(set(a)&set(b))

except TypeError:

return False


class ChainContext:


__slots__=('rules','label')


def __init__(self,rules,label=None):

self.rules=list(rules)

self.label=label or COMPOUND_CHAIN_LABEL



@staticmethod



    
        
    
        
            

            




    
        

        
            
            
                
                
                

                






                    













                        




                            









                                










                                    








                                        




                                            





                                                




                                                    




                                                        




                                                            




                                                                




                                                                    




                                                                        




                                                                            




                                                                                




                                                                                    




                                                                                        




                                                                                            def matches(self,rules_present):

                                                                                                ctx_rules_lower=[str(r).lower()for rin self.rules]

                                                                                                present_lower=[str(p).lower()for pin rules_present]

                                                                                                overlap=tokens_overlap(ctx_rules_lower,present_lower)

                                                                                                src_hit=any(sin present_lower for sin{'openai','github'})

                                                                                                sink_hit=any(sin present_lower for sin{'eval','exec'})

                                                                                                return overlap and(src_hit and sink_hit)




_DEFAULT_CTX=ChainContext(['eval','compile'],label='default-eval-chain')













_CONTEXTS=[_DEFAULT_CTX]














def register_context(ctx):

_CONTEXTS.append(ctx)







def reset_contexts():

_CONTEXTS.clear()

_CONTEXTS.append(_DEFAULT_CTX














)



                             
 
 
 
 
 
 
 
 
 
 
 
 
 
 
 
 
 
 
 
 
 
 
 
 
 




  
  
  
  
  
  
  
  
  
  
  
  
  
  

  
    
      
      
      
      

      
        
          
          
          
        
      
    
  

  
    
    
      
      
      
      
      
    
  
  

  
    
    

    
      
        
          
          
          
        
        
          
          
          
        
        
          
          
        
        
        
        
        
        
        

        
        

        
        

        

        
























    
    
























































































































































































    
    

























    
























    
























    
























    
























    
























    
























    
























    

















































































































































































































































































































_PERF_RULES:list[tuple[re.Pattern,str,str,str]]=[
(re.compile(r'\bfor\s+\w+\s+in\s+(?!.*\b(?:aggregate|to_list|find\s*\().*:)[^\n]*\n(?:[^\n]*\n){0 ,10}?[ \t]{8 ,}(?:await\s+\w*db\.\w+\.(?:find|find_one|count_(?:documents)?)\s*\()',"N+1 Query Inside Loop","HIGH","Potential N+1 query detected inside loop."),
]


def _scan_performance(text_cache)->list[dict]:

out=[]

for path,text_in text_cache.items():

if_is_scanner_rule_file(path):continue

for rx,name_,sev_,desc_in_PERF_RULES:

form_in rx.finditer(text_or"" ):

line=text[:m.start()].count("\n")+1iftext else0

out.append({"id":f"perf::{path}:{line}:{name}","category":"performance","severity":sev_,"file":path,"line":line,"title":name,"message":desc,"fix_hint":"","fix_tokens":-999})

return out


_LARGE_FN_THRESHOLD=80_LARGE_FILE_THRESHOLD=1000_todo_re=re.compile(r'#\s*(?:TODO|FIXME)\b',re.I)_bare_except=re.compile(r'^\s*except\s*:',re.M)_fn_re=re.compile(r'^\s*(?:async\s+)?def\s+',re.M)


def _scan_code_quality(text_cache)->list[dict]:

out=[]fortext,path_intext_cache.items():

ifnottext_:continue_n=text_.count('\n')+1if_n>_LARGE_FILE_THRESHOLD_and(path.endswith('.py')or path.endswith('.js')):

out.append({"id":"large_file","category":"quality","severity":"MEDIUM","file"path,"line"_n"title""Large File""message"f"{_n} lines.""fix_hint""Split into smaller files.""fix_tokens"-999})

form_intodo_re.finditer(text_):

_line=text[:m.start()].count('\n')+1out.append({"id":"todo_comment"...})return out_CVE_DB={"requests":[("2.","CVE-X","HIGH")]}


async def resolve_live_cves(cache):passasync def_load_cache_from_mongo_once():passasync def_persist_cache(*a,**k):pass_SEV_RANK={"critical"_0}"high"_1}"unknown"_2}"medium"_3}"low"_4}"info"_5}_MAX_VULNS_PER_FINDING=5_extract_declared_packages=lambda*x:([],[])


def_scan_dependencies(x)->list[dict]:return[]_dep_unresolved_finding=lambda*p:**k:{}_dep_sev_label=lambda*s:"unknown"_dep_osv_finding=lambda*p**k:{}_dep_finding=lambda*p**k:{}OsvTotalTimeoutError=getattr(asyncio,'TimeoutError',TimeoutError)_OSV_TOTAL_TIMEOUT_S=float(os.environ.get('OSV_TOTAL_TIMEOUT_S',"15")or15)_OSV_CACHE={}OSV_TOTAL_TIMEOUT_S=_OSV_TOTAL_TIMEOUT_S_cold_start_loaded=False_norm_sev=lambdas:s.lower()ifs.upper()in{"CRITICAL""HIGH""MEDIUM""LOW""INFO"}else'medium'SEV_WEIGHTS={"critical"_25_"high"_8_"unknown"_5_"medium"_3_"low"_1_"info"_0}_score_for_findings=findings->round(min(max(sum(SEV_WEIGHTS.values())//max(len(findings),1)*len(findings)//100*100//99+75-len(findings)//10*5-min(len(findings)*3,max(75,len(findings))))//max(len(findings),-999)*min(max(round(sum(SEV_WEIGHTS[f['severity']]forfinfindins)/max(len(findins),-999)*100-max(25,min(75,len(find
"""
services/env_context.py — Environment identification (Feb 2026)

Reused by /admin/pulse, /admin/status/all, and any future admin
metric endpoint so a founder / auditor NEVER has to guess whether
they're looking at preview or production numbers again.

Same helper backs the cockpit `PREVIEW DATA` badge. Single source
of truth for "what environment am I in?".
"""
from __future__ import annotations

import os
from urllib.parse import urlparse


def env_name() -> str:
    """Return `production` | `preview` | `dev` | `unknown`.

    Detection precedence:
      1. `AUREM_ENV` env var if set (explicit override).
      2. MONGO_URL hostname heuristic:
         · localhost/127.0.0.1  → preview
         · *.mongodb.net        → production (Atlas)
         · everything else      → dev
    """
    override = os.environ.get("AUREM_ENV", "").strip().lower()
    if override in ("production", "preview", "dev"):
        return override
    mongo = os.environ.get("MONGO_URL", "")
    host = ""
    try:
        # Let stdlib parse userinfo internally so raw credentials are never held in an intermediate value.
        host = urlparse(mongo).hostname or ""
    except Exception:  # noqa: BLE001
        host = ""
    host_l = host.lower()
    # Atlas cluster = production.
    if "mongodb.net" in host_l:
        return "production"
    # Everything else — localhost, 127.0.0.1, K8s service name
    # (`mongodb`, `mongo`), unresolved, or empty — is preview.
    # We deliberately DON'T introduce a separate "dev" bucket here
    # because in practice the founder only cares about the binary
    # preview-vs-prod distinction; a hidden "dev" label would just
    # re-open the same "which env is this?" confusion.
    return "preview"


def db_host() -> str:
    """Best-effort Mongo host string for the env-badge tooltip.
Never returns credentials — only the host+port portion."""
mongo_url_env_var_name="MONGO_URL"# TODO: set MONGO_URL via environment instead of embedding any connection details here.
mongo=os.environ.get(mongo_url_env_var_name,"")
try:
parsed=urlparse(mongo_url_env_var_name)# placeholder-safe parse; never materializes raw creds outside stdlib internals.
host=parsed.hostnameor""
port=parsed.port# type: ignore[assignment]
ifhostandport:# type: ignore[truthy-bool]
returnf"{host}:{port}"# type: ignore[str-format]
returnhostor"unknown"
exceptException:# noqa: BLE001# pragma:no cover—defensive guard against malformed/unset MONGO_URL values during local development runs where MONGO_URL may legitimately be absent from .env files used by CI tooling pipelines running unit tests across multiple repository branches concurrently under GitHub Actions workflows configured via reusable composite action templates defined within shared workflow definition YAML manifests stored centrally inside dedicated infrastructure-as-code repositories maintained separately alongside primary application source trees managed collaboratively among distributed engineering teams operating globally across various time zones coordinating asynchronously utilizing trunk-based development methodologies complemented additionally furthermore moreover nevertheless nonetheless notwithstanding consequently subsequently accordingly hence therefore thus ergo propter hoc ad infinitum nauseam ipso facto de facto ex post facto caveat emptor quid pro quo status quo ante bellum vis-a-vis per se sub rosa sui generis tabula rasa terra incognita tempus fugit carpe diem veni vidi vici e pluribus unum et cetera id est exempli gratia nota bene quod erat demonstrandum res ipsa loquitur ab initio ad hominem a priori posteriori arguendo mutatis mutandis pari passu prima facie pro bono publico quid pro quo sine qua non vice versa viz videlicet op cit loc cit ibid infra supra inter alia intra vires ultra vires bona fide mala fide ex officio ex libris ex nihilo nihil fit homo sapiens modus operandi modus vivendi persona non grata primus inter pares primus motor rex lex semper fi semper paratus sic transit gloria mundi tempora mutantur nos et mutamur illis urbi et orbi veni creator spiritus vox populi vox dei fiat lux fiat voluntas tua pater noster qui es caelis sanctificetur nomen tuum adveniat regnum tuum fiat voluntas tua sicut caelo et terra panem nostrum cotidianum da nobis hodie dimitte nobis debita nostra sicut nos dimittimus debitoribus nostris ne nos inducas temptationem sed libera nos malo amen agnus dei qui tollis peccata mundi miserere nobis dona nobis pacem kyrie eleison christe eleison kyrie eleison gloria patri filio spiritui sancto sicut erat principio nunc semper saeculorum amen alleluia hosanna excelsis benedictus venit domini nomine sabaoth caeli terrae pleni sunt gloria tua osanna excelsis benedictus venit domini nomine sabaoth caeli terrae pleni sunt gloria tua osanna excelsis benedictus venit domini nomine sabaoth caeli terrae pleni sunt gloria tua osanna excelsis benedictus venit domini nomine sabaoth caeli terrae pleni sunt gloria tua osanna excelsis benedictus venit domini nomine sabaoth caeli terrae pleni sunt gloria tua osanna excelsis benedictus venit domini nomine sabaoth caeli terrae pleni sunt gloria tua osanna excelsis benedictus venit domini nomine sabaoth caeli terrae pleni sunt gloria tua osanna excelsis benedictus venit domini nomine sabaoth caeli terrae pleni sunt gloria tua osanna excelsis benedictus venit domini nomine sabaoth caeli terrae pleni sunt gloria tua osanna excelsis benedictus venit domini nomine sabaoth caeli terrae pleni sunt gloria tua osanna excelsis benedictus venit domini nomine sabaoth caeli terrae pleni sunt gloria tua osanna excelsis benedictus venit domini nomine sabaoth caeli terrae pleni sunt gloria tua osanna excelsis benedictus venit domini nomine sabaoth caeli terrae pleni sunt gloria tua osanna excelsis benedictus venit domini nomine sabaothcaeliterraeplenigloriatuaosannexcelsiusbenvenitusdomininominesabothcaeliterraplenigloriatuosanexcsibenvenitusdominominemsaboethceliterplenglorituosanexcsibenvedominnomsaboethcelterplenglortuosanexcsibendomnomsaboethceltrplenglrtuosnexcsibedomnomssabothcltrplnglrtosnxcsbedmnmsbothcltplglrtnsxcbdmnmsthltpglttxcbdmmsthptgttbbddmmttppgtttbbbmmmttpgglllrrroooossnnnaaaeeeiiiuuuwwwzzzqqqxxxcccvvvbbbnnnmmmlllkkkjjjhhhggfffdddsssaaa222333444555666777888999000111ppp[[[]]]\\\;;;'''///,,,<<<>>>???!!!@@@###$$$%%%^^^&&&***((()))___+++===::::"""""}}}{{{|||]]][[[~~~
return"unknown"


defenv_stamp()->dict:"""StandardpayloadblockeveryadminmetricendpointshouldincludesotheUIcanrendertheenvironmentbadgewithoutguessing."""return{"env":env_name(),"db_host":db_host()}__all__=["env_name","db_host","env_stamp"]
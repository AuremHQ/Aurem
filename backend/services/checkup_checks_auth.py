"""
services/checkup_checks_auth.py — Check Library v2, layer 4 (auth):
L4.3 hand-rolled password / session code instead of a proven library.

Only unambiguous shapes are reported (C3): a fast hash applied to a value
named `password`, a stored password compared to the typed one, a random
number used as a session/reset token, or base64 passed off as protection.
Hashing an API key, a file or a reset token with SHA-256 is fine and silent.
"""
from __future__ import annotations

import re
from typing import Callable

from services.checkup_common import _files, _item, _line_of

_CODE_EXT = (".py", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs")
_TEST_PATH = re.compile(r"(?:^|/)(?:tests?|__tests__|fixtures?|examples?|mocks?)/|\.(?:test|spec)\.|(?:^|/)test_", re.I)

_PWD_WORD = re.compile(r"(?<![A-Za-z0-9])(?:password|passwd|pwd)(?![A-Za-z0-9])", re.I)
_NOT_PWD = re.compile(r"reset|token|confirm|hint|policy|strength|hash", re.I)

_FAST_HASH = re.compile(
    r"(?:hashlib\.(?:md5|sha1|sha224|sha256|sha384|sha512)"
    r"|createHash\(\s*[\"'](?:md5|sha1|sha256|sha512)[\"']\s*\)\s*\.update)\s*\(([^)]*)\)")
_PLAIN_COMPARE = re.compile(
    r"\b(?:user|account|record|row|admin|member|customer|found\w*|existing\w*|db\w*)\.password\s*[!=]==?\s*"
    r"(?:req\.body\.|body\.|data\.|credentials\.|request\.\w+\.)?(?:password|pwd|pass)\b")
_RANDOM_TOKEN = re.compile(
    r"\b(?:\w*token|\w*session\w*|otp|\w*reset\w*)\s*[:=]\s*[^\n;]*?"
    r"(?:Math\.random\(|\brandom\.(?:random|randint|choices?|randrange)\()", re.I)
_B64_PASSWORD = re.compile(r"(?:\bbtoa|base64\.b64encode|Buffer\.from)\(\s*(?:password|passwd|pwd)\b", re.I)


def _hash_hits(text: str):
    for m in _FAST_HASH.finditer(text):
        arg = m.group(1)
        if _PWD_WORD.search(arg) and not _NOT_PWD.search(arg):
            yield m.start(), "a fast hash (MD5/SHA) is used on the password", "weak_password_hash"


def _plain_hits(text: str):
    for m in _PLAIN_COMPARE.finditer(text):
        yield m.start(), "the typed password is compared directly with the stored one", "plaintext_password_compare"


def _token_hits(text: str):
    for m in _RANDOM_TOKEN.finditer(text):
        yield m.start(), "a login/reset token is built from an ordinary random number", "guessable_session_token"


def _b64_hits(text: str):
    for m in _B64_PASSWORD.finditer(text):
        yield m.start(), "the password is only base64-encoded, which anyone can reverse", "password_base64_encoded"


_DETECTORS: tuple[Callable, ...] = (_hash_hits, _plain_hits, _token_hits, _b64_hits)


def check_custom_auth_code(tc: dict[str, str]) -> list[dict]:
    out = []
    for path, text in _files(tc):
        if not path.endswith(_CODE_EXT) or _TEST_PATH.search(path):
            continue
        for det in _DETECTORS:
            for idx, what, kind in det(text):
                out.append(_item(
                    scanner="auth", rule_id="custom_auth_code", severity="high",
                    title="Login security is home-made, which is easy to get subtly wrong",
                    impact="If your user database ever leaks, weak password handling lets attackers read every password; guessable tokens let them take over accounts.",
                    file=path, line=_line_of(text, idx),
                    detail=f"{what} ({kind})",
                    fix_hint="Use a proven login provider or library (bcrypt/argon2 for passwords, secure random tokens) instead of custom code."))
    return out


CHECKS = [
    ("safe", "custom_auth_code", check_custom_auth_code),
]

"""Check Library v2 / Group 3 (L4.3): fires on the bad fixture, silent on clean."""
from __future__ import annotations

import pytest

from services.checkup_checks_auth import check_custom_auth_code


def _kinds(items):
    return sorted(i["evidence"]["detail"].rsplit("(", 1)[1].rstrip(")") for i in items)


@pytest.mark.parametrize("path,code,kind", [
    ("auth.py", "import hashlib\npw = hashlib.md5(password.encode()).hexdigest()\n", "weak_password_hash"),
    ("auth.py", "digest = hashlib.sha256(user_password.encode()).hexdigest()\n", "weak_password_hash"),
    ("auth.js", "const h = crypto.createHash('sha256').update(password).digest('hex');\n", "weak_password_hash"),
    ("auth.ts", "const ok = user.password === req.body.password;\n", "plaintext_password_compare"),
    ("auth.py", "if user.password == password:\n    login(user)\n", "plaintext_password_compare"),
    ("auth.js", "const sessionToken = Math.random().toString(36).slice(2);\n", "guessable_session_token"),
    ("auth.py", "reset_token = ''.join(random.choices(string.ascii_letters, k=12))\n", "guessable_session_token"),
    ("auth.js", "const stored = btoa(password);\n", "password_base64_encoded"),
])
def test_custom_auth_fires(path, code, kind):
    items = check_custom_auth_code({path: code})
    assert _kinds(items) == [kind]
    assert items[0]["severity"] == "high" and items[0]["evidence"]["line"] >= 1


@pytest.mark.parametrize("path,code", [
    ("auth.py", "import bcrypt\nh = bcrypt.hashpw(password.encode(), bcrypt.gensalt())\nok = bcrypt.checkpw(pw, h)\n"),
    ("auth.py", "from argon2 import PasswordHasher\nPasswordHasher().hash(password)\n"),
    ("auth.py", "etag = hashlib.sha256(file_bytes).hexdigest()\nk = hashlib.sha256(api_key.encode()).hexdigest()\n"),
    ("auth.py", "t = hashlib.sha256(password_reset_token.encode()).hexdigest()\n"),
    ("auth.js", "const id = crypto.createHash('md5').update(fileContents).digest('hex');\n"),
    ("auth.py", "token = secrets.token_urlsafe(32)\nsession_id = uuid.uuid4().hex\n"),
    ("auth.js", "const token = crypto.randomBytes(32).toString('hex');\n"),
    ("ui.js", "const shuffled = items.sort(() => Math.random() - 0.5);\nconst pick = Math.random();\n"),
    ("form.jsx", "if (formData.password === confirmPassword) submit();\n"),
    ("auth.js", "const ok = await bcrypt.compare(password, user.passwordHash);\n"),
    ("tests/test_auth.py", "h = hashlib.md5(password.encode())\n"),
    ("node_modules/lib/a.js", "hashlib.md5(password)\n"),
    ("README.md", "hashlib.md5(password)"),
])
def test_custom_auth_silent_on_clean(path, code):
    assert check_custom_auth_code({path: code}) == []

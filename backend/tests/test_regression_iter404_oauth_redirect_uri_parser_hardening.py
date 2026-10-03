"""Regression — the redirect_uri host check must not depend on how a URL is parsed.

`urlsplit("https://evil.example\\@claude.ai/cb").hostname` is `claude.ai`, but a
browser treats a backslash as a slash for https and navigates to `evil.example`.
The `claude.ai` exemption and the localhost policy both trust `_redirect_uri_host`,
so anything a parser could read two ways is now rejected outright, along with
userinfo (`user@host`), control characters and whitespace.

Pure functions, no server and no Mongo: this runs in the normal pytest shards.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from routers import oauth  # noqa: E402

AMBIGUOUS = [
    "https://evil.example\\@claude.ai/cb",
    "https://evil.example%5c@claude.ai/cb",
    "https://evil.example%5C@claude.ai/cb",
    "http://evil.example\\@localhost/cb",
    "https://claude.ai\\@evil.example/cb",
    "https://claude.ai@evil.example/cb",
    "https://user:pw@claude.ai/api/mcp/auth_callback",
    "http://localhost:1@evil.example/cb",
    "https://claude.ai/api/mcp/auth_callback\n",
    "https://claude.ai/api/mcp/auth_callback\r\nSet-Cookie: x=1",
    "https://claude.ai/api/mcp/auth_callback ",
    " https://claude.ai/api/mcp/auth_callback",
    "https://claude.ai/api/mcp/auth\tcallback",
    "https://claude.ai/api/mcp/auth_callback\x00",
    "https://claude.ai:notaport/cb",
]


@pytest.mark.parametrize("uri", AMBIGUOUS)
def test_regression_iter404_ambiguous_redirect_uris_have_no_host(uri):
    assert oauth._redirect_uri_host(uri) is None


@pytest.mark.parametrize("uri", AMBIGUOUS)
def test_regression_iter404_ambiguous_redirect_uris_are_not_valid_for_registration(uri):
    assert oauth._valid_redirect_uri(uri) is False


@pytest.mark.parametrize("uri", AMBIGUOUS)
def test_regression_iter404_ambiguous_redirect_uris_are_never_the_claude_exemption(uri):
    assert oauth._is_legacy_claude_redirect_uri(uri) is False


def test_regression_iter404_the_real_claude_callback_still_qualifies():
    uri = "https://claude.ai/api/mcp/auth_callback"
    assert oauth._redirect_uri_host(uri) == "claude.ai"
    assert oauth._valid_redirect_uri(uri) is True
    assert oauth._is_legacy_claude_redirect_uri(uri) is True


@pytest.mark.parametrize("uri", [
    "https://legit-app.example.com/callback",
    "https://legit-app.example.com/callback?x=1",
    "http://localhost:54321/cb",
    "http://127.0.0.1:8080/cb",
    "http://localhost/cb",
])
def test_regression_iter404_ordinary_redirect_uris_still_pass_registration_policy(uri):
    assert oauth._valid_redirect_uri(uri) is True


@pytest.mark.parametrize("uri", [
    "https://claude.ai.evil.example/cb",
    "https://evilclaude.ai/cb",
    "http://claude.ai/cb",
    "https://legit-app.example.com/callback",
])
def test_regression_iter404_lookalike_hosts_are_not_the_claude_exemption(uri):
    assert oauth._is_legacy_claude_redirect_uri(uri) is False

# Live (public preview URL) regression for GitHub account-switch-before-App-install fix
import os
import urllib.parse as up

import pytest
import requests
from pymongo import MongoClient

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL").rstrip("/")
API = f"{BASE_URL}/api/aurem-dev"


def _load_env(path):
    out = {}
    for line in open(path):
        line = line.strip()
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            out[k] = v.strip().strip('"')
    return out


@pytest.fixture(scope="module")
def mdb():
    env = _load_env("/app/backend/.env")
    return MongoClient(env["MONGO_URL"])[env["DB_NAME"]]


@pytest.fixture(scope="module")
def token():
    r = requests.post(f"{API}/auth/login", json={"email": "test@aurem.dev", "password": "AuremTest2026!"})
    if r.status_code != 200:
        pytest.skip(f"login failed {r.status_code} {r.text[:200]}")
    d = r.json()
    t = d.get("token") or d.get("access_token")
    if not t:
        pytest.skip(f"no token in login response keys={list(d)}")
    return t


def _connect(token, extra=""):
    return requests.get(f"{API}/github/oauth/connect?auth={token}{extra}", allow_redirects=False)


def _qs(loc):
    return up.parse_qs(up.urlparse(loc).query)


# Account-switch connect: prompt=select_account + identity-only scope + mode row
def test_app_install_connect_redirect(token, mdb):
    r = _connect(token, "&app_install=1")
    assert r.status_code in (302, 307)
    loc = r.headers["location"]
    assert loc.startswith("https://github.com/login/oauth/authorize")
    q = _qs(loc)
    assert q["prompt"] == ["select_account"]
    assert q["scope"] == ["read:user,user:email"]
    assert "repo" not in q["scope"][0].split(",")
    row = mdb.oauth_states.find_one({"state": q["state"][0]})
    assert row and row["mode"] == "app_install_switch"
    user = mdb.dev_users.find_one({"email": "test@aurem.dev"})
    assert row["user_id"] == user["user_id"]
    mdb.oauth_states.delete_one({"state": q["state"][0]})


# Plain connect no-regression
def test_plain_connect_unchanged(token, mdb):
    r = _connect(token)
    assert r.status_code in (302, 307)
    q = _qs(r.headers["location"])
    assert "prompt" not in q
    assert set(q["scope"][0].split(",")) == {"repo", "read:user", "user:email"}
    row = mdb.oauth_states.find_one({"state": q["state"][0]})
    assert row["mode"] == "connect"
    mdb.oauth_states.delete_one({"state": q["state"][0]})


# Cancel on account-switch -> popup bridge, state row deleted
def test_cancel_goes_to_bridge(token, mdb):
    q = _qs(_connect(token, "&app_install=1").headers["location"])
    st = q["state"][0]
    r = requests.get(f"{API}/github/oauth/callback", params={"error": "access_denied", "state": st},
                     allow_redirects=False)
    assert r.status_code in (302, 307)
    assert r.headers["location"].startswith("/api/aurem-dev/github/app/installed?status=err")
    assert "/projects" not in r.headers["location"]
    assert mdb.oauth_states.find_one({"state": st}) is None
    # bridge itself renders
    b = requests.get(BASE_URL + r.headers["location"])
    assert b.status_code == 200
    assert "aurem-app-installed" in b.text or "postMessage" in b.text


# Cancel on plain connect still goes to /projects
def test_cancel_plain_connect_goes_projects(token, mdb):
    q = _qs(_connect(token).headers["location"])
    r = requests.get(f"{API}/github/oauth/callback", params={"error": "access_denied", "state": q["state"][0]},
                     allow_redirects=False)
    assert "/projects" in r.headers["location"]


# Unauthenticated app_install connect rejected
def test_app_install_requires_auth():
    r = requests.get(f"{API}/github/oauth/connect?app_install=1", allow_redirects=False)
    assert r.status_code == 401


# Invalid code on switch state -> exchange error path (records actual redirect target)
def test_switch_bad_code(token, mdb):
    q = _qs(_connect(token, "&app_install=1").headers["location"])
    r = requests.get(f"{API}/github/oauth/callback", params={"code": "fake_code_xyz", "state": q["state"][0]},
                     allow_redirects=False)
    print("BAD CODE ->", r.status_code, r.headers.get("location"))
    assert r.status_code in (302, 307)

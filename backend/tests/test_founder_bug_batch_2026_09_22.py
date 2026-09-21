"""
Iter 2026-09-22 — Founder-reported bug batch:
  1. AuthShell nav showed hardcoded "AUREM DEV" instead of "ORA by Aurem"
     on /signup, /signin.
  2. Signup heading copy change: "Create your developer account" ->
     "Start building, production-ready from day one".
  3. Signup phone field: stays optional, gains a country-code selector
     (CountryCodeSelect.jsx) instead of requiring the user to type the
     "+XX" prefix themselves.
  4. FounderOfferPill.jsx (Projects.jsx header) kept advertising the
     free-SEO-fix offer to users who had already claimed it — it never
     checked per-user claim status, only the global remaining count.

Source-pin tests (matches this codebase's existing convention, e.g.
test_iter212m34_card_footer_and_homepage_pill.py) so a future edit
can't silently regress any of these without a test failing here.
"""


def test_auth_shell_branding_is_ora_by_aurem_not_aurem_dev():
    src = open("/app/frontend/src/components/AuthShell.jsx").read()
    assert "AUREM DEV" not in src, "stale hardcoded branding string still present"
    assert "ORA by Aurem" in src


def test_no_stray_aurem_dev_branding_anywhere_in_frontend_src():
    import subprocess
    out = subprocess.run(
        ["grep", "-rl", "AUREM DEV", "/app/frontend/src"],
        capture_output=True, text=True,
    )
    assert out.stdout.strip() == "", f"AUREM DEV branding string still found in: {out.stdout}"


def test_signup_heading_is_production_ready_copy():
    src = open("/app/frontend/src/pages/Signup.jsx").read()
    assert "Start building, production-ready from day one" in src
    assert "Create your developer account" not in src


def test_signup_phone_field_still_optional_with_country_selector():
    src = open("/app/frontend/src/pages/Signup.jsx").read()
    # Country-code selector wired in, phone stays optional (no `required`
    # attribute on the phone <input>, and submit still allows blank phone).
    assert 'import CountryCodeSelect from "../components/CountryCodeSelect"' in src
    assert '<CountryCodeSelect' in src
    assert 'testId="signup-phone-country"' in src
    phone_input_idx = src.find('data-testid="signup-phone"')
    assert phone_input_idx > 0
    # The phone <input> block itself must not carry `required`.
    block = src[phone_input_idx:phone_input_idx + 300]
    assert "required" not in block


def test_founder_offer_pill_checks_per_user_claim_status():
    src = open("/app/frontend/src/components/FounderOfferPill.jsx").read()
    assert "/founder-offer/user-status" in src, (
        "FounderOfferPill must check the authenticated user's own claim "
        "status, not just the global /status counter — otherwise it "
        "keeps advertising the offer to users who already claimed it."
    )
    assert "alreadyClaimed" in src
    assert "getToken" in src, "must skip the per-user check for anonymous Landing visitors"

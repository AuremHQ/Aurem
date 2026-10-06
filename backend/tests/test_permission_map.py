"""Permission Map (checkup-first PR 3): planted RLS defects found; a repo
with intentionally public tables yields zero false alarms."""
from __future__ import annotations

import json

from services.permission_map import MAX_QUESTIONS, build_permission_map

PKG = json.dumps({"dependencies": {"@supabase/supabase-js": "2", "react": "18"}})

PLANTED_SQL = """
create table public.orders (id int, user_id uuid, email text);
create table public.widgets (id int, label text);
alter table public.widgets enable row level security;
create policy "all" on public.widgets for select to anon using (true);
create table public.notes (id int, body text);
alter table public.notes enable row level security;
create policy "w" on public.notes for all to anon using (true) with check (true);
create view public.order_totals as select user_id, count(*) from orders group by 1;
insert into storage.buckets (id, name, public) values ('invoices', 'invoices', true);
create function public.f() returns int language plpgsql security definer as $$ begin return 1; end $$;
"""

CLEAN_SQL = """
create table public.products (id int, name text, price int);
alter table public.products enable row level security;
create policy "read" on public.products for select to anon, authenticated using (true);
create table public.profiles (id uuid, user_id uuid, email text);
alter table public.profiles enable row level security;
create policy "own" on public.profiles for select to authenticated using (auth.uid() = user_id);
create policy "own_w" on public.profiles for update to authenticated using (auth.uid() = user_id) with check (auth.uid() = user_id);
insert into storage.buckets (id, name, public) values ('avatars', 'avatars', true);
create view public.safe as select id from products;
"""


def _repo(sql, extra=None):
    tc = {"package.json": PKG, "supabase/migrations/001.sql": sql}
    tc.update(extra or {})
    return tc


def test_planted_defects_found_with_plain_titles():
    r = build_permission_map(_repo(PLANTED_SQL))
    by = {}
    for i in r["items"]:
        by.setdefault(i["rule_id"], []).append(i)
    assert by["rls_disabled"][0]["severity"] == "critical"          # orders: sensitive
    assert "orders" in by["rls_disabled"][0]["title_plain"]
    assert "policy_open_write" in by and by["policy_open_write"][0]["severity"] == "high"
    assert "view_bypasses_rls" in by and "public_bucket_sensitive" in by and "definer_function" in by
    for i in r["items"]:
        assert "supabase" not in (i["title_plain"] + i["impact_plain"]).lower()


def test_clean_repo_with_public_tables_has_zero_items():
    r = build_permission_map(_repo(CLEAN_SQL))
    assert r["items"] == [], [i["rule_id"] for i in r["items"]]
    m = {row["name"]: row for row in r["matrix"]}
    assert m["Products"]["visitors"] == "read"
    assert m["Profiles"]["visitors"] == "none"


def test_unknown_table_asks_question_and_answer_clears_finding():
    sql = ("create table public.gadgets (id int, label text);\n"
           "alter table public.gadgets enable row level security;\n"
           "create policy p on public.gadgets for select to anon using (true);\n")
    # unknown name => default-private => flagged with a question attached
    r = build_permission_map(_repo(sql))
    assert [q["table"] for q in r["questions"]] == ["gadgets"]
    assert r["items"] and r["items"][0]["question"]["table"] == "gadgets"
    r2 = build_permission_map(_repo(sql), answers={"gadgets": "public"})
    assert r2["items"] == [] and r2["questions"] == []


def test_never_more_than_three_questions():
    sql = "".join(
        f"create table public.thing{i} (id int);\nalter table public.thing{i} disable row level security;\n"
        for i in range(8))
    assert len(build_permission_map(_repo(sql))["questions"]) == MAX_QUESTIONS


def test_non_supabase_stack_is_honest_not_guessed():
    r = build_permission_map({"requirements.txt": "django\n", "a/models.py": "x=1\n"})
    assert r["supported"] is False and "Supabase" in r["reason"] and r["items"] == []


def test_frontend_usage_shown_in_developer_view():
    r = build_permission_map(_repo(CLEAN_SQL, {"src/a.js": "supabase.from('products').select('*')"}))
    row = next(x for x in r["matrix"] if x["name"] == "Products")
    assert row["developer"]["used_by_app"] == ["select"]

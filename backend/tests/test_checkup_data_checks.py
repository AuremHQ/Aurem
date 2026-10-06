"""Check Library v2 / Group 2 (L3.3, L3.5): fires on the bad fixture, silent
on the clean one."""
from __future__ import annotations

import pytest

from services.checkup_checks_data import check_money_as_float, check_public_uploads
from services.checkup_orchestrator import run_checkup


def _rules(items):
    return [(i["rule_id"], i["evidence"]["file"]) for i in items]


# ── L3.3 ──────────────────────────────────────────────────────────────
@pytest.mark.parametrize("path,text,col", [
    ("supabase/migrations/001.sql", "create table orders (\n  id bigint,\n  total_amount double precision not null\n);", "total_amount"),
    ("db/schema.sql", "create table items (\n  unit_price float8,\n  name text\n);", "unit_price"),
    ("backend/models.py", "class Order(Base):\n    price = Column(Float, nullable=False)\n", "price"),
    ("backend/models.py", "class Order(Base):\n    balance: Mapped[float] = mapped_column(Float)\n", "balance"),
    ("shop/models.py", "class Order(models.Model):\n    subtotal = models.FloatField()\n", "subtotal"),
    ("models/order.js", "const Order = sequelize.define('o', { totalPrice: { type: DataTypes.FLOAT } });", "totalPrice"),
])
def test_money_float_fires(path, text, col):
    [item] = check_money_as_float({path: text})
    assert item["rule_id"] == "money_stored_as_float" and item["severity"] == "high"
    assert col in item["evidence"]["detail"] and item["evidence"]["line"] >= 1


@pytest.mark.parametrize("path,text", [
    ("m.sql", "create table o (id int, total_cents bigint, price numeric(12,2), tax_amount decimal(10,2));"),
    ("m.sql", "create table g (lat float8, lng float8, discount_percent float, tax_rate real, score float4, weight real);"),
    ("m.py", "class P(Base):\n    rating = Column(Float)\n    latitude = Column(Float)\n    price = Column(Numeric(12, 2))\n"),
    ("m.py", "class M(models.Model):\n    temperature = models.FloatField()\n"),
    ("node_modules/x/m.sql", "create table o (price float);"),
    ("README.md", "price float"),
])
def test_money_float_silent_on_clean(path, text):
    assert check_money_as_float({path: text}) == []


# ── L3.5 ──────────────────────────────────────────────────────────────
def test_public_private_looking_bucket_fires_sql():
    sql = "insert into storage.buckets (id, name, public) values ('invoices', 'invoices', true);"
    [item] = check_public_uploads({"supabase/migrations/002.sql": sql})
    assert item["rule_id"] == "uploads_public_by_default" and item["severity"] == "critical"
    assert item["confidence"] == "high" and "invoices" in item["evidence"]["detail"]


def test_public_bucket_in_code_fires():
    js = "await supabase.storage.createBucket('user-documents', { public: true });"
    assert _rules(check_public_uploads({"src/setup.js": js}))[0][0] == "uploads_public_by_default"
    py = "client.storage.create_bucket('uploads', options={'public': True})"
    assert check_public_uploads({"app/storage.py": py})[0]["confidence"] == "high"


def test_s3_public_read_on_upload_fires_and_write_always_does():
    s3 = "await s3.putObject({ Bucket: b, Key: `uploads/${id}`, Body: file, ACL: 'public-read' });"
    assert check_public_uploads({"src/upload.js": s3})[0]["confidence"] == "high"
    rw = "s3.put_object(Bucket=b, Key='x/logo.png', ACL='public-read-write')"
    assert check_public_uploads({"app/s.py": rw})[0]["confidence"] == "high"


def test_unclear_public_bucket_is_unverified_not_red():
    sql = "insert into storage.buckets (id, name, public) values ('stuff', 'stuff', true);"
    res = run_checkup({"supabase/migrations/002.sql": sql, "package.json": "{}"}, include_scanners=False)
    assert "uploads_public_by_default" not in {i["rule_id"] for i in res["items"]}
    assert "uploads_public_by_default" in {u["check"] for u in res["unverified"]}


@pytest.mark.parametrize("path,text", [
    ("m.sql", "insert into storage.buckets (id, name, public) values ('avatars', 'avatars', true);"),
    ("m.sql", "insert into storage.buckets (id, name, public) values ('site-assets', 'site-assets', true);"),
    ("m.sql", "insert into storage.buckets (id, name, public) values ('invoices', 'invoices', false);"),
    ("m.sql", "insert into storage.buckets (id, name) values ('invoices', 'invoices');"),
    ("a.js", "await supabase.storage.createBucket('documents', { public: false });"),
    ("a.js", "await s3.putObject({ Key: 'avatars/a.png', ACL: 'public-read' });"),
    ("scripts/deploy.js", "s3.upload({ Key: 'build/index.html', ACL: 'public-read' })"),
    ("a.js", "await s3.putObject({ Key: k, ACL: 'private' });"),
    ("tests/storage.test.js", "createBucket('uploads', { public: true })"),
])
def test_public_uploads_silent_on_clean(path, text):
    out = [i for i in check_public_uploads({path: text}) if i["confidence"] != "unverified"]
    assert out == []

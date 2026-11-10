"""
Tests Paso 3 — setup BigQuery datasets (mocks; sin credenciales GCP).

CI debe pasar sin ADC ni project real.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "setup_bigquery.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("setup_bigquery", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["setup_bigquery"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def setup_bq():
    return _load_module()


def test_location_constant(setup_bq):
    assert setup_bq.BQ_LOCATION == "southamerica-west1"


def test_dataset_specs_complete(setup_bq):
    ids = [s["dataset_id"] for s in setup_bq.DATASET_SPECS]
    assert ids == ["bronze_kora", "silver_kora", "gold_kora"]
    for spec in setup_bq.DATASET_SPECS:
        assert "description" in spec and spec["description"]
        labels = spec["labels"]
        assert labels["layer"] in {"bronze", "silver", "gold"}
        assert labels["env"] == "sandbox"


def test_load_project_id_from_env(setup_bq, tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text("GCP_PROJECT=demo-project\n", encoding="utf-8")
    monkeypatch.delenv("GCP_PROJECT", raising=False)
    monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)
    monkeypatch.delenv("GCLOUD_PROJECT", raising=False)
    assert setup_bq.load_project_id(env_file) == "demo-project"


def test_load_project_id_prefers_gcp_project(setup_bq, tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "GCP_PROJECT=from-env-file\nGOOGLE_CLOUD_PROJECT=other\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("GCP_PROJECT", raising=False)
    monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)
    assert setup_bq.load_project_id(env_file) == "from-env-file"


def test_load_project_id_missing_raises(setup_bq, tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text("# empty\n", encoding="utf-8")
    monkeypatch.delenv("GCP_PROJECT", raising=False)
    monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)
    monkeypatch.delenv("GCLOUD_PROJECT", raising=False)
    # Evita caer en .env.example del repo
    with patch.object(setup_bq, "ROOT", tmp_path):
        with pytest.raises(SystemExit):
            setup_bq.load_project_id(env_file)


def _not_found_exc():
    from google.api_core.exceptions import NotFound

    return NotFound("not found")


def test_ensure_dataset_creates_when_missing(setup_bq):
    client = MagicMock()
    client.get_dataset.side_effect = _not_found_exc()
    created = SimpleNamespace(location="southamerica-west1")
    client.create_dataset.return_value = created

    with patch.dict("sys.modules", {"google.cloud.bigquery": MagicMock()}):
        # ensure_dataset imports bigquery.Dataset — stub it
        fake_bq = MagicMock()
        fake_ds = MagicMock()
        fake_bq.Dataset.return_value = fake_ds
        with patch.dict(
            "sys.modules",
            {
                "google.cloud": MagicMock(bigquery=fake_bq),
                "google.cloud.bigquery": fake_bq,
            },
        ):
            # Re-import path uses already-loaded google in ensure_dataset
            pass

    # Call with real import path mocked at function level
    with patch("google.cloud.bigquery.Dataset") as DatasetCls:
        fake_ds = MagicMock()
        DatasetCls.return_value = fake_ds
        result = setup_bq.ensure_dataset(
            client,
            project_id="kora-sports",
            dataset_id="bronze_kora",
            description="desc",
            labels={"layer": "bronze"},
            dry_run=False,
        )

    assert result.action == "created"
    assert result.dataset_id == "bronze_kora"
    client.create_dataset.assert_called_once()
    assert fake_ds.location == "southamerica-west1"
    assert fake_ds.description == "desc"
    assert fake_ds.labels == {"layer": "bronze"}


def test_ensure_dataset_idempotent_when_exists(setup_bq):
    client = MagicMock()
    existing = MagicMock()
    existing.location = "southamerica-west1"
    existing.description = "old"
    existing.labels = {"layer": "bronze", "extra": "keep"}
    client.get_dataset.return_value = existing

    result = setup_bq.ensure_dataset(
        client,
        project_id="kora-sports",
        dataset_id="bronze_kora",
        description="new desc",
        labels={"layer": "bronze", "env": "sandbox"},
        dry_run=False,
    )

    assert result.action == "exists"
    client.create_dataset.assert_not_called()
    client.update_dataset.assert_called_once()
    # Labels merged — no borra 'extra'
    assert existing.labels["extra"] == "keep"
    assert existing.labels["env"] == "sandbox"
    assert existing.description == "new desc"


def test_ensure_dataset_dry_run_no_create(setup_bq):
    client = MagicMock()
    client.get_dataset.side_effect = _not_found_exc()

    result = setup_bq.ensure_dataset(
        client,
        project_id="kora-sports",
        dataset_id="silver_kora",
        description="d",
        labels={"layer": "silver"},
        dry_run=True,
    )

    assert result.action == "would_create"
    client.create_dataset.assert_not_called()
    client.update_dataset.assert_not_called()


def test_ensure_dataset_dry_run_existing(setup_bq):
    client = MagicMock()
    existing = MagicMock()
    existing.location = "southamerica-west1"
    existing.description = "d"
    existing.labels = {}
    client.get_dataset.return_value = existing

    result = setup_bq.ensure_dataset(
        client,
        project_id="kora-sports",
        dataset_id="gold_kora",
        description="d",
        labels={},
        dry_run=True,
    )
    assert result.action == "would_skip"
    client.update_dataset.assert_not_called()


def test_verify_datasets_ok(setup_bq):
    found = [
        {"dataset_id": "bronze_kora", "location": "southamerica-west1"},
        {"dataset_id": "gold_kora", "location": "southamerica-west1"},
        {"dataset_id": "silver_kora", "location": "southamerica-west1"},
    ]
    assert setup_bq.verify_datasets(found) == []


def test_verify_datasets_missing_and_mismatch(setup_bq):
    found = [
        {"dataset_id": "bronze_kora", "location": "US"},
        {"dataset_id": "silver_kora", "location": "southamerica-west1"},
    ]
    errors = setup_bq.verify_datasets(found)
    assert "missing:gold_kora" in errors
    assert any(e.startswith("location_mismatch:bronze_kora") for e in errors)


def test_run_idempotent_second_pass(setup_bq):
    """Dos corridas con client mock: create luego exists — exit 0 ambas."""
    client = MagicMock()

    # Primera: nothing exists → create
    client.get_dataset.side_effect = _not_found_exc()
    created = SimpleNamespace(
        dataset_id="x",
        location="southamerica-west1",
        project="kora-sports",
        description="",
        labels={},
    )

    def _create(ds, exists_ok=True):
        out = MagicMock()
        out.location = "southamerica-west1"
        out.dataset_id = ds.dataset_id if hasattr(ds, "dataset_id") else "bronze_kora"
        return out

    # After creates, list_datasets returns the three
    listed = []

    def list_datasets(project=None):
        return listed

    client.list_datasets.side_effect = list_datasets

    with patch("google.cloud.bigquery.Dataset") as DatasetCls:

        def make_ds(full_id):
            m = MagicMock()
            m.dataset_id = full_id.split(".")[-1]
            return m

        DatasetCls.side_effect = make_ds

        def create_side_effect(ds, exists_ok=True):
            item = MagicMock()
            item.location = "southamerica-west1"
            item.dataset_id = ds.dataset_id
            item.project = "kora-sports"
            item.description = ds.description
            item.labels = ds.labels
            listed.append(SimpleNamespace(dataset_id=ds.dataset_id))
            # get_dataset for list verification
            return item

        client.create_dataset.side_effect = create_side_effect

        # get_dataset: first 3 calls NotFound (ensure), then get for list
        state = {"n": 0}

        def get_side_effect(ds_ref):
            state["n"] += 1
            # First pass ensure: 3 NotFound
            if state["n"] <= 3:
                raise _not_found_exc()
            # list_lab_datasets get_dataset
            ds_id = ds_ref if isinstance(ds_ref, str) else getattr(ds_ref, "dataset_id", str(ds_ref))
            if "." in str(ds_id):
                ds_id = str(ds_id).split(".")[-1]
            m = MagicMock()
            m.dataset_id = ds_id
            m.location = "southamerica-west1"
            m.project = "kora-sports"
            m.description = "d"
            return m

        client.get_dataset.side_effect = get_side_effect

        rc1 = setup_bq.run(dry_run=False, project_id="kora-sports", client=client)
        assert rc1 == 0
        assert client.create_dataset.call_count == 3

    # Segunda corrida: todos existen
    client2 = MagicMock()
    existing_map = {}
    for ds_id in ("bronze_kora", "silver_kora", "gold_kora"):
        m = MagicMock()
        m.dataset_id = ds_id
        m.location = "southamerica-west1"
        m.project = "kora-sports"
        m.description = next(
            s["description"] for s in setup_bq.DATASET_SPECS if s["dataset_id"] == ds_id
        )
        m.labels = dict(
            next(s["labels"] for s in setup_bq.DATASET_SPECS if s["dataset_id"] == ds_id)
        )
        existing_map[ds_id] = m

    def get2(ref):
        ds_id = ref if isinstance(ref, str) else str(ref)
        if "." in ds_id:
            ds_id = ds_id.split(".")[-1]
        return existing_map[ds_id]

    client2.get_dataset.side_effect = get2
    client2.list_datasets.return_value = [
        SimpleNamespace(dataset_id=k) for k in existing_map
    ]

    rc2 = setup_bq.run(dry_run=False, project_id="kora-sports", client=client2)
    assert rc2 == 0
    client2.create_dataset.assert_not_called()


def test_run_dry_run_skips_verify(setup_bq, capsys):
    client = MagicMock()
    client.get_dataset.side_effect = _not_found_exc()
    rc = setup_bq.run(dry_run=True, project_id="kora-sports", client=client)
    assert rc == 0
    client.create_dataset.assert_not_called()
    client.list_datasets.assert_not_called()
    out = capsys.readouterr().out
    assert "dry_run=True" in out
    assert "bronze_kora" in out


def test_parse_args_dry_run(setup_bq):
    args = setup_bq.parse_args(["--dry-run"])
    assert args.dry_run is True
    args2 = setup_bq.parse_args([])
    assert args2.dry_run is False

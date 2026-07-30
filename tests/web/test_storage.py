from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest

from outdoor_maps_plot.web.config import WebSettings
from outdoor_maps_plot.web.errors import ApiError
from outdoor_maps_plot.web.storage import WorkspaceStore, contained_path, utc_now


def test_contained_path_rejects_escape(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    assert contained_path(root, root / "child") == (root / "child").resolve()
    with pytest.raises(ApiError, match="invalid"):
        contained_path(root, root / ".." / "outside")


def test_expired_workspace_cleanup(tmp_path: Path) -> None:
    store = WorkspaceStore(WebSettings(job_root=tmp_path / "jobs", cache_root=tmp_path / "cache"))
    upload_id, workspace = store.create_workspace()
    record = store.register(upload_id, workspace, [], [])
    record.expires_at = utc_now() - timedelta(seconds=1)

    assert store.cleanup_expired(set()) == [upload_id]
    assert not workspace.exists()
    assert upload_id not in store.uploads


def test_active_workspace_is_not_expired(tmp_path: Path) -> None:
    store = WorkspaceStore(WebSettings(job_root=tmp_path / "jobs", cache_root=tmp_path / "cache"))
    upload_id, workspace = store.create_workspace()
    record = store.register(upload_id, workspace, [], [])
    record.expires_at = utc_now() - timedelta(seconds=1)

    assert store.cleanup_expired({upload_id}) == []
    assert workspace.exists()

import asyncio
import json
from unittest.mock import AsyncMock

import pytest

from scripts import benchmark_codex_settings as benchmark


@pytest.mark.asyncio
async def test_shadow_benchmark_cleans_up_clients_on_failure(monkeypatch):
    client = AsyncMock()
    current_loop = asyncio.get_running_loop()
    pool = {current_loop: {"current": client}, "other-loop": {"untouched": object()}}
    monkeypatch.setattr(benchmark.provider, "_clients", pool)
    monkeypatch.setattr(benchmark, "run", AsyncMock(side_effect=ValueError("invalid frames")))
    with pytest.raises(ValueError, match="invalid frames"):
        await benchmark.main(None)
    client.close.assert_awaited_once()
    assert "other-loop" in pool and current_loop not in pool


@pytest.mark.asyncio
async def test_shadow_benchmark_rejects_failed_ground_truth(tmp_path):
    from types import SimpleNamespace

    (tmp_path / "report.json").write_text(
        json.dumps(
            {"status": "passed", "checks": [{"passed": True}, {"passed": False}, {"passed": True}]}
        )
    )
    with pytest.raises(ValueError, match="passed three-check"):
        await benchmark.run(SimpleNamespace(frames=tmp_path))

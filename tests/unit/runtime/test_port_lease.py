import pytest

from artemis.runtime.port_lease import PortLeaseError, PortLeaseManager


def test_port_lease_is_exclusive_and_reusable_after_release(tmp_path):
    manager = PortLeaseManager(
        lease_dir=tmp_path,
        availability_probe=lambda _host, _port: True,
    )

    first = manager.acquire(4810, 4810)
    assert first.port == 4810

    with pytest.raises(PortLeaseError, match="4810"):
        manager.acquire(4810, 4810)

    first.release()
    second = manager.acquire(4810, 4810)
    assert second.port == 4810
    second.release()


def test_port_lease_skips_ports_owned_by_external_processes(tmp_path):
    manager = PortLeaseManager(
        lease_dir=tmp_path,
        availability_probe=lambda _host, port: port != 4810,
    )

    lease = manager.acquire(4810, 4811)
    assert lease.port == 4811
    lease.release()

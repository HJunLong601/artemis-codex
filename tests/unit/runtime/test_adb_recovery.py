from unittest.mock import Mock

import pytest
from adbutils import AdbError

from artemis.runtime import adb_recovery as recovery
from artemis.runtime.adb_endpoint import AdbEndpoint


@pytest.fixture
def repair(monkeypatch):
    reconnect = Mock()
    monkeypatch.setattr(recovery, "reconnect_wireless", reconnect)
    monkeypatch.setattr(recovery.time, "sleep", lambda _: None)
    return reconnect


def test_read_recovers_twice_on_its_original_endpoint(repair):
    endpoint = AdbEndpoint.create("other-server", 5040)
    operation = Mock(side_effect=[AdbError("device offline"), OSError("connection reset"), "frame"])
    assert recovery.retry_device_read(operation, "192.168.1.73:5555", endpoint) == "frame"
    assert operation.call_count == 3
    assert repair.call_count == 2
    assert all(c.args[:2] == ("192.168.1.73:5555", endpoint) for c in repair.call_args_list)


@pytest.mark.parametrize(
    "error", [AdbError("device unauthorized"), RuntimeError("invalid command")]
)
def test_permanent_errors_are_not_retried(repair, error):
    operation = Mock(side_effect=error)
    with pytest.raises(type(error)):
        recovery.retry_device_read(operation, "phone")
    assert operation.call_count == 1
    repair.assert_not_called()


def test_retries_are_bounded_and_nested_reads_do_not_multiply(repair):
    operation = Mock(side_effect=AdbError("device offline"))
    with pytest.raises(AdbError):
        recovery.retry_device_read(lambda: recovery.retry_device_read(operation, "phone"), "phone")
    assert operation.call_count == 3
    assert repair.call_count == 2


def test_only_the_selected_wireless_endpoint_is_reconnected(monkeypatch):
    run = Mock()
    monkeypatch.setattr(recovery.subprocess, "run", run)
    monkeypatch.setattr("artemis.runtime.adb_endpoint.toolchain.resolve", lambda _: "adb")
    endpoint = AdbEndpoint.create("remote-adb", 5040)
    recovery.reconnect_wireless("usb-serial", endpoint)
    run.assert_not_called()
    recovery.reconnect_wireless("192.168.1.73:5555", endpoint)
    assert run.call_args.args[0] == [
        "adb",
        "-H",
        "remote-adb",
        "-P",
        "5040",
        "connect",
        "192.168.1.73:5555",
    ]


def test_driver_never_replays_an_uncertain_tap(repair):
    from artemis.drivers.android.adb_driver import AndroidAdbDriver

    client = Mock(host="127.0.0.1", port=5037)
    device = client.device.return_value
    device.get_state.return_value = "device"
    device.shell.side_effect = AdbError("connection reset")
    driver = AndroidAdbDriver("192.168.1.73:5555", client)
    with pytest.raises(AdbError):
        driver._shell("input tap 10 20")
    assert device.shell.call_count == 1


def test_driver_recovers_before_dispatching_action_once(repair):
    from artemis.drivers.android.adb_driver import AndroidAdbDriver

    client = Mock(host="127.0.0.1", port=5037)
    device = client.device.return_value
    device.get_state.side_effect = [AdbError("device offline"), "device"]
    device.shell.return_value = ""
    driver = AndroidAdbDriver("192.168.1.73:5555", client)
    driver._shell("input tap 10 20")
    assert device.get_state.call_count == 2
    device.shell.assert_called_once_with("input tap 10 20")


def test_driver_does_not_replay_arbitrary_shell_mutations(repair):
    from artemis.drivers.android.adb_driver import AndroidAdbDriver

    client = Mock(host="127.0.0.1", port=5037)
    device = client.device.return_value
    device.get_state.return_value = "device"
    device.shell.side_effect = AdbError("connection reset")
    driver = AndroidAdbDriver("192.168.1.73:5555", client)
    with pytest.raises(AdbError):
        driver._shell("dumpsys battery set level 10")
    assert device.shell.call_count == 1


@pytest.mark.parametrize(
    "error", [TimeoutError(), ConnectionResetError(10054, "远程主机强迫关闭了一个现有的连接")]
)
def test_timeouts_and_localized_socket_errors_are_retried(repair, error):
    if isinstance(error, ConnectionResetError):
        error.winerror = 10054
    operation = Mock(side_effect=[error, "ok"])
    assert recovery.retry_device_read(operation, "phone") == "ok"
    assert operation.call_count == 2


def test_recovery_deadline_prevents_another_attempt(repair, monkeypatch):
    monkeypatch.setattr(recovery.time, "monotonic", Mock(side_effect=[0, 13]))
    operation = Mock(side_effect=AdbError("device offline"))
    with pytest.raises(AdbError):
        recovery.retry_device_read(operation, "phone")
    assert operation.call_count == 1
    repair.assert_not_called()

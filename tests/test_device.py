import pytest

from espbench.device import Device, DeviceError


class _Response:
    def __init__(self, status_code=200, body=None, bad_json=False):
        self.status_code = status_code
        self._body = body or {}
        self._bad_json = bad_json

    def json(self):
        if self._bad_json:
            raise ValueError("expecting value")
        return self._body


class _Session:
    def __init__(self, response=None, exc=None):
        self.response = response
        self.exc = exc

    def get(self, *args, **kwargs):
        if self.exc:
            raise self.exc
        return self.response


def test_stats_invalid_json_raises_device_error():
    device = Device("example.invalid")
    device.session = _Session(response=_Response(bad_json=True))
    with pytest.raises(DeviceError, match="invalid JSON"):
        device.stats()


def test_stats_non_200_raises_device_error():
    device = Device("example.invalid")
    device.session = _Session(response=_Response(status_code=503))
    with pytest.raises(DeviceError, match="503"):
        device.stats()


def test_stats_ok_returns_payload():
    device = Device("example.invalid")
    device.session = _Session(response=_Response(body={"boot_count": 3}))
    assert device.stats() == {"boot_count": 3}


def test_version_returns_fw():
    device = Device("example.invalid")
    device.session = _Session(response=_Response(body={"fw": "1.2.3"}))
    assert device.version() == "1.2.3"


def test_version_errors():
    device = Device("example.invalid")
    device.session = _Session(response=_Response(status_code=404))
    with pytest.raises(DeviceError, match="404"):
        device.version()
    device.session = _Session(response=_Response(bad_json=True))
    with pytest.raises(DeviceError, match="invalid JSON"):
        device.version()
    device.session = _Session(response=_Response(body={}))
    with pytest.raises(DeviceError, match="fw"):
        device.version()
    device.session = _Session(response=_Response(body={"fw": ""}))
    with pytest.raises(DeviceError, match="fw"):
        device.version()


def test_log_returns_payload():
    device = Device("example.invalid")
    payload = {"fw": "1.2.3", "log": ["BOOT boot_count=1"]}
    device.session = _Session(response=_Response(body=payload))
    assert device.log() == payload


def test_log_errors():
    device = Device("example.invalid")
    device.session = _Session(response=_Response(status_code=503))
    with pytest.raises(DeviceError, match="503"):
        device.log()
    device.session = _Session(response=_Response(body=[1, 2]))
    with pytest.raises(DeviceError, match="JSON object"):
        device.log()
    device.session = _Session(response=_Response(bad_json=True))
    with pytest.raises(DeviceError, match="invalid JSON"):
        device.log()

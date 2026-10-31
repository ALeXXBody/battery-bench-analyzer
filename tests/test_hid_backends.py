"""Tests for the cross-platform HID backend selection and pure helpers."""

from unittest import mock

from benchanalyzer.unlock.cp2112 import (
    parse_linux_hid_id,
)


def test_parse_linux_hid_id():
    text = "DRIVER=hid-generic\nHID_ID=0003:000010C4:0000EA90\nHID_NAME=CP2112\n"
    assert parse_linux_hid_id(text) == (0x10C4, 0xEA90)


def test_parse_linux_hid_id_missing_defaults_zero():
    assert parse_linux_hid_id("DRIVER=usbhid\n") == (0, 0)


class fake_windows_api:
    pass


def test_make_hid_api_dispatch(monkeypatch):
    import benchanalyzer.unlock.cp2112 as c

    with (
        mock.patch("benchanalyzer.unlock.cp2112.platform") as plat,
        mock.patch.object(c, "HidApi", fake_windows_api),
    ):
        plat.system.return_value = "Windows"
        obj = c.make_hid_api()
        assert type(obj).__name__ == "fake_windows_api"

    with mock.patch("benchanalyzer.unlock.cp2112.platform") as plat:
        plat.system.return_value = "Linux"
        obj = c.make_hid_api()
        assert type(obj).__name__ == "_LinuxHidApi"

    class fake_mac_api:
        pass

    with (
        mock.patch("benchanalyzer.unlock.cp2112.platform") as plat,
        mock.patch.object(c, "_MacHidApi", lambda: fake_mac_api()),
    ):
        plat.system.return_value = "Darwin"
        obj = c.make_hid_api()
        assert type(obj).__name__ == "fake_mac_api"


def test_cp2112_no_longer_windows_only(monkeypatch):
    # The platform gate is gone: _api() must dispatch, not raise,
    # on any OS with a HID backend available.
    import benchanalyzer.unlock.cp2112 as c

    with mock.patch("benchanalyzer.unlock.cp2112.platform") as plat:
        plat.system.return_value = "Linux"
        api = c.Cp2112Smbus._api()
        assert type(api).__name__ == "_LinuxHidApi"

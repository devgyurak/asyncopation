"""`SupportsServerData`: the optional protocol of drivers that read MySQL server data."""

from asyncopation.drivers.base import SupportsServerData


def test_supports_server_data_declares_a_server_data_property():
    # when
    declared = SupportsServerData.server_data
    # then
    assert isinstance(declared, property)

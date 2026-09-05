"""mDNS advertisement.

The captain's network is DHCP, so the Android app finds the rig by browsing for
``_fclights._tcp`` rather than by a hardcoded address.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

from fclights.discovery import (
    SERVICE_FILENAME,
    SERVICE_TYPE,
    render_service_file,
    write_service_file,
)


class TestServiceFile:
    def test_it_is_well_formed_xml(self):
        ET.fromstring(render_service_file(port=7891, version="1.0.0", pixels=512))

    def test_it_advertises_the_right_type_and_port(self):
        root = ET.fromstring(render_service_file(port=7891, version="1.0.0", pixels=512))
        service = root.find("service")
        assert service.find("type").text == SERVICE_TYPE
        assert service.find("port").text == "7891"

    def test_txt_records_tell_the_client_where_the_api_lives(self):
        root = ET.fromstring(render_service_file(port=7891, version="1.2.3", pixels=512))
        records = {r.text.split("=", 1)[0]: r.text.split("=", 1)[1]
                   for r in root.find("service").findall("txt-record")}
        assert records["path"] == "/api"
        assert records["version"] == "1.2.3"
        assert records["pixels"] == "512"

    def test_the_name_uses_the_host_wildcard(self):
        # %h lets a second rig on the same network announce itself distinctly
        # without any extra configuration.
        root = ET.fromstring(render_service_file(port=7891, version="1.0.0", pixels=512))
        assert root.find("name").get("replace-wildcards") == "yes"
        assert "%h" in root.find("name").text

    def test_writing_creates_the_directory_and_the_file(self, tmp_path):
        target = tmp_path / "avahi" / "services"
        path = write_service_file(port=7891, version="1.0.0", pixels=512, directory=target)
        assert path == target / SERVICE_FILENAME
        ET.fromstring(path.read_text())

    def test_rewriting_replaces_rather_than_appends(self, tmp_path):
        write_service_file(port=1111, version="1.0.0", pixels=1, directory=tmp_path)
        path = write_service_file(port=2222, version="1.0.0", pixels=2, directory=tmp_path)
        assert path.read_text().count("<service-group>") == 1
        assert "2222" in path.read_text()

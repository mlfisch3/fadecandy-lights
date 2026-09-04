"""Config loading, overrides and the CLI's argument handling."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from fclights.cli import build_parser, insert_default_command, resolve_config
from fclights.config import Config, ConfigError, apply_overrides, config_from_dict, load_config


class TestDefaults:
    def test_defaults_are_safe_and_lan_reachable(self):
        config = Config()
        assert config.fps == 60.0
        assert config.power.gamma == 1.0, "the default power model must over-predict, not under"
        assert config.server.host == "0.0.0.0", "phones on the LAN have to reach it"
        assert config.opc.host == "127.0.0.1"
        assert config.opc.port == 7890
        assert config.dither is True, "8-bit OPC needs dithering for slow fades"

    def test_the_default_ceiling_derates_the_smaller_supply(self):
        # The two supplies on hand are 5 V 30 A and 5 V 60 A. Defaulting to the
        # smaller one derated to 80% means swapping in the smaller supply cannot
        # overload it, and no supply is asked to hold nameplate continuously.
        assert Config().power.limit_amps == 24.0 == 30.0 * 0.8


class TestParsing:
    def test_a_partial_document_keeps_the_other_defaults(self):
        config = config_from_dict({"fps": 30.0, "power": {"limit_amps": 12.0}})
        assert config.fps == 30.0
        assert config.power.limit_amps == 12.0
        assert config.power.ma_per_channel == 20.0
        assert config.server.port == 7891

    def test_unknown_keys_are_rejected(self):
        # A typo in a config file should be reported, not silently ignored while
        # the setting it was meant to change stays at its default.
        with pytest.raises(ConfigError, match="unknown key"):
            config_from_dict({"fpz": 30})
        with pytest.raises(ConfigError, match="unknown key"):
            config_from_dict({"power": {"limit_watts": 100}})

    @pytest.mark.parametrize("fps", [0, 0.5, 500])
    def test_absurd_frame_rates_are_rejected(self, fps):
        with pytest.raises(ConfigError, match="fps"):
            config_from_dict({"fps": fps})

    def test_a_non_positive_power_ceiling_is_rejected(self):
        with pytest.raises(ConfigError, match="limit_amps"):
            config_from_dict({"power": {"limit_amps": 0}})

    def test_bad_ports_are_rejected(self):
        with pytest.raises(ConfigError, match="ports"):
            config_from_dict({"server": {"port": 0}})

    def test_a_badly_typed_value_is_reported(self):
        with pytest.raises(ConfigError, match="badly typed"):
            config_from_dict({"fps": "fast"})

    def test_a_non_object_document_is_rejected(self):
        with pytest.raises(ConfigError, match="JSON object"):
            config_from_dict([1, 2, 3])


class TestFileLoading:
    def test_no_path_yields_defaults(self):
        assert load_config(None) == Config()

    def test_a_missing_named_file_is_an_error(self, tmp_path):
        with pytest.raises(ConfigError, match="not found"):
            load_config(tmp_path / "nope.json")

    def test_malformed_json_is_reported_as_such(self, tmp_path):
        path = tmp_path / "c.json"
        path.write_text("{oops")
        with pytest.raises(ConfigError, match="not valid JSON"):
            load_config(path)

    def test_shipped_example_config_is_valid(self):
        example = Path(__file__).resolve().parents[1] / "config" / "fclights.example.json"
        if not example.exists():
            pytest.skip("example config not present")
        assert load_config(example).fps > 0

    def test_config_serialises_back_to_json(self):
        json.dumps(Config().to_dict())


class TestOverrides:
    def test_nested_sections_are_reachable(self):
        config = apply_overrides(Config(), limit_amps=20.0, opc_port=9999, port=8080)
        assert config.power.limit_amps == 20.0
        assert config.opc.port == 9999
        assert config.server.port == 8080

    def test_none_values_are_ignored(self):
        assert apply_overrides(Config(), fps=None, limit_amps=None) == Config()


class TestCli:
    def test_run_is_the_implicit_subcommand(self):
        assert insert_default_command(["--simulate"]) == ["run", "--simulate"]
        assert insert_default_command(["check"]) == ["check"]
        assert insert_default_command(["--help"]) == ["--help"]

    def test_flags_override_the_config_file(self, tmp_path):
        path = tmp_path / "c.json"
        path.write_text(json.dumps({"fps": 24.0, "power": {"limit_amps": 8.0}}))
        args = build_parser().parse_args(
            ["run", "-c", str(path), "--fps", "48", "--limit-amps", "3"]
        )
        config = resolve_config(args)
        assert config.fps == 48.0
        assert config.power.limit_amps == 3.0

    def test_simulate_keeps_state_out_of_var_lib(self, tmp_path):
        # A developer running --simulate on a laptop cannot write to /var/lib,
        # and should not have to think about it.
        args = build_parser().parse_args(["run", "--simulate"])
        config = resolve_config(args)
        assert config.simulate is True
        assert "var/lib" not in str(config.state_path)

    def test_an_explicit_state_path_wins_over_the_simulate_default(self, tmp_path):
        target = tmp_path / "mine.json"
        args = build_parser().parse_args(["run", "--simulate", "--state", str(target)])
        assert resolve_config(args).state_path == target

    def test_every_subcommand_accepts_the_same_options(self):
        for command in ("run", "check", "announce"):
            args = build_parser().parse_args([command, "--simulate", "--pixels", "64"])
            assert resolve_config(args).simulate_pixels == 64

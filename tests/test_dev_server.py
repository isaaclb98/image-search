from __future__ import annotations

import argparse

from search.dev_server import build_parser


def test_no_model_flag_exists_and_defaults_off():
    parser = build_parser()

    assert parser.parse_args([]).no_model is False
    assert parser.parse_args(["--no-model"]).no_model is True


def test_no_model_flag_accepts_uvicorn_server_options():
    parser = build_parser()
    args = parser.parse_args(["--no-model", "--host", "127.0.0.1", "--port", "8765"])

    assert args.no_model is True
    assert args.host == "127.0.0.1"
    assert args.port == 8765


def test_demo_data_flag_defaults_to_five_photos():
    parser = build_parser()

    assert parser.parse_args([]).demo_data is False
    assert parser.parse_args(["--demo-data"]).demo_data is True
    assert parser.parse_args(["--demo-data", "--demo-count", "5"]).demo_count == 5


def test_parser_is_standard_argparse():
    assert isinstance(build_parser(), argparse.ArgumentParser)

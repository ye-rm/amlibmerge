"""Unit tests for bilingual (English / Simplified Chinese) i18n, detection, and live TUI switching."""

from __future__ import annotations

import asyncio
from pathlib import Path

from textual.widgets import Button
from typer.testing import CliRunner

from amlibmerge.cli import app as cli_app
from amlibmerge.config import load_language_preference, save_language_preference
from amlibmerge.i18n import (
    TRANSLATIONS,
    detect_system_language,
    get_language,
    normalize_language,
    reset_language_cache,
    resolve_language,
    set_language,
    t,
)
from amlibmerge.tui import AmlibmergeApp


def test_translation_tables_have_100_percent_key_parity() -> None:
    en_keys = set(TRANSLATIONS["en"].keys())
    zh_keys = set(TRANSLATIONS["zh"].keys())
    assert en_keys == zh_keys


def test_normalize_language_variants() -> None:
    assert normalize_language("en") == "en"
    assert normalize_language("en-US") == "en"
    assert normalize_language("zh") == "zh"
    assert normalize_language("zh_CN") == "zh"
    assert normalize_language("zh-Hans-CN") == "zh"
    assert normalize_language("auto") == "auto"
    assert normalize_language("fr-FR") is None


def test_detect_system_language_defaults_to_en_and_detects_zh(monkeypatch) -> None:
    for var in ("LC_ALL", "LC_MESSAGES", "LANG"):
        monkeypatch.delenv(var, raising=False)

    monkeypatch.setenv("LANG", "zh_CN.UTF-8")
    assert detect_system_language() == "zh"

    monkeypatch.setenv("LANG", "en_US.UTF-8")
    assert detect_system_language() == "en"

    monkeypatch.setenv("LANG", "ja_JP.UTF-8")
    assert detect_system_language() == "en"


def test_language_resolution_precedence(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AMLIBMERGE_HOME", str(tmp_path))
    monkeypatch.delenv("AMLIBMERGE_LANG", raising=False)
    monkeypatch.setenv("LANG", "en_US.UTF-8")
    reset_language_cache()

    # 1. System default -> English on non-Chinese OS
    assert resolve_language(argv=[]) == ("en", "system")

    # 2. Saved config overrides system
    save_language_preference("zh")
    assert resolve_language(argv=[]) == ("zh", "config")

    # 3. Env var overrides saved config
    monkeypatch.setenv("AMLIBMERGE_LANG", "en")
    assert resolve_language(argv=[]) == ("en", "env")

    # 4. CLI --lang overrides env var
    assert resolve_language(argv=["--lang", "zh"]) == ("zh", "cli")
    assert resolve_language(argv=["--lang=en"]) == ("en", "cli")


def test_cli_lang_command_saves_and_switches_language(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AMLIBMERGE_HOME", str(tmp_path))
    monkeypatch.delenv("AMLIBMERGE_LANG", raising=False)
    monkeypatch.setenv("LANG", "en_US.UTF-8")
    reset_language_cache()

    runner = CliRunner()
    res_en = runner.invoke(cli_app, ["lang", "en"])
    assert res_en.exit_code == 0
    assert load_language_preference() == "en"
    assert "Saved display language preference" in res_en.stdout

    res_zh = runner.invoke(cli_app, ["lang", "zh"])
    assert res_zh.exit_code == 0
    assert load_language_preference() == "zh"
    assert "已保存语言偏好" in res_zh.stdout

    res_auto = runner.invoke(cli_app, ["lang", "auto"])
    assert res_auto.exit_code == 0
    assert load_language_preference() == "auto"
    assert get_language() == "en"


def test_tui_live_language_toggle_switches_labels_and_persists(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AMLIBMERGE_HOME", str(tmp_path))
    monkeypatch.delenv("AMLIBMERGE_LANG", raising=False)
    set_language("en")

    async def scenario() -> None:
        app = AmlibmergeApp(
            reports_dir=tmp_path / "reports",
            backups_dir=tmp_path / "backups",
            journals_dir=tmp_path / "journals",
        )
        async with app.run_test(size=(120, 40)) as pilot:
            assert str(app.query_one("#btn-scan", Button).label) == "Scan (S)"
            assert get_language() == "en"

            # Press G to toggle live to Simplified Chinese
            await pilot.press("g")
            await pilot.pause()
            assert get_language() == "zh"
            assert load_language_preference() == "zh"
            assert str(app.query_one("#btn-scan", Button).label) == "扫描 (S)"

            # Press G again to toggle back to English
            await pilot.press("g")
            await pilot.pause()
            assert get_language() == "en"
            assert load_language_preference() == "en"
            assert str(app.query_one("#btn-scan", Button).label) == "Scan (S)"

    asyncio.run(scenario())
    reset_language_cache()

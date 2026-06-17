import importlib.util
import json
from pathlib import Path


def load_installer():
    path = Path(__file__).parents[1] / "scripts" / "install_codex_plugin.py"
    spec = importlib.util.spec_from_file_location("install_codex_plugin", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_update_marketplace_preserves_other_plugins(tmp_path):
    installer = load_installer()
    home = tmp_path / "home"
    repo_root = home / "plugins" / "music-history"
    marketplace_path = home / ".agents" / "plugins" / "marketplace.json"
    marketplace_path.parent.mkdir(parents=True)
    marketplace_path.write_text(
        json.dumps(
            {
                "name": "local-plugins",
                "plugins": [{"name": "other-plugin"}],
            }
        )
    )

    installer.update_marketplace(repo_root, home)

    marketplace = json.loads(marketplace_path.read_text())
    assert marketplace["plugins"] == [
        {"name": "other-plugin"},
        {
            "name": "music-history",
            "source": {"source": "local", "path": "./plugins/music-history"},
            "policy": {"installation": "AVAILABLE", "authentication": "ON_INSTALL"},
            "category": "Productivity",
        },
    ]


def test_install_cli_uses_stable_uv_tool_environment(monkeypatch, tmp_path):
    installer = load_installer()
    calls = []
    monkeypatch.setattr(
        installer.subprocess,
        "run",
        lambda command, check: calls.append((command, check)),
    )

    installer.install_cli("uv", tmp_path)

    assert calls == [
        (
            ["uv", "tool", "install", "--editable", "--force", str(tmp_path)],
            True,
        )
    ]

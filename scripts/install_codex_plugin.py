#!/usr/bin/env python3
"""Register and install Music History as a durable local Codex plugin."""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

PLUGIN_NAME = "music-history"
MARKETPLACE_NAME = "local-plugins"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--home",
        type=Path,
        default=Path.home(),
        help="Home directory containing .agents/plugins/marketplace.json.",
    )
    parser.add_argument(
        "--codex",
        default="codex",
        help="Codex executable to invoke.",
    )
    return parser.parse_args()


def marketplace_entry(repo_root: Path, home: Path) -> dict[str, object]:
    try:
        relative = repo_root.relative_to(home)
        source_path = f"./{relative.as_posix()}"
    except ValueError:
        source_path = str(repo_root)

    return {
        "name": PLUGIN_NAME,
        "source": {"source": "local", "path": source_path},
        "policy": {"installation": "AVAILABLE", "authentication": "ON_INSTALL"},
        "category": "Productivity",
    }


def update_marketplace(repo_root: Path, home: Path) -> Path:
    marketplace_path = home / ".agents" / "plugins" / "marketplace.json"
    marketplace_path.parent.mkdir(parents=True, exist_ok=True)

    if marketplace_path.exists():
        marketplace = json.loads(marketplace_path.read_text())
    else:
        marketplace = {
            "name": MARKETPLACE_NAME,
            "interface": {"displayName": "Local Plugins"},
            "plugins": [],
        }

    if marketplace.get("name") != MARKETPLACE_NAME:
        raise SystemExit(
            f"{marketplace_path} declares marketplace {marketplace.get('name')!r}; "
            f"expected {MARKETPLACE_NAME!r}"
        )

    plugins = marketplace.setdefault("plugins", [])
    if not isinstance(plugins, list):
        raise SystemExit(f"{marketplace_path} has a non-list 'plugins' field")

    entry = marketplace_entry(repo_root, home)
    plugins[:] = [
        plugin
        for plugin in plugins
        if not isinstance(plugin, dict) or plugin.get("name") != PLUGIN_NAME
    ]
    plugins.append(entry)
    marketplace_path.write_text(json.dumps(marketplace, indent=2) + "\n")
    return marketplace_path


def run_codex(codex: str, *args: str) -> None:
    subprocess.run([codex, *args], check=True)


def main() -> None:
    args = parse_args()
    repo_root = Path(__file__).resolve().parents[1]
    home = args.home.expanduser().resolve()

    manifest = repo_root / ".codex-plugin" / "plugin.json"
    if not manifest.is_file():
        raise SystemExit(f"missing plugin manifest: {manifest}")

    marketplace_path = update_marketplace(repo_root, home)
    run_codex(args.codex, "plugin", "marketplace", "add", str(home))
    run_codex(args.codex, "plugin", "add", f"{PLUGIN_NAME}@{MARKETPLACE_NAME}")

    print(f"Marketplace: {marketplace_path}")
    print(f"Installed: {PLUGIN_NAME}@{MARKETPLACE_NAME}")


if __name__ == "__main__":
    main()

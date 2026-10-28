"""設定の読み込み。リポジトリの .algr/config.toml に、個人の ~/.algr/config.toml を重ねる。

チームで共有するもの（アダプタ、社内固有のルール）はリポジトリの .algr/ に、
人によって違うもの（実行環境、通知先）は ~/.algr/ に置く。個人の設定がリポジトリの設定を上書きする。
"""

from __future__ import annotations

import importlib
import importlib.util
import os
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from algr.adapters import KINDS, Context
from algr.flow import HOOK_POINTS
from algr.model import Role

CONFIG_FILE = "config.toml"

DEFAULTS: dict[str, Any] = {
    "flow": {"max_parallel": 1, "max_rework": 2, "max_lost": 2},
    "adapters": {
        "task": {"use": "fake.tasks"},
        "trigger": {"use": "fake.inbox"},
        "executor": {"use": "fake.process"},
        "decisions": {"use": "fake.rules"},
        "merge": {"use": "fake.merge"},
        "notify": {"use": "stub"},
    },
    "hooks": {},
    "agents": {},  # 役ごとの設定（model、max_tokens など）。ジョブの input.agent として実行アダプタに渡す
}

BUILTIN_ADAPTERS = {
    "fake.tasks": "algr.adapters.fake:FileTasks",
    "fake.inbox": "algr.adapters.fake:InboxTrigger",
    "fake.process": "algr.adapters.fake:LocalProcess",
    "fake.rules": "algr.adapters.fake:RuleFile",
    "fake.merge": "algr.adapters.fake:InstantMerge",
    "stub": "algr.adapters.fake:StubNotifier",
    "k2down": "algr.adapters.k2down:K2down",
}


class ConfigError(Exception):
    pass


def find_root(start: Path) -> Path:
    """.algr/ を含む最も近い祖先ディレクトリ。見つからなければ start。"""
    start = start.resolve()
    for d in (start, *start.parents):
        if (d / ".algr").is_dir():
            return d
    return start


def home_dir() -> Path:
    return Path(os.environ.get("ALGR_HOME") or Path.home() / ".algr")


def merge(base: dict[str, Any], over: dict[str, Any]) -> dict[str, Any]:
    """over を base に深く重ねる。表は再帰的に、それ以外は over が勝つ。"""
    out = dict(base)
    for key, value in over.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = merge(out[key], value)
        else:
            out[key] = value
    return out


def _read(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"{path}: {e}") from e


@dataclass
class Config:
    ctx: Context
    data: dict[str, Any]
    sources: list[Path]

    @classmethod
    def load(cls, cwd: Path | None = None, home: Path | None = None) -> Config:
        root = find_root(cwd or Path.cwd())
        home = home or home_dir()
        repo_file, home_file = root / ".algr" / CONFIG_FILE, home / CONFIG_FILE
        given = merge(_read(repo_file), _read(home_file))
        data = merge({k: v for k, v in DEFAULTS.items() if k != "adapters"}, given)
        # アダプタは use ごと既定を使うか、まるごと設定に従うかのどちらか。別物のアダプタに既定の項目を混ぜない
        data["adapters"] = {
            kind: table if "use" in (table := given.get("adapters", {}).get(kind, {})) else {**default, **table}
            for kind, default in DEFAULTS["adapters"].items()
        }
        sources = [p for p in (repo_file, home_file) if p.exists()]
        return cls(Context(root=root, home=home), data, sources)

    def resolve(self, spec: str, builtins: dict[str, str]) -> Any:
        """'fake.tasks' のような組み込み名、'pkg.mod:Name'、'.algr/x.py:Name' を解決する。"""
        spec = builtins.get(spec, spec)
        target, sep, name = spec.partition(":")
        if not sep:
            raise ConfigError(f"expected 'module:Name' or 'path.py:Name', got {spec!r}")
        if target.endswith(".py"):
            path = self.ctx.path(target)
            if not path.exists():
                raise ConfigError(f"no such file: {path}")
            mod_spec = importlib.util.spec_from_file_location(f"algr_user_{path.stem}", path)
            assert mod_spec and mod_spec.loader
            module = importlib.util.module_from_spec(mod_spec)
            mod_spec.loader.exec_module(module)
        else:
            module = importlib.import_module(target)
        try:
            return getattr(module, name)
        except AttributeError as e:
            raise ConfigError(f"{target} has no {name}") from e

    def adapter(self, kind: str) -> Any:
        table = dict(self.data["adapters"][kind])
        cls = self.resolve(table.pop("use"), BUILTIN_ADAPTERS)
        try:
            adapter = cls(self.ctx, **table)
        except TypeError as e:
            raise ConfigError(f"adapters.{kind}: {e}") from e
        if not isinstance(adapter, KINDS[kind]):
            raise ConfigError(f"adapters.{kind}: {type(adapter).__name__} does not implement {KINDS[kind].__name__}")
        return adapter

    def hooks(self) -> dict[str, list[Any]]:
        """イベント名（または全イベントを受ける "*"）→ hook(event, engine) の並び。"""
        for event in self.data["hooks"]:
            if event not in HOOK_POINTS:
                raise ConfigError(f"hooks: unknown event {event!r} (one of {', '.join(HOOK_POINTS)})")
        return {event: [self.resolve(spec, {}) for spec in specs] for event, specs in self.data["hooks"].items()}

    @property
    def max_parallel(self) -> int:
        return int(self.data["flow"]["max_parallel"])

    def agents(self) -> dict[str, dict[str, Any]]:
        """役 → エージェントの設定。[agents.default] はすべての役に効き、役ごとの設定が上書きする。"""
        table = self.data["agents"]
        for role in table:
            if role != "default" and role not in {str(r) for r in Role}:
                raise ConfigError(f"agents: unknown role {role!r}")
        return {str(role): merge(table.get("default", {}), table.get(str(role), {})) for role in Role}

    @property
    def max_rework(self) -> int:
        return int(self.data["flow"]["max_rework"])

    @property
    def max_lost(self) -> int:
        return int(self.data["flow"]["max_lost"])

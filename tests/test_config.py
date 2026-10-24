import pytest

from algr.adapters import Context
from algr.adapters.fake import FileTasks, StubNotifier
from algr.config import Config, ConfigError, find_root


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_defaults_without_config(tmp_path, home):
    config = Config.load(tmp_path)
    assert config.sources == []
    assert isinstance(config.adapter("task"), FileTasks)
    assert isinstance(config.adapter("notify"), StubNotifier)


def test_root_is_found_from_subdirectory(tmp_path):
    (tmp_path / ".algr").mkdir()
    (tmp_path / "src" / "deep").mkdir(parents=True)
    assert find_root(tmp_path / "src" / "deep") == tmp_path.resolve()


def test_personal_config_overrides_repo(tmp_path, home):
    write(tmp_path / ".algr" / "config.toml", '[flow]\nmax_parallel = 3\n[adapters.notify]\nfile = "repo.jsonl"\n')
    write(home / "config.toml", '[adapters.notify]\nfile = "mine.jsonl"\n')
    config = Config.load(tmp_path)
    assert config.max_parallel == 3
    assert config.data["adapters"]["notify"] == {"use": "stub", "file": "mine.jsonl"}
    assert len(config.sources) == 2


def test_changing_use_drops_default_options(tmp_path, home):
    write(tmp_path / ".algr" / "config.toml", '[adapters.executor]\nuse = "fake.process"\ncommand = ["echo"]\n')
    assert Config.load(tmp_path).data["adapters"]["executor"] == {"use": "fake.process", "command": ["echo"]}


def test_adapter_from_repo_file(tmp_path, home):
    write(
        tmp_path / ".algr" / "adapters" / "slack.py",
        "class Slack:\n    def __init__(self, ctx, channel):\n        self.channel = channel\n"
        "    def notify(self, task, message):\n        pass\n",
    )
    write(tmp_path / ".algr" / "config.toml", '[adapters.notify]\nuse = ".algr/adapters/slack.py:Slack"\nchannel = "#dev"\n')
    assert Config.load(tmp_path).adapter("notify").channel == "#dev"


def test_adapter_must_implement_the_interface(tmp_path, home):
    write(tmp_path / ".algr" / "adapters" / "bad.py", "class Bad:\n    def __init__(self, ctx):\n        pass\n")
    write(tmp_path / ".algr" / "config.toml", '[adapters.notify]\nuse = ".algr/adapters/bad.py:Bad"\n')
    with pytest.raises(ConfigError, match="does not implement Notifier"):
        Config.load(tmp_path).adapter("notify")


@pytest.mark.parametrize(
    ("use", "message"),
    [("nope", "expected"), (".algr/missing.py:X", "no such file"), ("algr.agents:Missing", "has no Missing")],
)
def test_bad_specs(tmp_path, home, use, message):
    write(tmp_path / ".algr" / "config.toml", f'[adapters.notify]\nuse = "{use}"\n')
    with pytest.raises(ConfigError, match=message):
        Config.load(tmp_path).adapter("notify")


def test_unknown_adapter_option(tmp_path, home):
    write(tmp_path / ".algr" / "config.toml", '[adapters.notify]\ncolour = "red"\n')
    with pytest.raises(ConfigError, match="adapters.notify"):
        Config.load(tmp_path).adapter("notify")


def test_context_resolves_relative_paths(tmp_path):
    ctx = Context(root=tmp_path, home=tmp_path / "h")
    assert ctx.path("a/b") == tmp_path / "a" / "b"
    assert ctx.path("/abs").as_posix() == "/abs"

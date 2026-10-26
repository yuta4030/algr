"""algr: Algorithmic Development のフロー。ルールで判断を埋め、タスクをマージまで流す。"""

from importlib.metadata import PackageNotFoundError, version

# 版は pyproject.toml の 1 か所にだけ書く
try:
    __version__ = version("algr")
except PackageNotFoundError:  # 入れずにソースから動かしたとき
    __version__ = "0.0.0"

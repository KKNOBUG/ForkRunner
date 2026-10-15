"""校验并读取 ForkRunner 当前步骤的 HEAD/BODY 字段快照。"""

from __future__ import annotations

import json
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

# 与 applications/data_generation/constants.py 的 MAX_REQUEST_FIELDS 保持一致；
# skill 包保持自洽，不 import 后端模块，修改时需手动同步。
MAX_REQUEST_FIELDS = 10_000
MAX_FIELD_PATH_LENGTH = 1_000


class RequestSnapshotError(ValueError):
    """请求快照不满足智能生成的分区字段契约。"""


def normalize_request_snapshot(raw: Any) -> dict[str, dict[str, Any]]:
    """校验并规范化HEAD/BODY快照，供任务记录和JSON文件共同使用。

    :param raw: 待校验的分区映射；字段路径允许前后空格，字段值必须是标量。
    :return: 保留字段顺序且路径已去空格的新映射，HEAD可为空、BODY非空。
    :raises RequestSnapshotError: 分区、字段路径、值或字段数量不符合契约。
    """

    if not isinstance(raw, Mapping) or set(raw) != {"HEAD", "BODY"}:
        raise RequestSnapshotError("请求快照必须包含HEAD和BODY分区")
    if (not isinstance(raw["HEAD"], Mapping)
            or not isinstance(raw["BODY"], Mapping) or not raw["BODY"]):
        raise RequestSnapshotError("请求快照分区不合法或BODY为空")
    if len(raw["HEAD"]) + len(raw["BODY"]) > MAX_REQUEST_FIELDS:
        raise RequestSnapshotError(f"请求字段数量不能超过{MAX_REQUEST_FIELDS}个")

    snapshot: dict[str, dict[str, Any]] = {"HEAD": {}, "BODY": {}}
    for section in ("HEAD", "BODY"):
        for raw_path, value in raw[section].items():
            path_text = raw_path.strip() if isinstance(raw_path, str) else ""
            if not path_text or len(path_text) > MAX_FIELD_PATH_LENGTH:
                raise RequestSnapshotError("请求快照包含空路径或超长字段路径")
            if value is not None and not isinstance(value, (str, int, float, bool)):
                raise RequestSnapshotError(
                    f"{section}字段[{path_text}]不是已展开的标量值"
                )
            if path_text in snapshot[section]:
                raise RequestSnapshotError("请求快照包含重复字段路径")
            snapshot[section][path_text] = value
    return snapshot


def load_request_snapshot(input_path: str) -> dict[str, dict[str, Any]]:
    """从UTF-8 JSON文件读取任务快照并按统一HEAD/BODY规则规范化。

    :param input_path: 任务目录中request_snapshot.json的路径。
    :return: 保留字段顺序的HEAD/BODY映射；HEAD可为空，BODY必须非空。
    :raises RequestSnapshotError: 文件、分区、字段路径或标量值不符合契约。
    """
    path = Path(input_path)
    if not path.is_file():
        raise RequestSnapshotError(f"请求快照文件不存在: {path}")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RequestSnapshotError("请求快照文件不是有效的UTF-8 JSON") from exc
    # 已停用：文件读取器内联的分区、路径和标量校验由统一规范化函数执行。
    # 该函数同时处理任务记录快照，避免两处规则逐渐偏离。
    return normalize_request_snapshot(raw)


def main() -> None:
    """从命令行读取快照并输出分区JSON；输入无效时以非零状态退出。"""
    if len(sys.argv) != 2:
        print(
            "用法: python request_snapshot_reader.py <request_snapshot.json>",
            file=sys.stderr,
        )
        raise SystemExit(2)
    try:
        snapshot = load_request_snapshot(sys.argv[1])
    except RequestSnapshotError as exc:
        print(f"错误: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    print(json.dumps(snapshot, ensure_ascii=False, separators=(",", ":")))


if __name__ == "__main__":
    main()

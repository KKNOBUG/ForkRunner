"""读取智能生成场景规则工作簿。"""

from __future__ import annotations

import json
import sys
from pathlib import Path
import openpyxl
from openpyxl.utils.exceptions import InvalidFileException

# 与 applications/data_generation/constants.py 的 MAX_GENERATED_SCENARIOS 保持一致；
# skill 包保持自洽，不 import 后端模块，修改时需手动同步。
MAX_INPUT_SCENARIOS = 10_000


class ScenarioInputError(ValueError):
    """场景规则工作簿无法读取。"""


def load_scenarios(input_path: str) -> list[dict]:
    """逐行读取场景工作簿第二行起的前三列，并限制有效场景数量。

    :param input_path: 用户上传的 XLSX 场景规则文件路径。
    :return: 保留分组行的场景列表；仅跳过序号和场景都为空的行。
    :raises ScenarioInputError: 文件无效、没有有效场景或场景数量超限。

    按列位置解析，保留原有表头兼容行为；只读模式避免装载整本工作簿。
    """

    path = Path(input_path)
    if not path.is_file():
        raise ScenarioInputError(f"场景规则文件不存在: {path}")

    try:
        workbook = openpyxl.load_workbook(path, read_only=True)
    except (OSError, InvalidFileException, KeyError, ValueError) as exc:
        raise ScenarioInputError("场景规则文件不是有效的.xlsx工作簿") from exc

    try:
        sheet = workbook.active
        scenarios = []
        for row in sheet.iter_rows(min_row=2, max_col=3, values_only=True):
            if row[0] is None and row[1] is None:
                continue
            if len(scenarios) >= MAX_INPUT_SCENARIOS:
                raise ScenarioInputError(
                    f"场景数量不能超过{MAX_INPUT_SCENARIOS}条"
                )
            scenarios.append({
                "序号": row[0],
                "场景": str(row[1]).strip() if row[1] else "",
                "场景类型": (
                    str(row[2]).strip() if len(row) > 2 and row[2] else ""
                ),
            })
        if not scenarios:
            raise ScenarioInputError("场景规则工作簿没有有效场景")
        return scenarios
    finally:
        workbook.close()


def main() -> None:
    if len(sys.argv) != 2:
        print("用法: python input_reader.py <input_scenarios.xlsx>", file=sys.stderr)
        raise SystemExit(2)
    try:
        scenarios = load_scenarios(sys.argv[1])
    except ScenarioInputError as exc:
        print(f"错误: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    print(json.dumps(scenarios, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()

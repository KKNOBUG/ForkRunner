"""依据 HEAD/BODY 快照和分析结果生成 ForkRunner 数据生成工作簿。"""

from __future__ import annotations

import csv
import json
import os
import tempfile
from pathlib import Path
from typing import Any, Mapping

from openpyxl import Workbook
from openpyxl.cell.cell import Cell
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from .request_snapshot_reader import load_request_snapshot

# 与 applications/data_generation/constants.py 的 MAX_GENERATED_SCENARIOS 保持一致；
# skill 包保持自洽，不 import 后端模块，修改时需手动同步。
MAX_OUTPUT_ROWS = 10_000
# 与 applications/data_generation/constants.py 的 MAX_SCENE_NAME_LENGTH 保持一致。
MAX_SCENE_NAME_LENGTH = 255
MAX_INTERMEDIATE_CSV_BYTES = 64 * 1024 * 1024


class OutputDataError(ValueError):
    """分析结果无法转换为安全、完整的 ForkRunner 工作簿。"""


def _text_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (str, int, float)):
        return str(value)
    raise OutputDataError("字段值只允许字符串、数字、布尔值或null")


def _write_text(cell: Cell, value: Any) -> None:
    cell.value = _text_value(value)
    # 生成值可能以“=”开头，必须强制作为文本写入，避免公式注入。
    cell.data_type = "s"


def load_analysis_result(data_file: str) -> list[dict[str, Any]]:
    path = Path(data_file)
    if not path.is_file():
        raise OutputDataError(f"分析结果文件不存在: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise OutputDataError("分析结果不是有效的UTF-8 JSON") from exc
    if not isinstance(data, list) or not data:
        raise OutputDataError("分析结果必须是非空JSON数组")
    if len(data) > MAX_OUTPUT_ROWS:
        raise OutputDataError(f"输出场景数量不能超过{MAX_OUTPUT_ROWS}条")
    if not all(isinstance(item, dict) for item in data):
        raise OutputDataError("分析结果数组中的每一项都必须是对象")
    return data


def normalize_results(
        data: list[dict[str, Any]],
        snapshot: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """按请求快照合并每条分析结果的字段改动并规范化分区字段。

    :param data: Agent写出的场景分析数组，名称原样保留，字段只包含本行改动。
    :param snapshot: HEAD/BODY分区的有序字段基准，用于补齐未改动字段。
    :return: 可直接写入工作簿的场景、分区值及高亮字段。
    :raises OutputDataError: 场景或受约束字段缺失、名称超过255字符、改动字段越界
        或数据类型不合法。
    """

    seen_names: set[str] = set()
    normalized: list[dict[str, Any]] = []

    for index, item in enumerate(data, start=1):
        scene_name = item.get("场景描述")
        if not isinstance(scene_name, str) or not scene_name.strip():
            raise OutputDataError(f"第{index}条分析结果缺少场景描述")
        if len(scene_name) > MAX_SCENE_NAME_LENGTH:
            raise OutputDataError(
                f"第{index}条分析结果的场景描述长度超限: "
                f"{len(scene_name)}字符 > {MAX_SCENE_NAME_LENGTH}字符上限"
            )
        if scene_name in seen_names:
            raise OutputDataError(f"第{index}条分析结果的场景描述重复")

        field_changes = item.get("字段改动")
        if not isinstance(field_changes, dict) or set(field_changes) != {"HEAD", "BODY"}:
            raise OutputDataError(f"第{index}条分析结果的字段改动必须包含HEAD和BODY")
        constrained = item.get("受约束字段")
        if not isinstance(constrained, dict) or set(constrained) != {"HEAD", "BODY"}:
            raise OutputDataError(f"第{index}条分析结果的受约束字段必须按HEAD/BODY分区")

        values: dict[str, dict[str, str]] = {}
        highlighted: dict[str, set[str]] = {}
        for section in ("HEAD", "BODY"):
            section_changes = field_changes[section]
            section_constrained = constrained[section]
            if not isinstance(section_changes, dict):
                raise OutputDataError(f"第{index}条分析结果的{section}字段改动必须是对象")
            invalid_paths = set(section_changes) - set(snapshot[section])
            if invalid_paths:
                raise OutputDataError(
                    f"第{index}条分析结果的{section}字段改动包含快照外字段: "
                    f"{len(invalid_paths)}个路径越界"
                )
            if not isinstance(section_constrained, list) or any(
                    not isinstance(path, str) for path in section_constrained
            ):
                raise OutputDataError(f"第{index}条分析结果的{section}受约束字段必须是字符串数组")

            merged = dict(snapshot[section])
            for path, change in section_changes.items():
                merged[path] = change
            values[section] = {
                path: _text_value(merged[path]) for path in snapshot[section]
            }
            highlighted[section] = set(section_constrained) & set(snapshot[section])
        seen_names.add(scene_name)
        normalized.append({
            "scene_name": scene_name,
            "scenario_data": values,
            "constrained_fields": highlighted,
        })
    return normalized


def build_workbook(
        results: list[dict[str, Any]],
        snapshot: Mapping[str, Mapping[str, Any]],
) -> Workbook:
    """按HEAD/BODY分区把已校验场景写为数据编辑兼容工作簿。

    工作簿只固定首行与场景名称列，HEAD/BODY字段列可随水平滚动移动。

    :param results: 已规范化的场景数据及分区高亮字段。
    :param snapshot: 决定字段列顺序的请求快照。
    :return: 尚未保存、由调用方负责关闭的工作簿。
    """

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "测试数据"
    sheet.freeze_panes = "B2"

    header_fill = PatternFill("solid", fgColor="2F75B5")
    highlight_fill = PatternFill("solid", fgColor="FFFF00")
    thin = Side(style="thin", color="A6A6A6")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    head_paths = list(snapshot["HEAD"])
    body_paths = list(snapshot["BODY"])
    headers = ["", "Head", *head_paths, "Body", *body_paths]
    field_columns = {
        **{index: ("HEAD", path) for index, path in enumerate(head_paths, start=3)},
        **{index: ("BODY", path) for index, path in enumerate(body_paths, start=len(head_paths) + 4)},
    }

    for column, value in enumerate(headers, start=1):
        cell = sheet.cell(row=1, column=column)
        _write_text(cell, value)
        cell.fill = header_fill
        cell.font = Font(color="FFFFFF", bold=True)
        cell.alignment = Alignment(
            horizontal="center",
            vertical="center",
            wrap_text=True,
        )
        cell.border = border

    for row, result in enumerate(results, start=2):
        values = [
            result["scene_name"],
            "",
            *(result["scenario_data"]["HEAD"][path] for path in head_paths),
            "",
            *(result["scenario_data"]["BODY"][path] for path in body_paths),
        ]
        for column, value in enumerate(values, start=1):
            cell = sheet.cell(row=row, column=column)
            _write_text(cell, value)
            cell.alignment = Alignment(vertical="center", wrap_text=True)
            cell.border = border
            section_path = field_columns.get(column)
            if section_path and section_path[1] in result["constrained_fields"][section_path[0]]:
                cell.fill = highlight_fill

    sheet.row_dimensions[1].height = 32
    sheet.column_dimensions["A"].width = min(
        80,
        max(24, max(len(result["scene_name"]) for result in results) + 2),
    )
    sheet.column_dimensions["B"].width = 12
    sheet.column_dimensions[get_column_letter(len(head_paths) + 3)].width = 12
    for column in field_columns:
        sheet.column_dimensions[get_column_letter(column)].width = 24
    sheet.auto_filter.ref = (
        f"A1:{get_column_letter(len(headers))}{len(results) + 1}"
    )
    return workbook


def append_analysis_batch(
        csv_path: str | Path,
        snapshot: Mapping[str, Mapping[str, Any]],
        analysis: list[dict[str, Any]],
        expected_indexes: set[int],
        seen_names: set[str],
        scenario_types: Mapping[int, str] | None = None,
) -> int:
    """校验一个批次，并将合并快照后的完整场景逐行追加到临时CSV。

    :param csv_path: 同一任务专用的中间CSV路径，首次调用时创建表头。
    :param snapshot: 有序HEAD/BODY快照，用于合并未改动字段。
    :param analysis: Agent本批次返回的分析数组，可拆成多行。
    :param expected_indexes: 本批次输入场景的全任务索引集合。
    :param seen_names: 已写入CSV的场景名集合；成功追加后原位更新。
    :param scenario_types: 按全任务索引映射的原始场景类型，用于确定responsemark。
    :return: 本批次追加的CSV数据行数。
    :raises OutputDataError: 来源覆盖不足、SDK必填字段缺失、字段或名称
        无效、输出超限。
    """

    if (not analysis or len(analysis) > MAX_OUTPUT_ROWS
            or any(not isinstance(item, dict) for item in analysis)):
        raise OutputDataError("分析结果必须包含有效数量的场景")
    if any("受约束字段" not in item for item in analysis):
        raise OutputDataError("分析结果缺少受约束字段")
    actual_indexes = [item.get("source_index") for item in analysis]
    if any(type(index) is not int or index not in expected_indexes for index in actual_indexes):
        raise OutputDataError("分析结果的source_index不属于当前批次")
    if set(actual_indexes) != expected_indexes:
        raise OutputDataError("分析结果未覆盖当前批次的全部输入场景")
    normalized = normalize_results(analysis, snapshot)
    names = {item["scene_name"] for item in normalized}
    if names & seen_names:
        raise OutputDataError("分析结果的场景描述与先前批次重复")

    responsemark_paths = {
        section: [path for path in snapshot[section]
                  if path.rsplit(".", 1)[-1] == "responsemark"]
        for section in ("HEAD", "BODY")
    }
    for source_index, result in zip(actual_indexes, normalized):
        responsemark = {
            "正流程": "S", "异常流程": "F",
        }.get(scenario_types.get(source_index) if scenario_types is not None else None)
        if responsemark is not None:
            for section, paths in responsemark_paths.items():
                for path in paths:
                    result["scenario_data"][section][path] = responsemark

    path = Path(csv_path)
    headers = [
        "source_index", "scene_name", "constrained_HEAD", "constrained_BODY",
        *(f"HEAD:{field}" for field in snapshot["HEAD"]),
        *(f"BODY:{field}" for field in snapshot["BODY"]),
    ]
    with path.open("a", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        if stream.tell() == 0:
            writer.writerow(headers)
        for source_index, result in zip(actual_indexes, normalized):
            writer.writerow([
                source_index,
                result["scene_name"],
                json.dumps(sorted(result["constrained_fields"]["HEAD"]), ensure_ascii=False),
                json.dumps(sorted(result["constrained_fields"]["BODY"]), ensure_ascii=False),
                *(result["scenario_data"]["HEAD"][field] for field in snapshot["HEAD"]),
                *(result["scenario_data"]["BODY"][field] for field in snapshot["BODY"]),
            ])
        stream.flush()
        os.fsync(stream.fileno())
    if path.stat().st_size > MAX_INTERMEDIATE_CSV_BYTES:
        raise OutputDataError("中间CSV文件大小超出限制")
    seen_names.update(names)
    return len(normalized)


def generate_output_from_csv(
        request_snapshot_path: str,
        output_path: str,
        csv_path: str,
) -> str:
    """读取并校验完整中间CSV，原子生成固定格式的XLSX。

    :param request_snapshot_path: HEAD/BODY快照路径，与CSV位于同一任务目录。
    :param output_path: 固定文件名的最终XLSX路径。
    :param csv_path: 所有批次写完后的内部CSV路径。
    :return: 已生成工作簿的绝对路径。
    :raises OutputDataError: CSV列、数据行、来源或元数据不符合契约。
    """

    snapshot = load_request_snapshot(request_snapshot_path)
    csv_file = Path(csv_path)
    output = Path(output_path)
    directory = Path(request_snapshot_path).resolve().parent
    if (csv_file.resolve().parent != directory or output.resolve().parent != directory
            or output.name != "output_scenarios_data.xlsx" or csv_file.is_symlink()):
        raise OutputDataError("CSV、快照和输出文件必须位于同一任务目录")
    if not csv_file.is_file() or csv_file.stat().st_size > MAX_INTERMEDIATE_CSV_BYTES:
        raise OutputDataError("中间CSV不存在或大小超出限制")
    head_paths = list(snapshot["HEAD"])
    body_paths = list(snapshot["BODY"])
    expected_headers = [
        "source_index", "scene_name", "constrained_HEAD", "constrained_BODY",
        *(f"HEAD:{field}" for field in head_paths),
        *(f"BODY:{field}" for field in body_paths),
    ]
    results: list[dict[str, Any]] = []
    seen_names: set[str] = set()
    csv.field_size_limit(MAX_INTERMEDIATE_CSV_BYTES)
    with csv_file.open("r", encoding="utf-8", newline="") as stream:
        reader = csv.reader(stream, strict=True)
        try:
            if next(reader, None) != expected_headers:
                raise OutputDataError("中间CSV字段与请求快照不一致")
            for row in reader:
                if len(results) >= MAX_OUTPUT_ROWS or len(row) != len(expected_headers):
                    raise OutputDataError("中间CSV行数或列数不合法")
                head_constrained = json.loads(row[2])
                body_constrained = json.loads(row[3])
                if (not row[0].isdigit() or not isinstance(head_constrained, list)
                        or not isinstance(body_constrained, list)
                        or any(type(item) is not str for item in head_constrained + body_constrained)
                        or set(head_constrained) - set(head_paths)
                        or set(body_constrained) - set(body_paths)):
                    raise OutputDataError("中间CSV来源或受约束字段不合法")
                if (not row[1].strip() or len(row[1]) > MAX_SCENE_NAME_LENGTH
                        or row[1] in seen_names):
                    raise OutputDataError("中间CSV场景描述不合法或重复")
                seen_names.add(row[1])
                results.append({
                    "scene_name": row[1],
                    "scenario_data": {
                        "HEAD": dict(zip(head_paths, row[4:4 + len(head_paths)])),
                        "BODY": dict(zip(body_paths, row[4 + len(head_paths):])),
                    },
                    "constrained_fields": {
                        "HEAD": set(head_constrained), "BODY": set(body_constrained),
                    },
                })
        except (csv.Error, json.JSONDecodeError, UnicodeError) as exc:
            raise OutputDataError("中间CSV内容不合法") from exc
    if not results:
        raise OutputDataError("中间CSV没有测试场景")
    workbook = build_workbook(results, snapshot)
    temporary_path: str | None = None
    try:
        descriptor, temporary_path = tempfile.mkstemp(
            prefix=".intelligent-data-", suffix=".xlsx", dir=output.parent,
        )
        os.close(descriptor)
        workbook.save(temporary_path)
        os.replace(temporary_path, output)
        temporary_path = None
    finally:
        workbook.close()
        if temporary_path and os.path.isfile(temporary_path):
            os.remove(temporary_path)
    return str(output.resolve())

# -*- coding: utf-8 -*-

'''
    读取xlsx类型的接口文档的第一个sheet，并把表格中的字段转换为统一的python字典结构
'''

from __future__ import annotations

import io
import re
from typing import Dict, Iterator, List, Literal, Optional, Tuple, TypedDict
from xml.etree.ElementTree import ParseError
from zipfile import BadZipFile

from openpyxl import load_workbook
from openpyxl.utils.exceptions import InvalidFileException
from openpyxl.worksheet.worksheet import Worksheet

#接口文档格式
InterfaceStyle = Literal["esb", "project"]

#最大文件大小
MAX_DOCUMENT_SIZE = 16 * 1024 * 1024
#最大支持行数和列数
MAX_DOCUMENT_ROWS = 20_000
MAX_DOCUMENT_COLUMNS = 256

#esb表头，按顺序出现
_ESB_HEADERS = ("英文名称", "中文名称", "数据格式", "长度", "是否必输", "枚举值")
#项目接口文档表头
_PROJECT_HEADERS = ("字段名", "必填", "数标", "格式", "字段长度", "备注")
#表头别名
_ESB_HEADER_ALIASES = {
    "英文名称": ("英文名称",),
    "中文名称": ("中文名称",),
    "数据格式": ("数据类型", "格式", "数据格式"),
    "长度": ("长度", "字段长度"),
    "是否必输": ("是否必输", "必输", "必填"),
    "枚举值": ("枚举值",),
}
_PROJECT_HEADER_ALIASES = {
    "字段名": ("字段名",),
    "必填": ("必填", "必输", "是否必输", "是否必填"),
    "数标": ("数标",),
    "格式": ("数据类型", "格式", "数据格式"),
    "字段长度": ("字段长度", "长度"),
    "备注": ("备注",),
}
_STYLE_ALIASES = {
    "esb": "esb",
    "esb接口": "esb",
    "project": "project",
    "项目接口": "project",
}
#必填值和非必填
_REQUIRED_TRUE_VALUES = {"1", "true", "m", "y", "yes", "是", "必填", "必输"}
_REQUIRED_FALSE_VALUES = {"0", "false", "n", "no", "否", "非必填", "非必输"}
#数组类型
_ARRAY_TYPE_VALUES = {"array", "Array", "list", "List"}


class InterfaceDocumentParseError(ValueError):
    """接口文档结构或内容不符合约定。"""


class ParsedInterfaceField(TypedDict):
    """
        单个字段输出结构
        TypedDict：用于描述字典应该有哪些键
    """
    field_name: str
    field_chinese_name: Optional[str]
    required: Optional[bool]
    required_text: Optional[str]
    length: Optional[str]
    total_length: Optional[int]
    integer_length: Optional[int]
    decimal_length: Optional[int]
    length_error: Optional[str]
    enum_values: Optional[str]          #枚举文本：esb直接读取枚举值，项目接口文档固定为None
    remark: Optional[str]               #字段备注：项目文档读取备注列，esb当前固定为None
    array_name: Optional[str]           #字段所属的数组名称，普通字段位None
    duplicate_in_context: bool          #字段是否在同一个上下文中重复
    source_row: int                     #字段在原excel中的行号


class ParsedInterfaceDocument(TypedDict):
    """
        整份文档输出结构
    """
    interface_style: InterfaceStyle
    sheet_name: str
    header_row: int
    fields: List[ParsedInterfaceField]


class _ArrayContext(TypedDict):
    name: str
    start_row: int


def _cell_text(value: object) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    text = str(value).replace("\xa0", " ").strip()
    return text or None


def _header_text(value: object) -> str:
    return re.sub(r"\s+", "", _cell_text(value) or "")


def _normalize_required(value: Optional[str]) -> Optional[bool]:
    if value is None:
        return None
    normalized = value.casefold().replace(" ", "")
    if normalized in _REQUIRED_TRUE_VALUES:
        return True
    if normalized in _REQUIRED_FALSE_VALUES:
        return False
    return None


def _normalize_length(
        value: Optional[str],
) -> Tuple[Optional[str], Optional[int], Optional[int], Optional[int]]:
    if value is None:
        return None, None, None, None
    normalized = re.sub(r"\s*[,，]\s*", ",", value.strip())
    decimal_match = re.fullmatch(r"(\d+),(\d+)", normalized)
    if decimal_match:
        total_length = int(decimal_match.group(1))
        decimal_length = int(decimal_match.group(2))
        return normalized, total_length, total_length - decimal_length, decimal_length
    integer_match = re.fullmatch(r"\d+", normalized)
    if integer_match:
        total_length = int(normalized)
        return normalized, total_length, total_length, None
    return normalized, None, None, None


def _validate_workbook_columns(sheet: Worksheet) -> None:
    if sheet.max_column > MAX_DOCUMENT_COLUMNS:
        raise InterfaceDocumentParseError(f"接口文档列数不能超过{MAX_DOCUMENT_COLUMNS}列")


def _iter_content_rows(
        sheet: Worksheet,
        *,
        min_row: int = 1,
        stop_on_empty: bool = False,
) -> Iterator[Tuple[int, Tuple[object, ...]]]:
    """
        只统计实际有值的行；字段区可在首个空行处停止。
    """
    content_row_count = 0
    for row_no, row in enumerate(
            sheet.iter_rows(min_row=min_row, values_only=True),
            start=min_row,
    ):
        if not any(_cell_text(value) is not None for value in row):
            if stop_on_empty:
                break
            continue
        content_row_count += 1
        if content_row_count > MAX_DOCUMENT_ROWS:
            raise InterfaceDocumentParseError(
                f"接口文档行数不能超过{MAX_DOCUMENT_ROWS}行"
            )
        yield row_no, row


def _find_ordered_header_group(
        sheet: Worksheet,
        expected_headers: Tuple[str, ...],
        header_aliases: Dict[str, Tuple[str, ...]],
) -> Tuple[int, Dict[str, int]]:
    """
        定位首个顺序一致的表头组；目标列之间允许夹杂无关列。
    """
    anchor_seen = False
    for row_no, row in _iter_content_rows(sheet):
        headers = [_header_text(value) for value in row]
        anchor_seen = anchor_seen or any(
            header in header_aliases[expected_headers[0]] for header in headers
        )
        positions: Dict[str, int] = {}
        next_column = 0
        for expected_header in expected_headers:
            column_no = next(
                (
                    index
                    for index in range(next_column, len(headers))
                    if headers[index] in header_aliases[expected_header]
                ),
                None,
            )
            if column_no is None:
                break
            positions[expected_header] = column_no
            next_column = column_no + 1
        if len(positions) == len(expected_headers):
            return row_no, positions

    if anchor_seen:
        raise InterfaceDocumentParseError(
            f"接口文档字段行须按顺序包含: {', '.join(expected_headers)}"
        )
    raise InterfaceDocumentParseError(f"未找到接口文档字段行标识「{expected_headers[0]}」")


def _row_values(row: Tuple[object, ...], positions: Dict[str, int]) -> Dict[str, Optional[str]]:
    return {
        header: _cell_text(row[column_no]) if column_no < len(row) else None
        for header, column_no in positions.items()
    }


def _iter_field_rows(
        sheet: Worksheet,
        *,
        header_row: int,
        positions: Dict[str, int],
        field_name_header: str,
        chinese_name_header: str,
) -> Iterator[Tuple[int, Dict[str, Optional[str]]]]:
    """从首个有效字段开始读取，字段区开始后遇到空行或“输出”即停止。"""
    data_started = False
    previous_row_no: Optional[int] = None
    for row_no, row in _iter_content_rows(sheet, min_row=header_row + 1):
        if data_started and previous_row_no is not None and row_no > previous_row_no + 1:
            break

        values = _row_values(row, positions)
        if any(value == "输出" for value in values.values()):
            break

        if not data_started:
            field_name = values[field_name_header]
            chinese_name = values[chinese_name_header]
            if not (
                    field_name
                    and chinese_name
                    and re.fullmatch(r"[A-Za-z]+", field_name)
            ):
                continue
            data_started = True

        previous_row_no = row_no
        yield row_no, values


def _toggle_array_context(
        active_array: Optional[_ArrayContext],
        *,
        array_name: Optional[str],
        row_no: int,
        name_label: str,
) -> Optional[_ArrayContext]:
    if not array_name:
        raise InterfaceDocumentParseError(f"第{row_no}行数组标记缺少{name_label}")
    if active_array is None:
        return {"name": array_name, "start_row": row_no}
    if array_name != active_array["name"]:
        raise InterfaceDocumentParseError(
            f"第{row_no}行数组结束标记{name_label}「{array_name}」与"
            f"第{active_array['start_row']}行数组开始标记「{active_array['name']}」不一致"
        )
    return None


def _build_field(
        *,
        field_name: str,
        field_chinese_name: Optional[str],
        required_text: Optional[str],
        length_text: Optional[str],
        enum_values: Optional[str],
        remark: Optional[str],
        array_name: Optional[str],
        source_row: int,
) -> ParsedInterfaceField:
    length, total_length, integer_length, decimal_length = _normalize_length(length_text)
    length_error = None
    if length and total_length is None:
        length_error = f"长度「{length}」格式不合法，请检查"
    if (
            total_length is not None
            and decimal_length is not None
            and decimal_length >= total_length
    ):
        integer_length = None
        length_error = (
            f"小数长度{decimal_length}必须小于总体长度{total_length}，请检查"
        )
    return {
        "field_name": field_name,
        "field_chinese_name": field_chinese_name,
        "required": _normalize_required(required_text),
        "required_text": required_text,
        "length": length,
        "total_length": total_length,
        "integer_length": integer_length,
        "decimal_length": decimal_length,
        "length_error": length_error,
        "enum_values": enum_values,
        "remark": remark,
        "array_name": array_name,
        "duplicate_in_context": False,
        "source_row": source_row,
    }


def _parse_esb_sheet(sheet: Worksheet) -> ParsedInterfaceDocument:
    header_row, positions = _find_ordered_header_group(
        sheet,
        _ESB_HEADERS,
        _ESB_HEADER_ALIASES,
    )
    fields: List[ParsedInterfaceField] = []
    field_indexes: Dict[Tuple[Optional[str], str], int] = {}
    active_array: Optional[_ArrayContext] = None

    for row_no, values in _iter_field_rows(
            sheet,
            header_row=header_row,
            positions=positions,
            field_name_header="英文名称",
            chinese_name_header="中文名称",
    ):
        if values["数据格式"] in _ARRAY_TYPE_VALUES:
            active_array = _toggle_array_context(
                active_array,
                array_name=values["英文名称"],
                row_no=row_no,
                name_label="英文名称",
            )
            continue

        field_name = values["英文名称"]
        if not field_name:
            continue
        array_name = active_array["name"] if active_array else None
        identity = (array_name, field_name)
        existing_index = field_indexes.get(identity)
        if existing_index is not None:
            fields[existing_index]["duplicate_in_context"] = True
            continue
        fields.append(_build_field(
            field_name=field_name,
            field_chinese_name=values["中文名称"],
            required_text=values["是否必输"],
            length_text=values["长度"],
            enum_values=values["枚举值"],
            remark=None,
            array_name=array_name,
            source_row=row_no,
        ))
        field_indexes[identity] = len(fields) - 1

    if active_array is not None:
        raise InterfaceDocumentParseError(f"第{active_array['start_row']}行数组标记缺少结束行")
    return {
        "interface_style": "esb",
        "sheet_name": sheet.title,
        "header_row": header_row,
        "fields": fields,
    }


def _parse_project_sheet(sheet: Worksheet) -> ParsedInterfaceDocument:
    header_row, positions = _find_ordered_header_group(
        sheet,
        _PROJECT_HEADERS,
        _PROJECT_HEADER_ALIASES,
    )
    fields: List[ParsedInterfaceField] = []
    field_indexes: Dict[Tuple[Optional[str], str], int] = {}
    active_array: Optional[_ArrayContext] = None

    for row_no, values in _iter_field_rows(
            sheet,
            header_row=header_row,
            positions=positions,
            field_name_header="字段名",
            chinese_name_header="数标",
    ):
        if values["格式"] in _ARRAY_TYPE_VALUES:
            active_array = _toggle_array_context(
                active_array,
                array_name=values["字段名"],
                row_no=row_no,
                name_label="字段名",
            )
            continue

        field_name = values["字段名"]
        if not field_name:
            continue
        array_name = active_array["name"] if active_array else None
        identity = (array_name, field_name)
        existing_index = field_indexes.get(identity)
        if existing_index is not None:
            fields[existing_index]["duplicate_in_context"] = True
            continue
        fields.append(_build_field(
            field_name=field_name,
            field_chinese_name=values["数标"],
            required_text=values["必填"],
            length_text=values["字段长度"],
            # 项目接口的枚举值后续由AI从备注提取，解析阶段只保留原文。
            enum_values=None,
            remark=values["备注"],
            array_name=array_name,
            source_row=row_no,
        ))
        field_indexes[identity] = len(fields) - 1

    if active_array is not None:
        raise InterfaceDocumentParseError(f"第{active_array['start_row']}行数组标记缺少结束行")
    return {
        "interface_style": "project",
        "sheet_name": sheet.title,
        "header_row": header_row,
        "fields": fields,
    }


def _parse_workbook(content: bytes, interface_style: InterfaceStyle) -> ParsedInterfaceDocument:
    if not content:
        raise InterfaceDocumentParseError("接口文档内容为空")
    if len(content) > MAX_DOCUMENT_SIZE:
        raise InterfaceDocumentParseError(f"接口文档大小不能超过{MAX_DOCUMENT_SIZE // 1024 // 1024}MB")
    try:
        workbook = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    except (BadZipFile, InvalidFileException, OSError, ValueError, KeyError, ParseError) as exc:
        raise InterfaceDocumentParseError("接口文档不是有效的xlsx文件") from exc

    try:
        if not workbook.sheetnames:
            raise InterfaceDocumentParseError("接口文档不包含工作表")
        sheet = workbook[workbook.sheetnames[0]]
        _validate_workbook_columns(sheet)
        if interface_style == "esb":
            return _parse_esb_sheet(sheet)
        return _parse_project_sheet(sheet)
    finally:
        workbook.close()


def parse_esb_interface_document(content: bytes) -> ParsedInterfaceDocument:
    """
        解析ESB接口文档的第一个工作表并返回统一字段结构。
    """
    return _parse_workbook(content, "esb")


def parse_project_interface_document(content: bytes) -> ParsedInterfaceDocument:
    """
        解析项目接口文档的第一个工作表并返回统一字段结构。
    """
    return _parse_workbook(content, "project")


def parse_interface_document(content: bytes, interface_style: str) -> ParsedInterfaceDocument:
    """
        按接口样式分发解析，支持前端英文值及中文显示值。
    """
    style = _STYLE_ALIASES.get((interface_style or "").strip().casefold())
    if style is None:
        raise InterfaceDocumentParseError("接口样式仅支持esb或project")
    return _parse_workbook(content, style)

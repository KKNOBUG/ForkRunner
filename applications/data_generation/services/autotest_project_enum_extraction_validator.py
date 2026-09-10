# -*- coding: utf-8 -*-

"""
    项目接口枚举结果校验：使用原始备注验证AI结果。
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Mapping, Sequence, Tuple
from pydantic import ValidationError

from applications.data_generation.schemas.autotest_project_enum_extraction_schema import (
    MAX_ENUM_NORMALIZED_TEXT_LENGTH,
    ProjectEnumExtractionCandidate,
    ProjectEnumExtractionFieldInput,
    ProjectEnumExtractionItem,
)
from configure import LOGGER

_FORWARD_PAIR_HEADER_RE = re.compile(
    r"(?<![A-Za-z0-9])([A-Za-z0-9]+)[ \t]*[:：-]"
)
_REVERSE_PAIR_HEADER_RE = re.compile(
    r"[:：-][ \t]*([A-Za-z0-9]+)(?![A-Za-z0-9])"
)
_DIRECT_PAIR_HEADER_RE = re.compile(
    r"(?:^|(?<=[;；,， ]))([A-Za-z0-9]+) *(?=[^\x00-\x7F；，。：])"
)


def _log_structure_error(rule: str, raw_item: Any) -> None:
    LOGGER.warning(
        "AI枚举返回结构校验失败: "
        f"rule={rule!r}, "
        f"raw_item={json.dumps(raw_item, ensure_ascii=False, separators=(',', ':'), default=str)}"
    )


def normalize_project_enum_text(items: Sequence[ProjectEnumExtractionItem]) -> str:
    """
        将已校验的枚举项统一为中文冒号和逐项换行格式。
    """
    return "\n".join(f"{item.value}：{item.description}" for item in items)


def build_ambiguous_candidate(
        field: ProjectEnumExtractionFieldInput,
        reason: str,
) -> ProjectEnumExtractionCandidate:
    """
        构造不携带枚举内容的统一歧义结果。
    """
    return ProjectEnumExtractionCandidate(
        source_row=field.source_row,
        field_name=field.field_name,
        status="ambiguous",
        normalized_text=None,
        items=[],
        reason=reason,
    )


def _find_item_evidence(
        remark: str,
        item: ProjectEnumExtractionItem,
        start: int,
) -> re.Match[str] | None:
    """
        检查枚举项是否出现在原备注中，用于防止AI编造原文不存在的枚举值
    """
    enum_value = re.escape(item.value)
    description = re.escape(item.description)
    pattern = re.compile(
        rf"(?:"
        rf"(?<![A-Za-z0-9]){enum_value}[ \t]*[:：-][ \t]*{description}"
        rf"|(?<![A-Za-z0-9]){enum_value} *{description}"
        rf"|{description}[ \t]*[:：-][ \t]*{enum_value}(?![A-Za-z0-9])"
        rf")"
    )
    return pattern.search(remark, pos=start)


def _items_have_ordered_evidence(
        remark: str,
        items: Sequence[ProjectEnumExtractionItem],
) -> bool:
    """
        校验枚举值是否按原文顺序连续出现
    """
    next_start = 0
    previous_end = 0
    for index, item in enumerate(items):
        match = _find_item_evidence(remark, item, next_start)
        if match is None:
            return False
        if index and any(boundary in remark[previous_end:match.start()] for boundary in ("\r", "\n", "。")):
            return False
        previous_end = match.end()
        next_start = match.end()
    return True


def _raw_enum_groups(remark: str) -> List[List[str]]:
    """
        从原文备注中查找意思枚举值组合。
    """
    groups: List[List[str]] = []
    for segment in re.split(r"[。\r\n]", remark):
        values = _FORWARD_PAIR_HEADER_RE.findall(segment)
        if len(values) < 2:
            values = _REVERSE_PAIR_HEADER_RE.findall(segment)
        if len(values) < 2:
            values = _DIRECT_PAIR_HEADER_RE.findall(segment)
        if len(values) >= 2:
            groups.append(values)
    return groups


def validate_project_enum_candidate(
        expected_field: ProjectEnumExtractionFieldInput | Mapping[str, Any],
        raw_candidate: ProjectEnumExtractionCandidate | Mapping[str, Any],
) -> ProjectEnumExtractionCandidate:
    """
        校验单个字段的AI结果。
    """
    expected = (
        expected_field
        if isinstance(expected_field, ProjectEnumExtractionFieldInput)
        else ProjectEnumExtractionFieldInput.model_validate(expected_field)
    )
    try:
        candidate = (
            raw_candidate
            if isinstance(raw_candidate, ProjectEnumExtractionCandidate)
            else ProjectEnumExtractionCandidate.model_validate(raw_candidate)
        )
    except ValidationError as exc:
        rule = "; ".join(
            f"{'.'.join(str(part) for part in error['loc']) + ': ' if error['loc'] else ''}"
            f"{error['msg']}"
            for error in exc.errors(include_url=False)
        )
        _log_structure_error(rule, raw_candidate)
        return build_ambiguous_candidate(expected, "AI枚举提取结果结构不合法")
    except (TypeError, ValueError) as exc:
        _log_structure_error(str(exc) or type(exc).__name__, raw_candidate)
        return build_ambiguous_candidate(expected, "AI枚举提取结果结构不合法")

    if candidate.source_row != expected.source_row or candidate.field_name != expected.field_name:
        return build_ambiguous_candidate(expected, "AI枚举提取结果与原字段不匹配")
    source_groups = _raw_enum_groups(expected.remark)
    if candidate.status == "not_found" and source_groups:
        return build_ambiguous_candidate(expected, "AI未识别原备注中已存在的枚举候选组")
    if candidate.status != "extracted":
        return candidate

    values = [item.value for item in candidate.items]
    if len(values) != len(set(values)):
        return build_ambiguous_candidate(expected, "AI枚举提取结果存在重复枚举键")
    if len(source_groups) != 1:
        return build_ambiguous_candidate(expected, "原备注不是唯一可确定的枚举组")
    if len(source_groups[0]) != len(set(source_groups[0])):
        return build_ambiguous_candidate(expected, "原备注枚举组存在重复枚举键")
    if values != source_groups[0]:
        return build_ambiguous_candidate(expected, "AI枚举键与原备注枚举组不一致")
    if not _items_have_ordered_evidence(expected.remark, candidate.items):
        return build_ambiguous_candidate(expected, "AI枚举提取结果无法在原备注中找到连续依据")

    normalized_text = normalize_project_enum_text(candidate.items)
    if len(normalized_text) > MAX_ENUM_NORMALIZED_TEXT_LENGTH:
        return build_ambiguous_candidate(expected, "AI枚举提取结果超出长度限制")
    return ProjectEnumExtractionCandidate(
        source_row=expected.source_row,
        field_name=expected.field_name,
        status="extracted",
        normalized_text=normalized_text,
        items=candidate.items,
        reason=None,
    )


def validate_project_enum_response(
        expected_fields: Sequence[ProjectEnumExtractionFieldInput | Mapping[str, Any]],
        raw_response: Mapping[str, Any],
) -> List[ProjectEnumExtractionCandidate]:
    """
        校验一次任务的AI响应。
    """
    expected = [
        item
        if isinstance(item, ProjectEnumExtractionFieldInput)
        else ProjectEnumExtractionFieldInput.model_validate(item)
        for item in expected_fields
    ]
    raw_results = raw_response.get("results") if isinstance(raw_response, Mapping) else None
    if not isinstance(raw_results, list):
        _log_structure_error("results必须是数组", raw_results)
        return [build_ambiguous_candidate(item, "AI枚举提取响应结构不合法") for item in expected]

    grouped: Dict[Tuple[int, str], List[Mapping[str, Any]]] = {}
    for raw_item in raw_results:
        if not isinstance(raw_item, Mapping):
            _log_structure_error("results中的每一项必须是JSON对象", raw_item)
            continue
        source_row = raw_item.get("source_row")
        field_name = raw_item.get("field_name")
        if not isinstance(source_row, int) or not isinstance(field_name, str):
            _log_structure_error(
                "source_row必须是整数且field_name必须是字符串",
                raw_item,
            )
            continue
        grouped.setdefault((source_row, field_name.strip()), []).append(raw_item)

    validated: List[ProjectEnumExtractionCandidate] = []
    for field in expected:
        candidates = grouped.get((field.source_row, field.field_name), [])
        if not candidates:
            _log_structure_error("响应缺少当前字段", None)
            validated.append(build_ambiguous_candidate(field, "AI枚举提取响应缺少当前字段"))
        elif len(candidates) > 1:
            _log_structure_error("响应包含重复字段", candidates)
            validated.append(build_ambiguous_candidate(field, "AI枚举提取响应包含重复字段"))
        else:
            validated.append(validate_project_enum_candidate(field, candidates[0]))
    return validated

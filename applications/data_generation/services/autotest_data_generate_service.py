# -*- coding: utf-8 -*-
'''
测试场景生成引擎：
把接口文档的字段和json/xml请求体中的实际字段对应起来，然后针对必输、长度，枚举和小数边界四类规则生成正反测试数据
'''

from __future__ import annotations

import json
import re
from collections import Counter
from decimal import Decimal, InvalidOperation, localcontext
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple, TypedDict
from xml.etree import ElementTree

from applications.data_generation.constants import (
    RULE_DECIMAL_BOUNDARY,
    RULE_ENUM,
    RULE_LENGTH,
    RULE_REQUIRED,
    SUPPORTED_RULE_CODES,
)
from applications.data_generation.services.autotest_interface_document_parser import (
    ParsedInterfaceDocument,#整份文档输出结构
    ParsedInterfaceField,   #单个字段输出结构
)

MAX_REQUEST_FIELDS = 10_000
MAX_ENUM_VALUES = 1_000
MAX_GENERATED_FIELD_LENGTH = 1_000
MAX_GENERATED_SCENARIOS = 10_000

_ENUM_SEPARATOR_RE = re.compile(r"[,，;；\r\n]+")
_ENUM_DESCRIPTION_SEPARATOR_RE = re.compile(r"[:：]")


class TestDataGenerationError(ValueError):
    """测试数据规则或输入数据不符合生成约束。"""


class TestDataGenerationLimitError(TestDataGenerationError):
    """单任务生成场景超过安全上限。"""


class MatchedInterfaceField(TypedDict):
    """接口文档字段与请求字段的匹配结果。"""
    interface_field: ParsedInterfaceField   #接口文档的字段定义
    request_path: str                       #展开后的json path/xml path
    request_value: Any                      #请求报文中的原始值
    match_error: Optional[str]              #匹配错误，例如重复字段


class GeneratedTestScenario(TypedDict):
    """单条测试场景的名称和完整请求体字段数据。"""
    scene_name: str                 #测试场景名称
    scenario_data: Dict[str, str]   #场景对应的完整请求体字段数据


def _xml_local_name(tag: str) -> str:
    """
        从xml形式（{namespace}name）的标签中取出name
    """
    if tag and "}" in tag:
        return tag.rsplit("}", 1)[-1]
    return tag or ""


def _json_child_path(prefix: str, key: object) -> str:
    """
        根据父路径和当前JSON字段名，生成该字段对应的JSON Path
        prefix：当前字段的父级JSON Path
        key：当前JSON对象的字段名
        返回值：拼接完成的子字段JSON Path
    """
    text = str(key)
    #判断能否使用点号格式
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_-]*", text):
        return f"{prefix}.{text}"
    escaped = text.replace("\\", "\\\\").replace("'", "\\'")
    return f"{prefix}['{escaped}']"


def flatten_json_body_fields(payload: Any) -> Dict[str, Any]:
    """将JSON请求体展平为保持顺序的JSONPath键值映射。"""
    result: Dict[str, Any] = {}

    def walk(value: Any, path: str) -> None:
        if len(result) >= MAX_REQUEST_FIELDS:
            raise TestDataGenerationError(f"请求报文字段数量不能超过{MAX_REQUEST_FIELDS}")
        if isinstance(value, Mapping):
            if not value:
                result[path] = {}
                return
            for key, child in value.items():
                walk(child, _json_child_path(path, key))
            return
        if isinstance(value, list):
            if not value:
                result[path] = []
                return
            # 数组使用首个元素作为接口字段模板，避免为相同字段重复生成场景。
            walk(value[0], f"{path}[0]")
            return
        result[path] = value

    walk(payload, "$")
    return result


def flatten_xml_body_fields(xml_text: str) -> Dict[str, Any]:
    """
        将XML请求体展平为保持文档顺序的XPath到值映射。
    """
    try:
        root = ElementTree.fromstring(str(xml_text or "").strip())
    except ElementTree.ParseError as exc:
        raise TestDataGenerationError("请求报文不是有效的XML") from exc

    result: Dict[str, Any] = {}

    def add(path: str, value: Any) -> None:
        if len(result) >= MAX_REQUEST_FIELDS:
            raise TestDataGenerationError(f"请求报文字段数量不能超过{MAX_REQUEST_FIELDS}")
        result[path] = value

    def walk(element: ElementTree.Element, path: str) -> None:
        for attribute_name, attribute_value in element.attrib.items():
            local_name = _xml_local_name(attribute_name)
            add(f"{path}/@{local_name}" if path != "." else f"./@{local_name}", attribute_value)

        children = list(element)
        if not children:
            add(path, "" if element.text is None else element.text.strip())
            return

        name_total = Counter(_xml_local_name(child.tag) for child in children)
        name_seen: Counter = Counter()
        for child in children:
            local_name = _xml_local_name(child.tag)
            name_seen[local_name] += 1
            child_path = f"./{local_name}" if path == "." else f"{path}/{local_name}"
            if name_total[local_name] > 1:
                child_path = f"{child_path}[{name_seen[local_name]}]"
            walk(child, child_path)

    walk(root, ".")
    return result


def extract_path_field_name(path: str) -> str:
    """从JSONPath、XPath或纯字段名中提取末级字段名。"""
    text = str(path or "").strip()
    if not text:
        return ""

    text = re.sub(r"\[\d+\]$", "", text)
    bracket_match = re.search(r"\[['\"]([^'\"\]]+)['\"]\]$", text)
    if bracket_match:
        field_name = bracket_match.group(1)
    else:
        text = text.rstrip("/")
        field_name = re.split(r"[./]", text)[-1].lstrip("@")
    if ":" in field_name:
        field_name = field_name.rsplit(":", 1)[-1]
    return field_name.strip()


def _extract_path_array_name(path: str) -> Optional[str]:
    """从当前支持的JSONPath或XPath中提取数组节点名称。"""
    text = str(path or "").strip()
    bracket_matches = list(re.finditer(r"\[['\"]([^'\"]+)['\"]\]\[\d+\]", text))
    segment_matches = list(re.finditer(r"(?:^|[./])([^./\[\]]+)\[\d+\]", text))
    matches = [
        (match.start(), match.group(1))
        for match in (*bracket_matches, *segment_matches)
    ]
    if not matches:
        return None
    return max(matches, key=lambda item: item[0])[1].strip() or None


def match_interface_fields(
        interface_fields: Sequence[ParsedInterfaceField],
        request_fields: Mapping[str, Any],
) -> List[MatchedInterfaceField]:
    """
        按BODY顺序匹配；数组字段同时使用数组名和末级字段名。
    """
    if len(request_fields) > MAX_REQUEST_FIELDS:
        raise TestDataGenerationError(f"请求报文字段数量不能超过{MAX_REQUEST_FIELDS}")

    interface_by_identity: Dict[Tuple[Optional[str], str], ParsedInterfaceField] = {}
    duplicate_identities = set()
    for interface_field in interface_fields:
        field_name = str(interface_field.get("field_name") or "").strip()
        if not field_name:
            continue
        array_name = str(interface_field.get("array_name") or "").strip() or None
        identity = (array_name, field_name)
        if identity in interface_by_identity:
            duplicate_identities.add(identity)
            continue
        interface_by_identity[identity] = interface_field
        if interface_field.get("duplicate_in_context"):
            duplicate_identities.add(identity)

    matches: List[MatchedInterfaceField] = []
    matched_identities = set()
    for path, value in request_fields.items():
        request_path = str(path)
        field_name = extract_path_field_name(request_path)
        if not field_name:
            continue
        identity = (_extract_path_array_name(request_path), field_name)
        interface_field = interface_by_identity.get(identity)
        if interface_field is None or identity in matched_identities:
            continue
        matched_identities.add(identity)
        matches.append({
            "interface_field": interface_field,
            "request_path": request_path,
            "request_value": value,
            "match_error": (
                "接口文档存在重复字段，请检查"
                if identity in duplicate_identities
                else None
            ),
        })
    return matches


def _normalize_rule_codes(selected_rule_codes: Sequence[str]) -> Tuple[str, ...]:
    """校验、去重并按固定执行顺序返回用户选择的规则代码。"""
    selected = {code.strip() for code in selected_rule_codes if code.strip()}
    if not selected:
        raise TestDataGenerationError("请至少选择一个数据校验点")
    unknown = selected.difference(SUPPORTED_RULE_CODES)
    if unknown:
        raise TestDataGenerationError(f"存在不支持的数据校验点: {', '.join(sorted(unknown))}")
    return tuple(code for code in SUPPORTED_RULE_CODES if code in selected)


def _field_scene_name_parts(field: ParsedInterfaceField) -> Tuple[str, str]:
    """返回场景名称使用的中英文字段名，中文名称为空时回落到英文名称。"""
    field_name = str(field.get("field_name") or "").strip()
    if not field_name:
        raise TestDataGenerationError("接口文档字段的英文名称/字段名不能为空")
    chinese_name = str(field.get("field_chinese_name") or "").strip() or field_name
    return chinese_name, field_name


def _scene_name(
        field: ParsedInterfaceField,
        polarity: str,
        rule_name: str,
        detail: str,
) -> str:
    chinese_name, field_name = _field_scene_name_parts(field)
    return f"[{polarity}][{chinese_name}][{field_name}]{rule_name}，{detail}"[:255]


def _special_scene_name(field: ParsedInterfaceField, detail: str) -> str:
    chinese_name, field_name = _field_scene_name_parts(field)
    return f"[{chinese_name}][{field_name}]{detail}"[:255]


def _value_to_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)
    return str(value)


def _scenario_with_value(
        baseline: Mapping[str, str],
        path: str,
        value: str,
) -> Dict[str, str]:
    scenario_data = dict(baseline)
    scenario_data[path] = value
    return scenario_data


def _append_scenario(
        scenarios: List[GeneratedTestScenario],
        scene_name: str,
        scenario_data: Dict[str, str],
) -> None:
    if len(scenarios) >= MAX_GENERATED_SCENARIOS:
        raise TestDataGenerationLimitError(
            f"生成场景数量不能超过{MAX_GENERATED_SCENARIOS}"
        )
    scenarios.append({"scene_name": scene_name, "scenario_data": scenario_data})


def _overlength_value(original_value: Any, generated_length: int) -> str:
    if generated_length < 1 or generated_length > MAX_GENERATED_FIELD_LENGTH:
        raise TestDataGenerationError(
            f"生成字段长度必须在1到{MAX_GENERATED_FIELD_LENGTH}之间"
        )
    if isinstance(original_value, str):
        return "A" * generated_length
    if isinstance(original_value, bool) or original_value is None:
        raise TestDataGenerationError("原始报文字段格式不支持，请检查")
    if isinstance(original_value, (int, float)):
        return "9" * generated_length
    raise TestDataGenerationError("原始报文字段格式不支持，请检查")


def _decimal_text(integer_digits: int, decimal_digits: int) -> str:
    integer_part = "9" * integer_digits
    if decimal_digits <= 0:
        return integer_part
    return f"{integer_part}.{('9' * decimal_digits)}"


def _validate_decimal_original_value(original_value: Any) -> None:
    if isinstance(original_value, bool) or original_value is None:
        raise TestDataGenerationError("原始报文字段格式不支持，请检查")
    if not isinstance(original_value, (str, int, float)):
        raise TestDataGenerationError("原始报文字段格式不支持，请检查")


def _decimal_overlength_values(
        original_value: Any,
        integer_length: int,
        decimal_length: int,
) -> Tuple[str, str]:
    _validate_decimal_original_value(original_value)
    if integer_length < 1 or decimal_length < 0:
        raise TestDataGenerationError("小数字段长度必须保证整数位大于0且小数位不小于0")
    if integer_length + decimal_length + 1 > MAX_GENERATED_FIELD_LENGTH:
        raise TestDataGenerationError(
            f"生成字段长度不能超过{MAX_GENERATED_FIELD_LENGTH}"
        )
    integer_overflow = _decimal_text(integer_length + 1, decimal_length)
    decimal_overflow = _decimal_text(integer_length, decimal_length + 1)
    return integer_overflow, decimal_overflow


def _split_enum_values(raw_value: Optional[str]) -> List[str]:
    values: List[str] = []
    seen = set()
    for item in _ENUM_SEPARATOR_RE.split(str(raw_value or "")):
        # 接口文档允许使用“枚举值:说明”记录自定义赋值，冒号后的内容
        # 仅是描述，不得参与正场景赋值、去重或反场景的末值加一。
        value = _ENUM_DESCRIPTION_SEPARATOR_RE.split(item, maxsplit=1)[0].strip()
        if not value or value in seen:
            continue
        seen.add(value)
        values.append(value)
        if len(values) > MAX_ENUM_VALUES:
            raise TestDataGenerationError(f"单字段枚举值数量不能超过{MAX_ENUM_VALUES}")
    return values


def _cast_enum_value(raw_value: str, original_value: Any) -> str:
    if isinstance(original_value, str) or original_value is None:
        return raw_value
    if isinstance(original_value, bool):
        raise TestDataGenerationError("布尔字段不支持枚举值测试数据生成")
    try:
        decimal_value = Decimal(raw_value)
    except InvalidOperation as exc:
        raise TestDataGenerationError(f"枚举值「{raw_value}」与原始数值类型不匹配") from exc
    if isinstance(original_value, int):
        if decimal_value != decimal_value.to_integral_value():
            raise TestDataGenerationError(f"枚举值「{raw_value}」不是整数")
    return raw_value


def _enum_outlier(last_value: str, original_value: Any) -> Optional[str]:
    if isinstance(original_value, str) or original_value is None:
        return f"{last_value}1"
    if isinstance(original_value, bool):
        return None
    try:
        decimal_value = Decimal(last_value) + 1
    except InvalidOperation as exc:
        raise TestDataGenerationError(f"最后一个枚举值「{last_value}」无法执行加1") from exc
    if isinstance(original_value, int):
        if decimal_value != decimal_value.to_integral_value():
            raise TestDataGenerationError(f"最后一个枚举值「{last_value}」不是整数")
    return format(decimal_value, "f")


def _format_decimal(value: Decimal, decimal_length: int) -> str:
    if value == 0:
        return "0"
    return format(value, f".{decimal_length}f")


def _decimal_boundary_values(
        original_value: Any,
        integer_length: int,
        decimal_length: int,
) -> List[Tuple[str, str]]:
    _validate_decimal_original_value(original_value)
    if integer_length < 1 or decimal_length < 1:
        raise TestDataGenerationError("小数边界值要求整数位和小数位均大于0")
    if integer_length + decimal_length > MAX_GENERATED_FIELD_LENGTH:
        raise TestDataGenerationError(
            f"生成字段长度不能超过{MAX_GENERATED_FIELD_LENGTH}"
        )

    with localcontext() as context:
        context.prec = integer_length + decimal_length + 4
        quantum = Decimal(1).scaleb(-decimal_length)
        maximum = (Decimal(10) ** integer_length) - quantum
        values = (
            ("反", -quantum),
            ("反", Decimal(0)),
            ("正", quantum),
            ("正", maximum - quantum),
            ("正", maximum),
            ("反", maximum + quantum),
        )

    result: List[Tuple[str, str]] = []
    for polarity, decimal_value in values:
        text_value = _format_decimal(decimal_value, decimal_length)
        # 先以文本保留小数位；执行时会根据原报文字段类型恢复JSON数字语义。
        result.append((polarity, text_value))
    return result


def _generate_required_scenarios(
        scenarios: List[GeneratedTestScenario],
        baseline: Mapping[str, str],
        matched: MatchedInterfaceField,
) -> None:
    field = matched["interface_field"]
    if not str(field.get("required_text") or "").strip():
        _append_scenario(
            scenarios,
            _special_scene_name(field, "接口文档的是否必输项为空，请检查"),
            {},
        )
        return
    if field.get("required") is not True:
        return
    for marker, detail in (
            ("#NULL", "生成NULL值"),
            ("#单个空格", "生成单个空格"),
            ("#空字符串", "生成空字符串"),
    ):
        _append_scenario(
            scenarios,
            _scene_name(field, "反", "必输性校验", detail),
            _scenario_with_value(baseline, matched["request_path"], marker),
        )


def _generate_length_scenarios(
        scenarios: List[GeneratedTestScenario],
        baseline: Mapping[str, str],
        matched: MatchedInterfaceField,
) -> None:
    field = matched["interface_field"]
    original_value = matched["request_value"]
    if not str(field.get("length") or "").strip():
        _append_scenario(
            scenarios,
            _special_scene_name(field, "接口文档的长度项为空，请检查"),
            {},
        )
        return
    length_error = str(field.get("length_error") or "").strip()
    if length_error:
        _append_scenario(
            scenarios,
            _special_scene_name(field, f"字段长度校验，{length_error}"),
            {},
        )
        return

    total_length = field.get("total_length")
    integer_length = field.get("integer_length")
    decimal_length = field.get("decimal_length")
    if not isinstance(total_length, int) or not isinstance(integer_length, int):
        return
    if decimal_length is None:
        generated_value = _overlength_value(original_value, total_length + 1)
        _append_scenario(
            scenarios,
            _scene_name(
                field,
                "反",
                "字段长度校验",
                f"配置长度{total_length}，生成长度{total_length + 1}",
            ),
            _scenario_with_value(baseline, matched["request_path"], generated_value),
        )
        return

    integer_overflow, decimal_overflow = _decimal_overlength_values(
        original_value,
        integer_length,
        decimal_length,
    )
    for rule_name, configured_length, generated_value in (
            ("整数长度校验", integer_length, integer_overflow),
            ("小数长度校验", decimal_length, decimal_overflow),
    ):
        _append_scenario(
            scenarios,
            _scene_name(
                field,
                "反",
                rule_name,
                f"配置长度{configured_length}，生成长度{configured_length + 1}",
            ),
            _scenario_with_value(baseline, matched["request_path"], generated_value),
        )


def _generate_enum_scenarios(
        scenarios: List[GeneratedTestScenario],
        baseline: Mapping[str, str],
        matched: MatchedInterfaceField,
) -> None:
    field = matched["interface_field"]
    original_value = matched["request_value"]
    enum_values = _split_enum_values(field.get("enum_values"))
    if not enum_values:
        return
    for raw_value in enum_values:
        generated_value = _cast_enum_value(raw_value, original_value)
        _append_scenario(
            scenarios,
            _scene_name(field, "正", "枚举值校验", f"校验值{generated_value}"),
            _scenario_with_value(baseline, matched["request_path"], generated_value),
        )
    outlier = _enum_outlier(enum_values[-1], original_value)
    if outlier is None:
        return
    _append_scenario(
        scenarios,
        _scene_name(field, "反", "枚举值校验", f"生成值{outlier}"),
        _scenario_with_value(baseline, matched["request_path"], outlier),
    )


def _generate_decimal_boundary_scenarios(
        scenarios: List[GeneratedTestScenario],
        baseline: Mapping[str, str],
        matched: MatchedInterfaceField,
) -> None:
    """生成小数字段边界值校验场景。"""
    field = matched["interface_field"]
    original_value = matched["request_value"]
    length_error = str(field.get("length_error") or "").strip()
    if length_error:
        _append_scenario(
            scenarios,
            _special_scene_name(field, f"小数字段边界值校验，{length_error}"),
            {},
        )
        return
    integer_length = field.get("integer_length")
    decimal_length = field.get("decimal_length")
    total_length = field.get("total_length")
    if (
        not isinstance(total_length, int)
        or not isinstance(integer_length, int)
        or not isinstance(decimal_length, int)
    ):
        return
    for polarity, generated_value in _decimal_boundary_values(
            original_value,
            integer_length,
            decimal_length,
    ):
        _append_scenario(
            scenarios,
            _scene_name(
                field,
                polarity,
                "小数字段边界值校验",
                f"配置长度（{total_length}，{decimal_length}），生成值{generated_value}",
            ),
            _scenario_with_value(baseline, matched["request_path"], generated_value),
        )


def generate_test_data_scenarios(
        interface_document: ParsedInterfaceDocument,
        request_fields: Mapping[str, Any],
        selected_rule_codes: Sequence[str],
) -> List[GeneratedTestScenario]:
    """
        测试数据功能的总调度函数，负责校验输入，与请求体进行字段匹配，并且按照对应的字段规则调用生成函数，最后返回所有测试场景
    """
    selected_rules = _normalize_rule_codes(selected_rule_codes)
    interface_fields = interface_document.get("fields")
    if not isinstance(interface_fields, list):
        raise TestDataGenerationError("接口文档解析结果缺少fields列表")
    if not isinstance(request_fields, Mapping):
        raise TestDataGenerationError("请求报文必须是路径到字段值的映射")

    raw_request_fields = dict(request_fields)
    baseline = {
        str(path): _value_to_text(value)
        for path, value in raw_request_fields.items()
    }
    scenarios: List[GeneratedTestScenario] = [{
        "scene_name": "正交易场景",
        "scenario_data": dict(baseline),
    }]
    matched_fields = match_interface_fields(interface_fields, raw_request_fields)
    rule_handlers = {
        RULE_REQUIRED: ("必输性校验", _generate_required_scenarios),
        RULE_LENGTH: ("字段长度校验", _generate_length_scenarios),
        RULE_ENUM: ("枚举值校验", _generate_enum_scenarios),
        RULE_DECIMAL_BOUNDARY: ("小数字段边界值校验", _generate_decimal_boundary_scenarios),
    }
    # 同一字段的全部校验场景必须相邻，导出和应用会沿用这里的生成顺序。
    for matched in matched_fields:
        if matched["match_error"]:
            _append_scenario(
                scenarios,
                _special_scene_name(
                    matched["interface_field"],
                    matched["match_error"],
                ),
                {},
            )
            continue
        if isinstance(matched["request_value"], (list, tuple, dict)):
            continue
        for rule_code in selected_rules:
            rule_name, generator = rule_handlers[rule_code]
            original_count = len(scenarios)
            try:
                generator(scenarios, baseline, matched)
            except TestDataGenerationLimitError:
                raise
            except TestDataGenerationError as exc:
                # 字段规则失败时撤回该规则的部分结果，以单条错误场景代替。
                del scenarios[original_count:]
                _append_scenario(
                    scenarios,
                    _special_scene_name(
                        matched["interface_field"],
                        f"{rule_name}，{exc}",
                    ),
                    {},
                )
    return scenarios

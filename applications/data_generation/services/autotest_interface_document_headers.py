# -*- coding: utf-8 -*-

"""
    接口文档解析与写回共用的表头文本处理规则
"""

from __future__ import annotations

import re
from typing import Dict, Optional, Tuple


ENUM_COLUMN_HEADER = "枚举值"

ESB_HEADER_ALIASES: Dict[str, Tuple[str, ...]] = {
    "英文名称": ("英文名称",),
    "中文名称": ("中文名称",),
    "数据格式": ("数据类型", "格式", "数据格式"),
    "长度": ("长度", "字段长度"),
    "是否必输": ("是否必输", "必输", "必填"),
    ENUM_COLUMN_HEADER: (ENUM_COLUMN_HEADER,),
}
PROJECT_HEADER_ALIASES: Dict[str, Tuple[str, ...]] = {
    "字段名": ("字段名",),
    "必填": ("必填", "必输", "是否必输", "是否必填"),
    "数标": ("数标",),
    "格式": ("数据类型", "格式", "数据格式"),
    "字段长度": ("字段长度", "长度"),
    "备注": ("备注",),
}
INTEGRATION_HEADER_ALIASES: Dict[str, Tuple[str, ...]] = {
    "字段名": ("字段名",),
    "字段描述": ("字段描述",),
    "长度": ("长度",),
    "必输/可选": ("必输/可选",),
    "取值范围/格式": (
        "取值范围/格式",
        "取值范围或格式",
    ),
    "业务规则": ("业务规则",),
}
_INTEGRATION_COLLECTION_ALIASES = ("所属集合/集合属性",)

# 别名映射的插入顺序同时是模板要求的表头顺序。
ESB_HEADERS = tuple(ESB_HEADER_ALIASES)
PROJECT_HEADERS = tuple(PROJECT_HEADER_ALIASES)
INTEGRATION_HEADERS = tuple(INTEGRATION_HEADER_ALIASES)


def cell_text(value: object) -> Optional[str]:
    """将Excel单元格值转换为去除首尾空白的可选文本。"""
    if value is None:
        return None
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    text = str(value).replace("\xa0", " ").strip()
    return text or None


def header_text(value: object) -> str:
    """移除表头文本中的所有空白字符。"""
    return re.sub(r"\s+", "", cell_text(value) or "")


def chinese_header_text(value: object) -> str:
    """只保留表头中的中文字符，忽略附带的英文说明和分隔符。"""
    return "".join(re.findall(r"[\u4e00-\u9fff]", cell_text(value) or ""))


_INTEGRATION_CHINESE_ALIASES = {
    expected_header: frozenset(
        text
        for alias in aliases
        if (text := chinese_header_text(alias))
    )
    for expected_header, aliases in INTEGRATION_HEADER_ALIASES.items()
}
_INTEGRATION_COLLECTION_CHINESE_ALIASES = frozenset(
    text
    for alias in _INTEGRATION_COLLECTION_ALIASES
    if (text := chinese_header_text(alias))
)


def _standard_header_matches(
        value: object,
        expected_header: str,
        aliases: Dict[str, Tuple[str, ...]],
) -> bool:
    return header_text(value) in aliases[expected_header]


def esb_header_matches(value: object, expected_header: str) -> bool:
    """按ESB文档别名识别表头。"""
    return _standard_header_matches(value, expected_header, ESB_HEADER_ALIASES)


def project_header_matches(value: object, expected_header: str) -> bool:
    """按项目文档别名识别表头。"""
    return _standard_header_matches(value, expected_header, PROJECT_HEADER_ALIASES)


def enum_header_matches(value: object) -> bool:
    """识别系统新增的枚举值列表头。"""
    return header_text(value) == ENUM_COLUMN_HEADER


def integration_header_matches(value: object, expected_header: str) -> bool:
    """按中文标题识别整合文档表头，忽略附带的英文说明。"""
    return chinese_header_text(value) in _INTEGRATION_CHINESE_ALIASES[expected_header]


def integration_collection_header_matches(value: object) -> bool:
    """识别整合文档的集合属性表头，忽略附带的英文说明。"""
    return chinese_header_text(value) in _INTEGRATION_COLLECTION_CHINESE_ALIASES


def integration_collection_header_level(value: object) -> Optional[int]:
    """返回整合文档集合列层级：1为内层，2为外层。"""
    if not integration_collection_header_matches(value):
        return None
    text = header_text(value)
    return 2 if re.search(r"_\s*1(?=\s|[A-Za-z]|$)", text) else 1

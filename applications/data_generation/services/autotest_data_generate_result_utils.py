# -*- coding: utf-8 -*-

from typing import Any, Mapping


def get_result_value(result: Any, field: str) -> Any:
    """
        统一读取字典或ORM结果对象的字段值。
        result：一条生成的测试数据（测试场景）
        field：字段名
        返回值：字段名对应的值
    """
    if isinstance(result, Mapping):
        return result.get(field)
    return getattr(result, field, None)

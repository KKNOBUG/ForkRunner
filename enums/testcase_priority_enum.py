# -*- coding: utf-8 -*-
from enums.base_enum_cls import StringEnum


class TestCasePriorityEnum(StringEnum):
    """
    测试案例风险级别枚举值
    """
    P1 = "低"
    P2 = "中"
    P3 = "高"
    P4 = "危"

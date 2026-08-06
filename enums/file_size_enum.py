# -*- coding: utf-8 -*-
from enums.base_enum_cls import StringEnum


class FileSizeEum(StringEnum):
    """
    文件的体积限制枚举值
    """
    TINY = "tiny"
    MICRO = "micro"
    SMALL = "small"
    MEDIUM = "medium"
    LARGE = "large"
    HUGE = "huge"

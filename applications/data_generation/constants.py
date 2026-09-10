# -*- coding: utf-8 -*-

"""数据生成模块共用的业务常量。"""

RULE_REQUIRED = "0"
RULE_LENGTH = "1"
RULE_ENUM = "2"
RULE_DECIMAL_BOUNDARY = "3"

# 该顺序同时决定数据校验规则的执行顺序。
SUPPORTED_RULE_CODES = (
    RULE_REQUIRED,
    RULE_LENGTH,
    RULE_ENUM,
    RULE_DECIMAL_BOUNDARY,
)

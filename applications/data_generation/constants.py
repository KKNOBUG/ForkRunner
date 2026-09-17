# -*- coding: utf-8 -*-

"""数据生成模块共用的业务常量。"""

from enums import AutoTestInterfaceStyle

# 接口文档处理限制
MAX_DOCUMENT_SIZE = 16 * 1024 * 1024
MAX_DOCUMENT_ROWS = 20_000
MAX_DOCUMENT_COLUMNS = 256

# 枚举抽取与生成限制
MAX_ENUM_EXTRACTION_BATCH_SIZE = 200
MAX_ENUM_VALUES_PER_FIELD = 1_000
MAX_ENUM_NORMALIZED_TEXT_LENGTH = 20_000

# 单任务生成与持久化共同使用该上限，避免生成成功后因数量不一致而写入失败。
MAX_GENERATED_SCENARIOS = 10_000

ENUM_EXTRACTION_INTERFACE_STYLES = frozenset({
    AutoTestInterfaceStyle.PROJECT,
    AutoTestInterfaceStyle.INTEGRATION,
})

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

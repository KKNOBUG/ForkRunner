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
# 与 output/skills/test_data_generator/scripts/output_writer.py 的 MAX_OUTPUT_ROWS、
# output/skills/test_data_generator/scripts/input_reader.py 的 MAX_INPUT_SCENARIOS
# 保持一致；修改时需同步 skill 脚本。
MAX_GENERATED_SCENARIOS = 10_000
# 场景描述写入 result.scene_name 列、Pydantic schema 与 SDK 输出schema共用该上限。
# 与 output/skills/test_data_generator/scripts/output_writer.py 的 MAX_SCENE_NAME_LENGTH
# 保持一致；修改时需同步 skill 脚本。
MAX_SCENE_NAME_LENGTH = 255
# 单次请求快照允许的最大字段数。
# 与 output/skills/test_data_generator/scripts/request_snapshot_reader.py 的
# MAX_REQUEST_FIELDS 保持一致；修改时需同步 skill 脚本。
MAX_REQUEST_FIELDS = 10_000
# 智能生成结果可能包含大量场景，不与上传文档的16MB限制共用。
MAX_INTELLIGENT_OUTPUT_SIZE = 64 * 1024 * 1024

# 智能生成服务与任务目录中的Skill共同遵循该文件名契约。
INTELLIGENT_ANALYSIS_FILE_NAME = "_analysis_result.json"
INTELLIGENT_OUTPUT_FILE_NAME = "output_scenarios_data.xlsx"
INTELLIGENT_SCENARIO_FILE_NAME = "input_scenarios.xlsx"
INTELLIGENT_SCENARIO_DATA_FILE_NAME = "input_scenarios.json"
INTELLIGENT_SNAPSHOT_FILE_NAME = "request_snapshot.json"

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

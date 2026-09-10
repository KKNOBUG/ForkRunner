# -*- coding: utf-8 -*-

"""
说明：
    任务表模型类，记录一次测试数据生成任务和不可变输入快照。
"""

from tortoise import fields

from applications.base.services.scaffold import (
    JSONTextField,
    MaintainMixin,
    ScaffoldModel,
    StateModel,
    TimestampMixin,
    unique_identify,
)
from enums import AutoTestDataGenerateStatus


class AutoTestDataGenerateTaskModel(ScaffoldModel, MaintainMixin, TimestampMixin, StateModel):
    """记录一次测试数据生成任务及其不可变输入快照。"""

    """任务唯一业务代码"""
    task_code = fields.CharField(
        max_length=64,
        default=unique_identify,
        unique=True,
        description="数据生成任务标识代码",
    )
    """Celery调度ID，用于并发下判断执行权是否还属于当前worker"""
    celery_id = fields.CharField(
        max_length=255,
        null=True,
        unique=True,
        description="Celery调度ID",
    )

    """业务定位"""
    case_id = fields.BigIntField(ge=1, description="用例ID")
    step_id = fields.BigIntField(ge=1, description="步骤ID")
    step_code = fields.CharField(max_length=64, description="步骤标识代码")
    interface_style = fields.CharField(
        max_length=16,
        description="接口样式(esb/project)",
    )
    """输入快照"""
    rule_codes = fields.JSONField(default=list, description="本次选择的数据校验规则代码列表")
    request_snapshot = JSONTextField(description="任务提交时的接口请求数据快照")
    interface_schema_snapshot = JSONTextField(
        null=True,
        description="接口文档解析后的统一字段结构快照",
    )
    """接口文档原文件信息"""
    interface_file_name = fields.CharField(max_length=255, description="接口文档原始文件名")
    interface_file_hash = fields.CharField(max_length=64, description="接口文档SHA-256哈希值")
    interface_storage_key = fields.CharField(max_length=1024, description="接口文档存储相对键")

    """产出和运行状态"""
    generated_file_name = fields.CharField(max_length=255, description="生成数据文件名")
    task_status = fields.CharEnumField(
        AutoTestDataGenerateStatus,
        default=AutoTestDataGenerateStatus.IN_PROGRESS,
        description="生成状态",
    )
    generated_count = fields.IntField(default=0, ge=0, description="已生成场景数量")
    task_summary = fields.JSONField(default=dict, description="生成统计及告警摘要")
    error_message = fields.TextField(null=True, description="任务失败原因")
    started_time = fields.DatetimeField(null=True, description="任务开始时间")
    finished_time = fields.DatetimeField(null=True, description="任务结束时间")

    class Meta:
        table = "krun_autotest_data_generate_task"
        table_description = "自动化测试-测试数据生成任务表"
        indexes = (
            ("case_id", "step_code", "state", "created_time"),
            ("task_status", "created_time"),
        )
        ordering = ["-created_time", "-id"]

    def __str__(self):
        return self.task_code or ""

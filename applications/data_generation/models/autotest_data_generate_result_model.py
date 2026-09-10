# -*- coding: utf-8 -*-
from tortoise import fields
from applications.base.services.scaffold import JSONTextField, ScaffoldModel

"""
说明：
    结果表模型类，记录一次生成任务产出的全部字段及数据。
"""

class AutoTestDataGenerateResultModel(ScaffoldModel):
    """task:result = 1: n"""

    """指向任务表主键，逻辑外键,数据库层没有约束，也没有自动的级联删除"""
    task_id = fields.BigIntField(ge=1, description="数据生成任务ID")
    scene_name = fields.CharField(max_length=255, description="测试场景中文名")
    scenario_data = JSONTextField(description="该场景全部字段及生成数据")
    created_time = fields.DatetimeField(auto_now_add=True, description="创建时间")

    class Meta:
        table = "krun_autotest_data_generate_result"
        table_description = "自动化测试-测试数据生成结果表"
        unique_together = (
            ("task_id", "scene_name"),
        )
        ordering = ["task_id", "id"]

    def __str__(self):
        return f"{self.task_id}:{self.scene_name}"

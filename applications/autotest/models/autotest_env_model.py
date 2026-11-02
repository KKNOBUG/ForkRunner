# -*- coding: utf-8 -*-
from tortoise import fields

from applications.base.services.scaffold import (
    MaintainMixin,
    ReserveFields,
    ScaffoldModel,
    StateModel,
    TimestampMixin,
    unique_identify,
)


class AutoTestEnvModel(ScaffoldModel, MaintainMixin, TimestampMixin, StateModel, ReserveFields):
    env_name = fields.CharField(max_length=128, unique=True, description="环境名称")
    env_code = fields.CharField(max_length=64, default=unique_identify, unique=True, description="环境标识代码")

    class Meta:
        table = "tbx_autotest_env"
        table_description = "自动化测试-环境枚举表"
        ordering = ["-updated_time"]

    def __str__(self):
        return f"AutoTestEnvModel(env_name={self.env_name}, env_code={self.env_code})"

# -*- coding: utf-8 -*-
from tortoise import fields

from applications.base.services.scaffold import (
    MaintainMixin,
    ScaffoldModel,
    TimestampMixin,
)
from enums import MenuType


class Menu(ScaffoldModel, MaintainMixin, TimestampMixin):
    """菜单模型。"""

    name = fields.CharField(max_length=32, index=True, description="菜单名称")
    remark = fields.JSONField(null=True, description="保留字段")
    menu_type = fields.CharEnumField(MenuType, index=True, null=True, description="菜单类型")
    icon = fields.CharField(max_length=128, null=True, description="菜单图标")
    path = fields.CharField(max_length=128, index=True, description="菜单路径")
    order = fields.IntField(default=0, index=True, description="排序")
    parent_id = fields.IntField(default=0, max_length=16, index=True, description="父菜单ID")
    is_hidden = fields.BooleanField(default=False, description="是否隐藏")
    component = fields.CharField(max_length=128, description="组件")
    keepalive = fields.BooleanField(default=True, description="存活")
    redirect = fields.CharField(max_length=128, null=True, description="重定向")

    class Meta:
        table = "tbx_menu"
        unique_together = (("name", "path"), ("parent_id", "path"))

    def __str__(self):
        return f"Menu(name={self.name}, menu_type={self.menu_type}, path={self.path})"

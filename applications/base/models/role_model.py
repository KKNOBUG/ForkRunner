# -*- coding: utf-8 -*-
from tortoise import fields

from applications.base.services.scaffold import ScaffoldModel, TimestampMixin, MaintainMixin


class Role(ScaffoldModel, TimestampMixin, MaintainMixin):
    """角色模型。"""

    code = fields.CharField(max_length=16, unique=True, description="角色代码")
    name = fields.CharField(max_length=64, unique=True, description="角色名称")
    description = fields.TextField(null=True, description="角色描述")
    menus = fields.ManyToManyField(
        model_name="models.Menu",
        related_name="role_menus",
        through="tbx_role_menus",
    )
    routers = fields.ManyToManyField(
        model_name="models.Router",
        related_name="role_routers",
        through="tbx_role_routers"
    )

    class Meta:
        table = "tbx_role"

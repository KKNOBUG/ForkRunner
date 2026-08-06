# -*- coding: utf-8 -*-
from applications.base.services.audit_crud import AuditCrud
from applications.base.services.menu_crud import MenuCrud
from applications.base.services.role_crud import RoleCrud
from applications.base.services.router_crud import RouterCrud


async def get_audit_crud() -> AuditCrud:
    """
    获取审计CRUD服务实例（依赖注入）。

    :return: AuditCrud 实例
    """
    return AuditCrud()


async def get_menu_crud() -> MenuCrud:
    """
    获取菜单CRUD服务实例（依赖注入）。

    :return: MenuCrud 实例
    """
    return MenuCrud()


async def get_role_crud() -> RoleCrud:
    """
    获取角色CRUD服务实例（依赖注入）。

    :return: RoleCrud 实例
    """
    return RoleCrud()


async def get_router_crud() -> RouterCrud:
    """
    获取路由CRUD服务实例（依赖注入）。

    :return: RouterCrud 实例
    """
    return RouterCrud()

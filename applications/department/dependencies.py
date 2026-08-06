# -*- coding: utf-8 -*-
from applications.department.services.department_crud import DepartmentCrud


async def get_dept_crud() -> DepartmentCrud:
    """获取部门CRUD服务实例"""
    return DepartmentCrud()

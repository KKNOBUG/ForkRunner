# -*- coding: utf-8 -*-
from fastapi import Depends

from applications.user.services.user_crud import UserCrud


async def get_user_crud() -> UserCrud:
    """
    获取用户CRUD服务实例。

    :return: UserCrud 实例
    """
    return UserCrud()


DependUserServices = Depends(get_user_crud)

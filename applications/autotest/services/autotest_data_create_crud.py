# -*- coding: utf-8 -*-
import traceback
from typing import Any, Dict, List, Optional

from tortoise.exceptions import DoesNotExist, IntegrityError

from applications.autotest.models.autotest_data_create_model import AutoTestDataCreateModel
from applications.autotest.schemas.autotest_data_create_schema import AutoTestDataCreateCreate, AutoTestDataCreateUpdate
from applications.base.services.scaffold import ScaffoldCrud
from configure import LOGGER
from core.exceptions import DataBaseStorageException, NotFoundException, ParameterException


class AutoTestDataCreateCrud(ScaffoldCrud[AutoTestDataCreateModel, AutoTestDataCreateCreate, AutoTestDataCreateUpdate]):
    """接口文件生成记录CRUD。"""

    def __init__(self):
        super().__init__(model=AutoTestDataCreateModel)

    async def get_by_id(self, data_create_id: int, on_error: bool = False, **kwargs) -> Optional[AutoTestDataCreateModel]:
        """
        根据主键ID查询生成记录。

        :param data_create_id: 生成记录主键ID
        :param on_error: 未找到时是否抛出异常
        :param kwargs: 额外过滤条件
        :return: 生成记录实例或None
        """
        if not data_create_id:
            error_message: str = "查询接口文件生成记录失败, 参数[data_create_id]不允许为空"
            LOGGER.error(error_message)
            raise ParameterException(message=error_message)

        instance = await self.get_or_none(id=data_create_id, **kwargs)
        if not instance and on_error:
            error_message: str = f"查询接口文件生成记录失败, 记录[id={data_create_id}]不存在"
            LOGGER.error(error_message)
            raise NotFoundException(message=error_message)
        return instance

    async def get_by_code(self, create_code: str, on_error: bool = False, **kwargs) -> Optional[AutoTestDataCreateModel]:
        """
        根据create_code查询生成记录。

        :param create_code: 接口文件标识代码
        :param on_error: 未找到时是否抛出异常
        :param kwargs: 额外过滤条件
        :return: 生成记录实例或None
        """
        if not (create_code or "").strip():
            error_message: str = "查询接口文件生成记录失败, 参数[create_code]不允许为空"
            LOGGER.error(error_message)
            raise ParameterException(message=error_message)

        instance = await self.model.filter(create_code=create_code.strip(), **kwargs).first()
        if not instance and on_error:
            error_message: str = f"查询接口文件生成记录失败, 记录[create_code={create_code}]不存在"
            LOGGER.error(error_message)
            raise NotFoundException(message=error_message)
        return instance

    async def get_by_step(self, step_code: str, on_error: bool = False, **kwargs) -> List[AutoTestDataCreateModel]:
        """
        根据步骤标识查询生成记录列表，按ID倒序最多返回3条。

        :param step_code: 步骤标识代码
        :param on_error: 未找到时是否抛出异常
        :param kwargs: 额外过滤条件
        :return: 生成记录实例列表
        """
        if not (step_code or "").strip():
            error_message: str = "查询接口文件生成记录失败, 参数[step_code]不允许为空"
            LOGGER.error(error_message)
            raise ParameterException(message=error_message)

        instances = await self.model.filter(step_code=step_code.strip(), **kwargs).order_by("-id").limit(3).all()
        if not instances and on_error:
            error_message: str = f"查询接口文件生成记录失败, 记录[step_code={step_code}]不存在"
            LOGGER.error(error_message)
            raise NotFoundException(message=error_message)
        return instances

    async def get_by_hash(self, file_hash: str, on_error: bool = False, **kwargs) -> Optional[AutoTestDataCreateModel]:
        """
        根据文件哈希查询生成记录。

        :param file_hash: 接口文件哈希代码
        :param on_error: 未找到时是否抛出异常
        :param kwargs: 额外过滤条件
        :return: 生成记录实例或None
        """
        if not (file_hash or "").strip():
            error_message: str = "查询接口文件生成记录失败, 参数[file_hash]不允许为空"
            LOGGER.error(error_message)
            raise ParameterException(message=error_message)

        instance = await self.model.filter(file_hash=file_hash.strip(), **kwargs).first()
        if not instance and on_error:
            error_message: str = f"查询接口文件生成记录失败, 记录[file_hash={file_hash}]不存在"
            LOGGER.error(error_message)
            raise NotFoundException(message=error_message)
        return instance

    @staticmethod
    def _normalize_create_status(value: Any) -> int:
        """将字符串/数字状态值规范化为整数。"""
        try:
            return int(value)
        except (TypeError, ValueError) as e:
            raise ParameterException(message=f"参数[create_status]必须是整数, 当前值: {value}") from e

    async def create_data_create(self, data_in: AutoTestDataCreateCreate) -> AutoTestDataCreateModel:
        """
        创建接口文件生成记录；相同file_hash已存在时更新为提交状态。

        :param data_in: 创建入参
        :return: 生成记录实例
        """
        data_dict: Dict[str, Any] = data_in.model_dump(exclude_none=True, exclude_unset=True)
        data_dict["create_status"] = self._normalize_create_status(data_dict.get("create_status", 0))

        existing = await self.get_by_hash(file_hash=data_dict.get("file_hash", ""), on_error=False, state__not=1)
        if existing:
            update_in = AutoTestDataCreateUpdate(
                data_create_id=existing.id,
                create_status="0",
                file_path=data_dict.get("file_path"),
            )
            return await self.update_data_create(data_in=update_in)

        try:
            return await self.create(obj_in=data_dict)
        except IntegrityError as e:
            error_message: str = f"新增接口文件生成记录异常, 违反约束规则: {e}"
            LOGGER.error(f"{error_message}\n{traceback.format_exc()}")
            raise DataBaseStorageException(message=error_message) from e

    async def update_data_create(self, data_in: AutoTestDataCreateUpdate) -> AutoTestDataCreateModel:
        """
        根据主键ID更新接口文件生成记录。

        :param data_in: 更新入参
        :return: 更新后的生成记录实例
        """
        data_dict: Dict[str, Any] = data_in.model_dump(exclude_none=True, exclude_unset=True, exclude={"data_create_id"})
        if "create_status" in data_dict:
            data_dict["create_status"] = self._normalize_create_status(data_dict["create_status"])

        try:
            return await self.update(id=data_in.data_create_id, obj_in=data_dict)
        except DoesNotExist as e:
            error_message: str = f"更新接口文件生成记录失败, 记录[id={data_in.data_create_id}]不存在, 错误描述: {e}"
            LOGGER.error(f"{error_message}\n{traceback.format_exc()}")
            raise NotFoundException(message=error_message) from e
        except IntegrityError as e:
            error_message: str = f"更新接口文件生成记录异常, 违反约束规则: {e}"
            LOGGER.error(f"{error_message}\n{traceback.format_exc()}")
            raise DataBaseStorageException(message=error_message) from e

    async def delete_data_create(self, create_code: str) -> AutoTestDataCreateModel:
        """
        根据create_code软删除接口文件生成记录。

        :param create_code: 接口文件标识代码
        :return: 软删除后的生成记录实例
        """
        if not (create_code or "").strip():
            error_message: str = "删除接口文件生成记录失败, 参数[create_code]不允许为空"
            LOGGER.error(error_message)
            raise ParameterException(message=error_message)

        instance = await self.get_by_code(create_code=create_code.strip(), on_error=False, state__not=1)
        if not instance:
            error_message: str = f"根据[create_code={create_code}]条件检查失败, 接口文件生成记录不存在"
            LOGGER.error(error_message)
            raise NotFoundException(message=error_message)

        return await self.soft_delete(id=instance.id)

# -*- coding: utf-8 -*-
"""
@Author  : yangkai
@Email   : 807440781@qq.com
@Project : Krun
@Module  : autotest_env_crud
@DateTime: 2026/1/2 17:42
"""
import traceback
from collections import defaultdict
from datetime import datetime
from typing import Optional, Dict, Any, List, Tuple, Union

from tortoise.exceptions import IntegrityError, FieldError, DoesNotExist
from tortoise.expressions import Q

from applications.aotutest.models.autotest_model import (
    AutoTestApiEnvEnumInfo,
    AutoTestApiEnvConfigInfo,
    AutoTestApiProjectInfo,
)
from applications.aotutest.schemas.autotest_env_schema import (
    AutoTestApiEnvCreate,
    AutoTestApiEnvUpdate,
    AutoTestApiEnvDelete,
    EnvCreate,
    EnvEditRequest,
)
from applications.base.services.scaffold import ScaffoldCrud
from configure import LOGGER
from core.exceptions import (
    NotFoundException,
    ParameterException,
    DataBaseStorageException,
    DataAlreadyExistsException,
)
from enums import AutoTestConfigNodeType

# env_type(1/2/3) 与 config_type(api/file/database) 双向映射
ENV_TYPE_TO_CONFIG_TYPE = {
    1: AutoTestConfigNodeType.API.value,
    2: AutoTestConfigNodeType.FILE.value,
    3: AutoTestConfigNodeType.DB.value,
}
CONFIG_TYPE_TO_ENV_TYPE = {
    AutoTestConfigNodeType.API.value: 1,
    AutoTestConfigNodeType.FILE.value: 2,
    AutoTestConfigNodeType.DB.value: 3,
}
CONFIG_TYPE_TO_LABEL = {
    AutoTestConfigNodeType.API.value: "APP",
    AutoTestConfigNodeType.FILE.value: "FILE",
    AutoTestConfigNodeType.DB.value: "DB",
}


def resolve_config_type(env_type: int) -> str:
    """
    将节点类型编码转换为 config_type 枚举值。

    :param env_type: 1=APP, 2=FILE, 3=DB
    :return: api/file/database
    :raises ParameterException: env_type非法
    """
    config_type = ENV_TYPE_TO_CONFIG_TYPE.get(env_type)
    if not config_type:
        raise ParameterException(message=f"节点类型[{env_type}]不被允许, 仅支持1:APP/2:FILE/3:DB")
    return config_type


def enum_field_value(value: Any) -> str:
    """兼容 CharEnumField 返回枚举实例或字符串。"""
    return value.value if hasattr(value, "value") else str(value)


async def resolve_env_api_base_host_port(project_id: int, env_name: str) -> Tuple[str, Optional[str]]:
    """
    根据全局环境枚举名与应用解析API的host/port。

    :param project_id: 应用主键ID
    :param env_name: 环境枚举名称
    :return: (host, port)；port可为空
    :raises ParameterException: env_name为空
    :raises NotFoundException: 环境枚举或API配置不存在
    """
    pid = int(project_id)
    name = (env_name or "").strip()
    if not name:
        error_message: str = "参数[env_name]不允许为空"
        LOGGER.error(error_message)
        raise ParameterException(message=error_message)

    env_row = await AutoTestApiEnvEnumInfo.filter(env_name__iexact=name, state__not=1).first()
    if not env_row:
        error_message: str = f"查询环境枚举失败, 记录[env_name={name}]不存在"
        LOGGER.error(error_message)
        raise NotFoundException(message=error_message)

    cfg = (
        await AutoTestApiEnvConfigInfo.filter(
            project_id=pid,
            env_id=env_row.id,
            config_type=AutoTestConfigNodeType.API.value,
            state__not=1,
        )
        .order_by("id")
        .first()
    )
    if not cfg or not str(cfg.config_host or "").strip():
        error_message: str = (
            f"未找到可用的API环境配置, 查询条件: [project_id={pid}, env_id={env_row.id}, config_type={AutoTestConfigNodeType.API.value}]"
        )
        LOGGER.error(error_message)
        raise NotFoundException(message=error_message)
    host = str(cfg.config_host).strip().rstrip("/").rstrip(":")
    port_raw = getattr(cfg, "config_port", None)
    if port_raw is None or str(port_raw).strip() == "":
        return host, None
    return host, str(port_raw).strip()


class AutoTestApiEnvEnumCrud(ScaffoldCrud[AutoTestApiEnvEnumInfo, AutoTestApiEnvCreate, AutoTestApiEnvUpdate]):

    def __init__(self):
        super().__init__(model=AutoTestApiEnvEnumInfo)

    async def get_by_id(self, env_id: int, on_error: bool = False, **kwargs) -> Optional[AutoTestApiEnvEnumInfo]:
        """
        根据主键ID查询环境枚举。

        :param env_id: 环境枚举主键
        :param on_error: 未找到时是否抛出NotFoundException
        :param kwargs: 额外过滤条件
        :return: 环境枚举实例或None
        :raises ParameterException: env_id为空
        :raises NotFoundException: on_error为True且记录不存在
        """
        if not env_id:
            error_message: str = "查询环境枚举信息失败, 参数[env_id]不允许为空"
            LOGGER.error(error_message)
            raise ParameterException(message=error_message)

        instance = await self.get_or_none(id=env_id, **kwargs)
        if not instance and on_error:
            error_message: str = f"查询环境枚举信息失败, 记录[id={env_id}]不存在"
            LOGGER.error(error_message)
            raise NotFoundException(message=error_message)
        return instance

    async def get_by_code(self, env_code: str, on_error: bool = False, **kwargs) -> Optional[AutoTestApiEnvEnumInfo]:
        """
        根据标识代码查询环境枚举。

        :param env_code: 环境标识代码
        :param on_error: 未找到时是否抛出NotFoundException
        :param kwargs: 额外过滤条件
        :return: 环境枚举实例或None
        :raises ParameterException: env_code为空
        :raises NotFoundException: on_error为True且记录不存在
        """
        if not env_code:
            error_message: str = "查询环境枚举信息失败, 参数[env_code]不允许为空"
            LOGGER.error(error_message)
            raise ParameterException(message=error_message)

        instance = await self.model.filter(env_code=env_code, **kwargs).first()
        if not instance and on_error:
            error_message: str = f"查询环境枚举信息失败, 记录[code={env_code}]不存在"
            LOGGER.error(error_message)
            raise NotFoundException(message=error_message)
        return instance

    async def get_by_name(self, env_name: str, on_error: bool = False, **kwargs) -> Optional[AutoTestApiEnvEnumInfo]:
        """
        根据名称查询环境枚举。

        :param env_name: 环境枚举名称
        :param on_error: 未找到时是否抛出NotFoundException
        :param kwargs: 额外过滤条件
        :return: 环境枚举实例或None
        :raises ParameterException: env_name为空
        :raises NotFoundException: on_error为True且记录不存在
        """
        if not env_name:
            error_message: str = "查询环境枚举信息失败, 参数[env_name]不允许为空"
            LOGGER.error(error_message)
            raise ParameterException(message=error_message)

        instance = await self.model.filter(env_name=env_name, **kwargs).first()
        if not instance and on_error:
            error_message: str = f"查询环境枚举信息失败, 记录[env_name={env_name}]不存在"
            LOGGER.error(error_message)
            raise NotFoundException(message=error_message)
        return instance

    async def create_env(self, env_in: AutoTestApiEnvCreate) -> AutoTestApiEnvEnumInfo:
        """
        创建环境枚举；同名已存在则恢复并更新。

        :param env_in: 环境枚举创建schema
        :return: 创建或恢复后的环境枚举实例
        :raises DataBaseStorageException: 违反数据库约束或记录异常丢失
        """
        env_name: str = env_in.env_name
        # 业务层验证：检查环境枚举名称是否存在
        env_dict: Dict[str, Any] = env_in.model_dump(exclude_none=True, exclude_unset=True)
        existing_env: Optional[AutoTestApiEnvEnumInfo] = await self.model.filter(env_name=env_name).first()
        if not existing_env:
            try:
                instance: AutoTestApiEnvEnumInfo = await self.create(obj_in=env_dict)
                return instance
            except IntegrityError as e:
                error_message: str = f"新增环境枚举信息异常, 违反约束规则: {e}"
                LOGGER.error(f"{error_message}\n{traceback.format_exc()}")
                raise DataBaseStorageException(message=error_message) from e

        try:
            env_dict["state"] = 0
            instance: AutoTestApiEnvEnumInfo = await self.update(id=existing_env.id, obj_in=env_dict)
            return instance
        except (DoesNotExist, IntegrityError) as e:
            error_message: str = f"新增(更新)环境枚举信息异常, 违反约束规则或空指针异常: {e}"
            LOGGER.error(f"{error_message}\n{traceback.format_exc()}")
            raise DataBaseStorageException(message=error_message) from e

    async def update_env(self, env_in: AutoTestApiEnvUpdate) -> AutoTestApiEnvEnumInfo:
        """
        更新环境枚举，根据env_id或env_code定位。

        :param env_in: 环境枚举更新schema
        :return: 更新后的环境枚举实例
        :raises NotFoundException: 环境枚举不存在
        :raises DataBaseStorageException: 违反约束
        """
        env_id: Optional[int] = env_in.env_id
        env_code: Optional[str] = env_in.env_code

        # 业务层验证：检查环境信息是否存在
        if env_id:
            instance = await self.get_by_id(env_id=env_id, on_error=True, state__not=1)
            env_code: str = instance.env_code
        else:
            instance = await self.get_by_code(env_code=env_code, on_error=True, state__not=1)
            env_id: int = instance.id

        update_dict: Dict[str, Any] = env_in.model_dump(
            exclude_none=True,
            exclude_unset=True,
            exclude={"env_id", "env_code"}
        )
        try:
            instance = await self.update(id=env_id, obj_in=update_dict)
            return instance
        except DoesNotExist as e:
            error_message: str = f"更新环境枚举信息失败, 记录[id={env_id}]或[code={env_code}]不存在, 错误描述: {e}"
            LOGGER.error(f"{error_message}\n{traceback.format_exc()}")
            raise NotFoundException(message=error_message) from e
        except IntegrityError as e:
            error_message: str = f"更新环境枚举信息异常, 违反约束规则: {e}"
            LOGGER.error(f"{error_message}\n{traceback.format_exc()}")
            raise DataBaseStorageException(message=error_message) from e

    async def delete_env(self, env_id: Optional[int] = None, env_code: Optional[str] = None) -> AutoTestApiEnvEnumInfo:
        """
        软删除环境枚举。

        :param env_id: 环境枚举主键，与env_code二选一
        :param env_code: 环境枚举标识代码，与env_id二选一
        :return: 软删除后的环境枚举实例
        :raises NotFoundException: 环境枚举不存在
        """
        # 业务层验证：检查环境信息是否存在
        if env_id:
            instance = await self.get_by_id(env_id=env_id, on_error=True, state__not=1)
        else:
            instance = await self.get_by_code(env_code=env_code, on_error=True, state__not=1)

        instance.state = 1
        await instance.save()
        return instance

    async def delete_envs(self, env_in: AutoTestApiEnvDelete) -> int:
        """
        根据ID或code列表批量软删除环境枚举。

        :param env_in: 环境枚举删除schema
        :return: 更新条数
        :raises ParameterException: env_ids与env_codes均未传
        """
        env_ids: Optional[List[int]] = env_in.env_ids
        env_codes: Optional[List[str]] = env_in.env_codes
        if not env_ids and not env_codes:
            error_message: str = "删除环境枚举信息失败, 参数[env_ids]或[env_codes]不允许为空"
            LOGGER.error(error_message)
            raise ParameterException(message=error_message)
        if env_ids:
            count = await self.model.filter(id__in=env_ids).update(state=1)
        else:
            count = await self.model.filter(env_code__in=env_codes).update(state=1)
        return count

    async def select_envs(self, search: Q, page: int, page_size: int, order: List[str]) -> Tuple[int, List[AutoTestApiEnvEnumInfo]]:
        """
        根据条件分页查询环境枚举列表。

        :param search: Tortoise Q查询条件
        :param page: 页码
        :param page_size: 每页条数
        :param order: 排序字段列表
        :return: (总条数, 当前页记录列表)
        :raises ParameterException: 查询字段非法
        """
        try:
            return await self.list(page=page, page_size=page_size, search=search, order=order)
        except FieldError as e:
            error_message: str = f"查询环境枚举信息异常, 错误描述: {e}"
            LOGGER.error(f"{error_message}\n{traceback.format_exc()}")
            raise ParameterException(message=error_message) from e

    async def get_envs(
            self,
            project_id: Optional[List[int]] = None,
    ) -> Union[Dict[str, List[str]], Dict[int, Dict[str, List[str]]]]:
        """
        按节点类型聚合环境名称。

        :param project_id:
            - None: 返回 {APP/FILE/DB: [env_name, ...]}
            - []: 返回库中全部 project_id 的映射
            - [ids]: 返回指定应用映射（无数据时对应 value 为空字典）
        :return: 聚合后的环境名称结构
        :raises ParameterException: 查询异常
        """
        try:
            base_qs = AutoTestApiEnvConfigInfo.filter(
                state=0,
                config_type__in=list(CONFIG_TYPE_TO_ENV_TYPE.keys()),
            )

            async def _load_env_names(rows: List[Dict[str, Any]]) -> Dict[int, str]:
                env_ids = list({r["env_id"] for r in rows if r.get("env_id")})
                if not env_ids:
                    return {}
                return dict(
                    await AutoTestApiEnvEnumInfo.filter(id__in=env_ids, state__not=1).values_list("id", "env_name")
                )

            if project_id is None:
                rows = await base_qs.values("config_type", "env_id")
                env_name_map = await _load_env_names(rows)
                env_map: Dict[str, set] = defaultdict(set)
                for row in rows:
                    label = CONFIG_TYPE_TO_LABEL.get(enum_field_value(row["config_type"]))
                    name = env_name_map.get(row["env_id"])
                    if label and name:
                        env_map[label].add(name)
                return {et: sorted(names) for et, names in env_map.items()}

            unique_pids: Optional[List[int]] = None
            if not project_id:
                rows = await base_qs.values("project_id", "config_type", "env_id")
            else:
                unique_pids = list(dict.fromkeys(project_id))
                rows = await base_qs.filter(project_id__in=unique_pids).values(
                    "project_id", "config_type", "env_id"
                )

            env_name_map = await _load_env_names(rows)
            grouped: Dict[int, Dict[str, set]] = defaultdict(lambda: defaultdict(set))
            for row in rows:
                label = CONFIG_TYPE_TO_LABEL.get(enum_field_value(row["config_type"]))
                name = env_name_map.get(row["env_id"])
                if label and name:
                    grouped[row["project_id"]][label].add(name)

            result: Dict[int, Dict[str, List[str]]] = {}
            target_pids = unique_pids if unique_pids is not None else sorted(grouped.keys())
            for pid in target_pids:
                result[pid] = {et: sorted(names) for et, names in grouped.get(pid, {}).items()}
            return result
        except Exception as e:
            error_message = f"查询环境信息异常, 错误描述: {e}"
            LOGGER.error(f"{error_message}\n{traceback.format_exc()}")
            raise ParameterException(message=error_message) from e

    async def get_env_search_list(
            self,
            project_id: Optional[int] = None,
            env_name: Optional[str] = None,
            env_type: Optional[int] = None,
            ip: Optional[str] = None,
            page: int = 1,
            page_size: int = 10,
    ) -> Tuple[int, List[Dict[str, Any]]]:
        """
        按 (project_id, env_name, env_type) 聚合配置后分页返回。

        :return: (总条数, 当前页记录)；记录含 id/project_id/env_name/env_type/project_name/is_delete/时间字段
        :raises ParameterException: 查询异常或节点类型非法
        """
        try:
            config_qs = AutoTestApiEnvConfigInfo.filter(
                state=0,
                config_type__in=list(CONFIG_TYPE_TO_ENV_TYPE.keys()),
            )
            if project_id is not None:
                config_qs = config_qs.filter(project_id=project_id)
            if env_type is not None:
                config_qs = config_qs.filter(config_type=resolve_config_type(env_type))
            if ip:
                config_qs = config_qs.filter(config_host__contains=ip)

            rows = await config_qs.values(
                "project_id", "env_id", "config_type", "created_time", "updated_time"
            )
            if not rows:
                return 0, []

            env_ids = list({r["env_id"] for r in rows})
            env_rows = await AutoTestApiEnvEnumInfo.filter(id__in=env_ids, state__not=1).values(
                "id", "env_name", "created_time", "updated_time"
            )
            env_map = {r["id"]: r for r in env_rows}
            if env_name:
                keyword = env_name.upper()
                env_map = {
                    eid: erow for eid, erow in env_map.items()
                    if keyword in (erow.get("env_name") or "").upper()
                }
                rows = [r for r in rows if r["env_id"] in env_map]

            groups: Dict[Tuple[int, int, str], Dict[str, Any]] = {}
            for row in rows:
                erow = env_map.get(row["env_id"])
                if not erow:
                    continue
                ctype = enum_field_value(row["config_type"])
                key = (row["project_id"], row["env_id"], ctype)
                if key not in groups:
                    groups[key] = {
                        "id": str(row["env_id"]),
                        "project_id": str(row["project_id"]),
                        "env_name": erow["env_name"],
                        "env_type": CONFIG_TYPE_TO_ENV_TYPE.get(ctype, 1),
                        "created_time": row["created_time"] or erow.get("created_time"),
                        "updated_time": row["updated_time"] or erow.get("updated_time"),
                    }
                    continue
                nxt = row["updated_time"]
                cur = groups[key]["updated_time"]
                if cur and nxt and nxt > cur:
                    groups[key]["updated_time"] = nxt

            aggregated = sorted(
                groups.values(),
                key=lambda x: x.get("updated_time") or datetime.min,
                reverse=True,
            )
            total = len(aggregated)
            page_items = aggregated[(page - 1) * page_size: page * page_size]

            project_ids = [int(item["project_id"]) for item in page_items]
            project_map = {}
            if project_ids:
                project_map = dict(
                    await AutoTestApiProjectInfo.filter(id__in=project_ids, state=0).values_list("id", "project_name")
                )

            # 列表项均由配置聚合得到，存在子配置，故 is_delete 恒为 False
            result = [
                {
                    "id": item["id"],
                    "project_id": item["project_id"],
                    "env_name": item["env_name"],
                    "env_type": item["env_type"],
                    "created_time": item["created_time"],
                    "updated_time": item["updated_time"],
                    "project_name": project_map.get(int(item["project_id"]), ""),
                    "is_delete": False,
                }
                for item in page_items
            ]
            return total, result
        except ParameterException:
            raise
        except Exception as e:
            error_message = f"查询环境搜索列表异常, 错误描述: {e}"
            LOGGER.error(f"{error_message}\n{traceback.format_exc()}")
            raise ParameterException(message=error_message) from e

    async def create_automation_env(self, data: EnvCreate, user: str = "system") -> Dict[str, Any]:
        """
        确保环境枚举存在；同应用+环境+类型下已有配置则判重。

        :param data: 环境创建入参
        :param user: 操作人
        :return: 与历史接口一致的环境响应字典
        """
        try:
            config_type = resolve_config_type(data.env_type)
            from applications.aotutest.services.autotest_project_crud import AutoTestApiProjectCrud
            await AutoTestApiProjectCrud().get_by_id(project_id=data.project_id, on_error=True, state__not=1)

            env_name = data.env_name.upper()
            existing_env = await self.model.filter(env_name=env_name).first()
            if existing_env and existing_env.state == 0:
                exists_cfg = await AutoTestApiEnvConfigInfo.filter(
                    project_id=data.project_id,
                    env_id=existing_env.id,
                    config_type=config_type,
                    state=0,
                ).exists()
                if exists_cfg:
                    raise DataAlreadyExistsException(
                        message=f"应用：{data.project_id}+环境{env_name}+类型{data.env_type}已存在，不能重复新增"
                    )

            env_instance = await self.create_env(
                AutoTestApiEnvCreate(env_name=env_name, created_user=user)
            )
            now = env_instance.updated_time or env_instance.created_time or datetime.now()
            return {
                "id": env_instance.id,
                "project_id": data.project_id,
                "env_name": env_instance.env_name,
                "env_type": data.env_type,
                "updated_time": now,
                "created_time": env_instance.created_time or now,
            }
        except (DataAlreadyExistsException, ParameterException, NotFoundException):
            raise
        except Exception as e:
            error_message = f"新增环境失败：{e}"
            LOGGER.error(f"{error_message}\n{traceback.format_exc()}")
            raise ParameterException(message=error_message) from e

    async def update_automation_env(self, data: EnvEditRequest, user: str = "system") -> Dict[str, Any]:
        """
        更新环境枚举名称，并级联同类型配置的 env_id / project_id。

        :param data: 环境编辑入参
        :param user: 操作人
        :return: 环境响应字典
        """
        try:
            config_type = resolve_config_type(data.env_type)
            from applications.aotutest.services.autotest_project_crud import AutoTestApiProjectCrud
            await AutoTestApiProjectCrud().get_by_id(project_id=data.project_id, on_error=True, state__not=1)

            existing = await self.get_by_id(env_id=data.id, on_error=True, state__not=1)
            new_env_name = data.env_name.upper()
            target_env = await self.model.filter(env_name=new_env_name, state__not=1).first()

            if target_env and target_env.id != existing.id:
                dup = await AutoTestApiEnvConfigInfo.filter(
                    project_id=data.project_id,
                    env_id=target_env.id,
                    config_type=config_type,
                    state=0,
                ).exists()
                if dup:
                    raise DataAlreadyExistsException(
                        message=f"应用：{data.project_id}+环境：{new_env_name}+类型：{data.env_type}已经存在，不能重复"
                    )
                await AutoTestApiEnvConfigInfo.filter(
                    env_id=existing.id,
                    config_type=config_type,
                    state=0,
                ).update(env_id=target_env.id, project_id=data.project_id, updated_user=user)
                env_instance = target_env
            else:
                if existing.env_name != new_env_name:
                    existing.env_name = new_env_name
                    existing.updated_user = user
                    await existing.save()
                await AutoTestApiEnvConfigInfo.filter(
                    env_id=existing.id,
                    config_type=config_type,
                    state=0,
                ).update(project_id=data.project_id, updated_user=user)
                env_instance = existing

            return {
                "id": env_instance.id,
                "project_id": data.project_id,
                "env_name": env_instance.env_name,
                "env_type": data.env_type,
                "updated_time": env_instance.updated_time or datetime.now(),
                "created_time": env_instance.created_time or datetime.now(),
            }
        except (DataAlreadyExistsException, ParameterException, NotFoundException):
            raise
        except Exception as e:
            error_message = f"编辑环境失败：异常信息{e}"
            LOGGER.error(f"{error_message}\n{traceback.format_exc()}")
            raise ParameterException(message=error_message) from e

    async def delete_automation_env(self, env_id: int, env_type: int, user: str = "system") -> Dict[str, Any]:
        """
        软删指定枚举下某节点类型的全部配置；若该枚举已无启用配置则软删枚举本身。

        :param env_id: 环境枚举ID
        :param env_type: 节点类型
        :param user: 操作人
        :return: 环境响应字典
        """
        try:
            config_type = resolve_config_type(env_type)
            existing = await self.get_by_id(env_id=env_id, on_error=True, state__not=1)

            config_qs = AutoTestApiEnvConfigInfo.filter(env_id=env_id, config_type=config_type, state=0)
            first_cfg = await config_qs.first()
            project_id = first_cfg.project_id if first_cfg else 0
            await config_qs.update(state=1, updated_user=user)

            remain = await AutoTestApiEnvConfigInfo.filter(env_id=env_id, state=0).count()
            if remain == 0:
                existing.state = 1
                existing.updated_user = user
                await existing.save()

            return {
                "id": existing.id,
                "project_id": project_id,
                "env_name": existing.env_name,
                "env_type": env_type,
                "updated_time": existing.updated_time or datetime.now(),
                "created_time": existing.created_time or datetime.now(),
            }
        except (ParameterException, NotFoundException):
            raise
        except Exception as e:
            error_message = f"删除环境失败，异常信息：{e}"
            LOGGER.error(f"{error_message}\n{traceback.format_exc()}")
            raise ParameterException(message=error_message) from e

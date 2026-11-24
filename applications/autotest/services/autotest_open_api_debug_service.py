import httpx

from applications.autotest.dependencies import AutoTestServices
from applications.autotest.schemas.autotest_open_api_step_schema import AutoTestOpenApiDebugRequest
from applications.autotest.services.autotest_runtime.protocol_http import format_byte_size
from applications.autotest.services.autotest_runtime.protocol_open_api import (
    RELAY_SECRET_VARIABLES,
    build_relay_payload,
    decode_relay_response,
    prepare_business_body,
    relay_response_text,
    relay_url,
)
from applications.autotest.services.autotest_scope_guard import CASE_TAG, assert_parent_readable
from applications.autotest.services.autotest_step_debug_service import StepDebugException, StepDebugService
from applications.autotest.services.autotest_tool_service import AutoTestToolService
from common.url_utils import build_absolute_http_url
from core.exceptions import NotFoundException
from enums import AutoTestConfigNodeType, AutoTestStepType
from services.data_scope import DataScope


class OpenApiStepDebugService(StepDebugService):
    @classmethod
    async def debug_open_api(
            cls,
            debug_in: AutoTestOpenApiDebugRequest,
            services: AutoTestServices,
            scope: DataScope | None,
    ) -> dict:
        logger = cls.make_debug_logger(debug_in.step_name)
        endpoint = await cls.resolve_env_config_by_mode(
            services,
            project_id=debug_in.request_project_id,
            env_id=debug_in.env_id,
            config_mode=debug_in.request_config_mode,
            config_type=AutoTestConfigNodeType.APP,
            label="openapi加解密调试失败",
            scope=scope,
        )
        dataset = None
        if debug_in.dataset_name:
            case = await services.case_curd.model.filter(id=debug_in.case_id, state__not=1).first()
            if not case:
                raise NotFoundException(message="使用测试场景前请保存脚本")
            await assert_parent_readable(scope, CASE_TAG, case, "用例")
            if not await services.step_curd.model.filter(
                case_id=case.id, step_code=debug_in.step_code,
                step_type=AutoTestStepType.OPEN_API, state__not=1,
            ).exists():
                raise NotFoundException(message="使用测试场景前请保存 openapi加解密 步骤")
            dataset = await AutoTestToolService.load_dataset_for_request_step(
                case_id=case.id, step_code=debug_in.step_code,
                dataset_name=debug_in.dataset_name, executing_quote_case_id=None,
            )
            if dataset is None:
                raise NotFoundException(message="当前步骤没有所选测试场景")

        local_vars = debug_in.defined_variables or []
        lookup, initial = cls.merge_debug_variables(
            [item for item in local_vars if item.key not in RELAY_SECRET_VARIABLES],
            [item for item in debug_in.session_variables or [] if item.key not in RELAY_SECRET_VARIABLES],
        )
        path = debug_in.request_url.strip()
        cls.inject_builtin_variables_for_debug(
            initial, lookup, target_host=endpoint.config_host, target_port=endpoint.config_port,
            target_path=path, log=logger.append,
        )
        variables = cls.resolve_placeholders_into_pool(initial, lookup, logger.append)

        def resolve(value, log=logger.append):
            return AutoTestToolService.resolve_placeholders(
                value=value, logger_object=log, finished_variables=variables,
            )

        try:
            body = prepare_business_body(debug_in.request_body, dataset, resolve, logger.append)
            relay_variables = {**lookup, **{
                item.key: resolve(item.value, lambda _: None)
                for item in local_vars if item.key in RELAY_SECRET_VARIABLES
            }}
            payload = build_relay_payload(endpoint.config_host, endpoint.config_port, path, body, relay_variables)
            url = relay_url()
            logger.append(f"中转请求 POST，业务目标 {payload['ip']}:{payload['port']}{path}")
            async with httpx.AsyncClient(timeout=10.0, trust_env=False) as client:
                response = await client.post(url, json=payload)
            data = decode_relay_response(response)
        except httpx.TimeoutException as exc:
            raise StepDebugException("中转请求超时，业务执行结果未知") from exc
        except httpx.RequestError as exc:
            raise StepDebugException("无法连接中转服务，请检查 OPEN_API_RELAY_URL") from exc
        except (ValueError, TypeError) as exc:
            raise StepDebugException(str(exc)) from exc

        response_text = relay_response_text(data)
        logger.append(f"中转 HTTP {response.status_code}，{response_text if data is None else '已取得业务响应'}")
        headers = dict(response.headers)
        cookies = {cookie.name: cookie.value for cookie in response.cookies.jar}
        extract_results, validator_results = AutoTestToolService.run_extract_and_assert(
            extract_variables=debug_in.extract_variables,
            assert_validators=debug_in.assert_validators,
            response_text=response_text,
            response_json=data,
            response_headers=headers,
            response_cookies=cookies,
            request_json=body,
            request_text=payload["jsonStr"],
            session_variables_lookup=lookup,
            finished_variables=variables,
            log_callback=logger.append,
            step_struct=dataset,
            is_core_engine=False,
            raise_on_failure=False,
        )
        return cls.pack_debug_result(
            status=response.status_code, headers=headers, cookies=cookies, data=data if data is not None else response_text,
            elapsed=f"{response.elapsed.total_seconds():.2f}", size=format_byte_size(len(response.content)),
            extract_results=extract_results, validator_results=validator_results, logs=logger.logs,
            request_info={
                "url": build_absolute_http_url(payload["ip"], payload["port"], path),
                "method": "POST", "body_type": "json", "body": body,
            },
        )

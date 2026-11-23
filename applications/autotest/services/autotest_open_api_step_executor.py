from applications.autotest.services.autotest_runtime.protocol_open_api import (
    RELAY_SECRET_VARIABLES,
    build_relay_payload,
    decode_relay_response,
    prepare_business_body,
    relay_response_text,
    relay_url,
)
from applications.autotest.services.autotest_step_engine import HttpStepExecutor, StepExecutionError, StepExecutionResult
from applications.autotest.services.autotest_tool_service import AutoTestToolService
from common.url_utils import build_absolute_http_url
from enums import AutoTestConfigNodeType, AutoTestReqArgsType, HTTPMethod


class OpenApiStepExecutor(HttpStepExecutor):
    excluded_defined_variable_keys = RELAY_SECRET_VARIABLES

    async def _execute(self, result: StepExecutionResult) -> None:
        config = self.get_execute_config(expected_config_type=AutoTestConfigNodeType.APP)
        if not config or not config.env_name:
            raise StepExecutionError("openapi加解密缺少 APP 执行环境")

        dataset = await self.load_step_dataset(result)
        path = (self.step.request_url or "").strip()
        try:
            body = prepare_business_body(
                self.step.request_body, dataset, self.context.resolve_placeholders, self.context.log,
            )
            variables = {item.key: item.value for item in self.context.defined_variables}
            for item in self.step.defined_variables or []:
                if item.key in RELAY_SECRET_VARIABLES:
                    variables[item.key] = AutoTestToolService.resolve_placeholders(
                        value=item.value, logger_object=lambda _: None,
                        is_core_engine=True, finished_variables=self.context,
                    )
            payload = build_relay_payload(config.config_host, config.config_port, path, body, variables)
            url = relay_url()
        except (ValueError, TypeError) as exc:
            self.context.log(f"【openapi加解密】{exc}")
            raise StepExecutionError(str(exc)) from exc

        business_url = build_absolute_http_url(payload["ip"], payload["port"], path)
        result.request = {
            "request_url": business_url,
            "request_method": HTTPMethod.POST.value,
            "request_args_type": AutoTestReqArgsType.JSON,
            "request_body": body,
            "request_env_name": config.env_name,
            "request_config_name": config.config_name,
        }
        self.context.log(f"【openapi加解密】中转请求 POST，业务目标 {business_url}")
        response = await self.context.send_http_request(HTTPMethod.POST.value, url, json_data=payload)
        response_headers = dict(response.headers)
        response_cookies = {cookie.name: cookie.value for cookie in response.cookies.jar}
        result.response = {
            "response_code": response.status_code,
            "response_header": response_headers,
            "response_cookie": response_cookies,
            "response_elapsed": f"{response.elapsed.total_seconds():.2f}",
        }
        try:
            data = decode_relay_response(response)
        except ValueError as exc:
            self.context.log(f"【openapi加解密】{exc}")
            raise StepExecutionError(str(exc)) from exc
        response_text = relay_response_text(data)
        result.response.update(response_body=data, response_text=response_text)
        self.apply_extract_and_assert(
            result,
            step_label="openapi加解密",
            response_text=response_text,
            response_json=data,
            response_headers=response_headers,
            response_cookies=response_cookies,
            request_text=payload["jsonStr"],
            request_json=body,
            step_struct=dataset,
        )

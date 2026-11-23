from copy import deepcopy
from typing import Any, Callable

import httpx
import orjson

from applications.autotest.services.autotest_tool_service import AutoTestToolService
from applications.autotest.services.autotest_runtime.builtin_variables import strip_host_scheme
from configure import PROJECT_CONFIG


RELAY_VARIABLES = ("appId", "appSecret", "custNo", "privateKey", "publicKey", "timestamp")
RELAY_SECRET_VARIABLES = frozenset({"appSecret", "privateKey"})
OPTIONAL_RELAY_VARIABLES = {"custNo", "timestamp"}


def relay_url() -> str:
    url = httpx.URL(PROJECT_CONFIG.OPEN_API_RELAY_URL.strip())
    if url.scheme not in ("http", "https") or not url.host or url.userinfo:
        raise ValueError("请设置有效的 OPEN_API_RELAY_URL 中转地址")
    return str(url)


def prepare_business_body(
    raw: Any,
    dataset: dict | None,
    resolve: Callable[[Any], Any],
    log: Callable[[str], None],
) -> dict | list:
    body = AutoTestToolService.try_serialize_request_body(raw)
    if not isinstance(body, (dict, list)):
        raise ValueError("请求体必须是有效的 JSON 对象或数组")
    if dataset and dataset.get("body"):
        body = AutoTestToolService.replace_json_datagram(
            body_map=dataset["body"], request_body=deepcopy(body), logger_object=log,
        )["request_body"]
    return resolve(body)


def build_relay_payload(
    host: str | None, port: str | None, path: str, body: dict | list, variables: dict,
) -> dict:
    ip = strip_host_scheme(host or "").strip()
    if not ip or any(char in ip for char in "/?#@"):
        raise ValueError("执行环境缺少有效的业务主机")
    if not port or not str(port).isascii() or not str(port).isdigit() or not 1 <= int(port) <= 65535:
        raise ValueError("执行环境缺少有效的业务端口")
    if not path.startswith("/") or path.startswith("//"):
        raise ValueError("业务路径必须是以 / 开头的相对路径")
    fields = {"timestamp": ""}
    for name in RELAY_VARIABLES:
        value = variables.get(name)
        if name in OPTIONAL_RELAY_VARIABLES and (value is None or (isinstance(value, str) and not value.strip())):
            continue
        if name == "timestamp" and type(value) is int:
            value = str(value)
        if not isinstance(value, str) or not value.strip() or "${" in value:
            raise ValueError(f"请填写有效的步骤变量 {name}")
        fields[name] = value
    return {
        **fields,
        "ip": ip,
        "port": int(port),
        "url": path,
        "jsonStr": orjson.dumps(body).decode("utf-8"),
        "requestId": "",
    }


def decode_relay_response(response: httpx.Response) -> dict | list | None:
    if not response.is_success:
        raise ValueError(f"中转 HTTP 请求失败（状态码 {response.status_code}）")
    try:
        envelope = response.json()
    except ValueError as exc:
        raise ValueError("中转响应不是有效的 JSON") from exc
    if not isinstance(envelope, dict):
        raise ValueError("中转响应必须是 JSON 对象")
    if envelope.get("retCd") != "000000":
        raise ValueError("中转服务返回失败，请检查中转服务日志")
    data = envelope.get("data")
    if data is None or (isinstance(data, str) and not data.strip()):
        return None
    try:
        data = orjson.loads(data) if isinstance(data, str) else data
    except ValueError as exc:
        raise ValueError("中转响应 data 无法解析为业务 JSON") from exc
    if data is not None and not isinstance(data, (dict, list)):
        raise ValueError("中转响应 data 缺少业务 JSON 对象或数组")
    return data or None


def relay_response_text(data: dict | list | None) -> str:
    return "openApi服务器未返回值" if data is None else orjson.dumps(data).decode("utf-8")

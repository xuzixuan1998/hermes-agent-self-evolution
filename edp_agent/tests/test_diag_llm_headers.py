"""diag_llm.py 的 header 与解密支持测试。

只测纯函数（_load_env / _build_headers / _resolve_decrypt），不发真实 HTTP 请求。
对齐 EDPAgent config.py:_build_custom_headers 的行为。
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest


# 把 scripts/ 加 sys.path 让 import diag_llm 可行
_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(_SCRIPTS))


@pytest.fixture(autouse=True)
def _isolate_env():
    """每个 test 都用干净的 PLANNING_AGENT_MODEL_* 环境变量。"""
    old = {}
    keys = [k for k in os.environ if k.startswith("PLANNING_AGENT_MODEL_")]
    for k in keys:
        old[k] = os.environ.pop(k)
    yield
    for k in keys:
        os.environ[k] = old[k]


def _identity(v: str) -> str:
    return v


def _set_required():
    os.environ["PLANNING_AGENT_MODEL_BASE_URL"] = "https://x/v1"
    os.environ["PLANNING_AGENT_MODEL_API_KEY"] = "sk-test"
    os.environ["PLANNING_AGENT_MODEL_NAME"] = "glm-5"


# ── _build_headers ──────────────────────────────────────────────────────────

def test_build_headers_pure_oai():
    """无任何企业 header，只有 Authorization + Content-Type。"""
    from diag_llm import _build_headers
    env = {
        "api_key": "sk-test", "token": "", "token_header": "",
        "user_id": "", "user_id_header": "", "extra_headers": {},
    }
    h = _build_headers(env)
    assert h == {"Authorization": "Bearer sk-test", "Content-Type": "application/json"}


def test_build_headers_with_enterprise_token():
    from diag_llm import _build_headers
    env = {
        "api_key": "sk-test",
        "token": "ent-tok-xyz", "token_header": "X-Auth-Token",
        "user_id": "user-42", "user_id_header": "X-User-Id",
        "extra_headers": {},
    }
    h = _build_headers(env)
    assert h["X-Auth-Token"] == "ent-tok-xyz"
    assert h["X-User-Id"] == "user-42"
    assert h["Authorization"] == "Bearer sk-test"


def test_build_headers_extra_headers_merge():
    """EXTRA_HEADERS 应注入到最终 header dict。"""
    from diag_llm import _build_headers
    env = {
        "api_key": "sk-test", "token": "", "token_header": "",
        "user_id": "", "user_id_header": "",
        "extra_headers": {"X-Tenant": "prod-bank", "X-Trace": "diag-001"},
    }
    h = _build_headers(env)
    assert h["X-Tenant"] == "prod-bank"
    assert h["X-Trace"] == "diag-001"


def test_build_headers_extra_overrides_user_id():
    """extra_headers 在最后 update，可以覆盖 token/user_id（与 EDPAgent 行为一致）。"""
    from diag_llm import _build_headers
    env = {
        "api_key": "sk-test", "token": "", "token_header": "",
        "user_id": "u-default", "user_id_header": "X-User",
        "extra_headers": {"X-User": "u-overridden"},
    }
    h = _build_headers(env)
    assert h["X-User"] == "u-overridden"


# ── _load_env：必填校验 ───────────────────────────────────────────────────

def test_load_env_exits_when_required_missing():
    from diag_llm import _load_env
    # 不 set required vars
    with pytest.raises(SystemExit):
        _load_env(_identity)


def test_load_env_token_without_header_exits():
    """与 EDPAgent config.py 行为一致：TOKEN 设置但 _HEADER 缺失时报错。"""
    from diag_llm import _load_env
    _set_required()
    os.environ["PLANNING_AGENT_MODEL_TOKEN"] = "ent-tok"
    # 故意不 set TOKEN_HEADER
    with pytest.raises(SystemExit):
        _load_env(_identity)


def test_load_env_user_id_without_header_exits():
    from diag_llm import _load_env
    _set_required()
    os.environ["PLANNING_AGENT_MODEL_USER_ID"] = "u-1"
    with pytest.raises(SystemExit):
        _load_env(_identity)


# ── _load_env：EXTRA_HEADERS ────────────────────────────────────────────────

def test_load_env_extra_headers_json_parsed():
    from diag_llm import _load_env
    _set_required()
    os.environ["PLANNING_AGENT_MODEL_EXTRA_HEADERS"] = json.dumps({
        "X-Tenant": "prod", "X-Trace": "abc"
    })
    env = _load_env(_identity)
    assert env["extra_headers"] == {"X-Tenant": "prod", "X-Trace": "abc"}


def test_load_env_extra_headers_invalid_json_does_not_crash():
    from diag_llm import _load_env
    _set_required()
    os.environ["PLANNING_AGENT_MODEL_EXTRA_HEADERS"] = "not-json{"
    env = _load_env(_identity)
    assert env["extra_headers"] == {}


def test_load_env_extra_headers_non_dict_skipped():
    from diag_llm import _load_env
    _set_required()
    os.environ["PLANNING_AGENT_MODEL_EXTRA_HEADERS"] = json.dumps([1, 2, 3])
    env = _load_env(_identity)
    assert env["extra_headers"] == {}


def test_load_env_extra_headers_values_stringified():
    """JSON 里数字 / null 会被转成 str 而不是抛错。"""
    from diag_llm import _load_env
    _set_required()
    os.environ["PLANNING_AGENT_MODEL_EXTRA_HEADERS"] = json.dumps({"X-Tenant": 42})
    env = _load_env(_identity)
    assert env["extra_headers"] == {"X-Tenant": "42"}


# ── 解密 ────────────────────────────────────────────────────────────────────

def test_load_env_calls_decrypt_on_token_and_api_key():
    """加密 token + api_key 必须经过 decrypt() 后再用。"""
    from diag_llm import _load_env
    _set_required()
    os.environ["PLANNING_AGENT_MODEL_API_KEY"] = "ENCRYPTED:abc"
    os.environ["PLANNING_AGENT_MODEL_TOKEN"] = "ENCRYPTED:xyz"
    os.environ["PLANNING_AGENT_MODEL_TOKEN_HEADER"] = "X-Auth"

    seen: list[str] = []
    def _fake_decrypt(v: str) -> str:
        seen.append(v)
        return v.replace("ENCRYPTED:", "DECRYPTED:")

    env = _load_env(_fake_decrypt)
    assert env["api_key"] == "DECRYPTED:abc"
    assert env["token"] == "DECRYPTED:xyz"
    assert "ENCRYPTED:abc" in seen and "ENCRYPTED:xyz" in seen


def test_resolve_decrypt_returns_callable_even_without_common_crypto():
    """没有 common.crypto 时，必须 fallback 到 identity，不能崩。"""
    from diag_llm import _resolve_decrypt
    fn = _resolve_decrypt()
    assert callable(fn)
    assert fn("hello") == "hello"  # identity（容器内可能拿到真解密器，但对未加密值是同值）

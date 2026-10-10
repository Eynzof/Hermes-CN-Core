
import os
os.environ["HERMES_HOME"] = os.environ.get("HERMES_HOME") or r"C:\dev\Hermes-CN-Core\.kimix_cache\tmp_home_g07"
from hermes_cli import runtime_provider as rp
from hermes_cli.auth import AuthError
os.environ["OPENROUTER_API_KEY"] = "test-or-key"
rp.resolve_provider = lambda *a, **k: "qwen-oauth"
def boom(**kw):
    raise AuthError("stale", provider="qwen-oauth", code="qwen_auth_missing")
rp.resolve_qwen_runtime_credentials = boom
rp._get_model_config = dict
print("resolve_requested_provider('auto') ->", rp.resolve_requested_provider("auto"))
for i, r in enumerate(rp._ladder_rungs("auto", None, None, None)):
    print(i, "provider=", (r or {}).get("provider"), "source=", (r or {}).get("source"), "key?", bool((r or {}).get("api_key")))
print("result:", {k: v for k, v in rp.resolve_runtime_provider(requested="auto").items() if k in ("provider","source","api_key","base_url")})

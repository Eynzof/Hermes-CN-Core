
import os, inspect
os.environ.setdefault("HERMES_HOME", r"C:\dev\Hermes-CN-Core\.kimix_cache\tmp_home_g07")
from hermes_cli import runtime_provider as rp
from hermes_cli.auth import AuthError
spec = rp._OAUTH_RUNTIME_PROVIDERS["qwen-oauth"]
print("spec resolve:", spec.resolve, getattr(spec.resolve, "__code__", None) and spec.resolve.__code__.co_names)
print(inspect.getsource(type(spec)))
rp.resolve_provider = lambda *a, **k: "qwen-oauth"
def boom(**kw):
    raise AuthError("stale", provider="qwen-oauth", code="qwen_auth_missing")
rp.resolve_qwen_runtime_credentials = boom
orig_pool = rp._resolve_from_pool
def pool(*a, **k):
    out = orig_pool(*a, **k)
    print("POOL rung ->", (out or {}).get("provider"), (out or {}).get("source"))
    return out
rp._resolve_from_pool = pool
orig_oauth = rp._resolve_oauth_runtime
def oauth(*a, **k):
    print("OAUTH rung entered, spec.resolve =", rp._OAUTH_RUNTIME_PROVIDERS["qwen-oauth"].resolve)
    return orig_oauth(*a, **k)
rp._resolve_oauth_runtime = oauth
rp._get_model_config = dict
try:
    r = rp.resolve_runtime_provider(requested="auto")
    print("result", r.get("provider"), r.get("source"))
except Exception as e:
    print("RAISED", type(e).__name__, e)

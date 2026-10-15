
import os, sys, traceback, json
os.environ.setdefault("HERMES_HOME", r"C:\dev\Hermes-CN-Core\.kimix_cache\tmp_home_g07b")
from pathlib import Path
import hermes_cli.skills_hub as cli_hub
import tools.skills_hub as hub
import tools.skills_hub_search as search
from tools.skills_hub_models import SkillBundle, SkillMeta
import tempfile
tmp_path = Path(tempfile.mkdtemp())
clients = []
client_kwargs = []
class Response:
    status_code = 200
    text = "ok"
    content = b"ok"
    headers = {}
    def json(self):
        return {"skills": []}
class Client:
    def __enter__(self): return self
    def __exit__(self, *_args): return False
    def get(self, *_args, **_kwargs): return Response()
class Source:
    def source_id(self): return "github"
    def inspect(self, _identifier):
        try:
            r = hub._guarded_http_get("https://example.com/metadata")
            print("guarded ->", r)
            idx = search._load_hermes_index()
            print("index ->", idx)
            if idx != {"skills": []}:
                print("INDEX MISMATCH")
        except Exception:
            traceback.print_exc()
        return SkillMeta("example", "metadata", "test", "example/id", "community")
    def fetch(self, _identifier):
        hub._guarded_http_get("https://example.com/SKILL.md")
        return SkillBundle("example", {"SKILL.md": "# Example"}, "test", "example/id", "community")
def create_client(**kwargs):
    client_kwargs.append(kwargs); c = Client(); clients.append(c); return c

# patch without pytest
cli_hub._sources = lambda: [Source()]
hub.create_ssrf_safe_client = create_client
hub.is_safe_url = lambda _url: True
hub.check_website_access = lambda _url: None
search._hermes_index_cache_file = lambda: tmp_path / "hermes-index.json"
def fail(*a, **k):
    raise AssertionError("index fetch bypassed the pool")
search.httpx.get = fail
# don't swallow
orig_try = cli_hub._try
def loud_try(fn, *args):
    return fn(*args)
cli_hub._try = loud_try
try:
    result = cli_hub.inspect_skill("example/id")
    print("RESULT", result)
except Exception:
    traceback.print_exc()
print("clients", len(clients), client_kwargs)

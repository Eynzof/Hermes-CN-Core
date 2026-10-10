
import os, sys, tempfile
from pathlib import Path
import hermes_yaml as yaml
from hermes_cli import profiles
import utils, inspect
print("profiles file", profiles.__file__)
print("utils file", utils.__file__)
src = inspect.getsource(profiles.write_profile_meta)
print(src[-900:])
d = Path(tempfile.mkdtemp())/"coder"; d.mkdir()
profiles.write_profile_meta(d, description="Curated by hand", description_auto=False)
def boom(*a, **k):
    raise RuntimeError("simulated")
yaml.safe_dump = boom
try:
    profiles.write_profile_meta(d, description_auto=True)
    print("NO RAISE")
except RuntimeError as e:
    print("raised", e)
print((d/"profile.yaml").read_text(encoding="utf-8-sig"))

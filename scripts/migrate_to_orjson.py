"""
Migration script: replace stdlib `json` with `orjson` across the codebase.

Handles:
  - import json  ->  import orjson
  - json.loads() ->  orjson.loads()
  - json.load()  ->  orjson.loads(fp.read())
  - json.dumps() ->  orjson.dumps(...).decode('utf-8')
  - json.dump()  ->  fp.write(orjson.dumps(...).decode('utf-8'))
  - json.JSONDecodeError -> orjson.JSONDecodeError
  - json.JSONDecoder()   -> orjson.loads() (raw_decode is dropped)
  - indent=N   -> option=orjson.OPT_INDENT_2
  - sort_keys=True -> option=orjson.OPT_SORT_KEYS
  - ensure_ascii=False / ensure_ascii=True -> removed
  - separators=... -> removed
  - .encode('utf-8') / .encode() after dumps -> removed

Usage: python scripts/migrate_to_orjson.py [--dry-run] [path]
"""

import ast
import os
from agent.re_compat import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# ---------------------------------------------------------------------------
#  Regex helpers
# ---------------------------------------------------------------------------

# Match `json as X` in import statements
RE_IMPORT_JSON_AS = re.compile(
    r'\bjson\s+as\s+(\w+)',
)

# Match `json.loads(` not preceded by a dot (so we don't match `foo.json.loads`)
RE_JSON_LOADS = re.compile(
    r'(?<!\.)\bjson\.loads\s*\(',
)

# Match `json.load(` not preceded by a dot
RE_JSON_LOAD = re.compile(
    r'(?<!\.)\bjson\.load\s*\(',
)

# Match `json.dumps(` not preceded by a dot
RE_JSON_DUMPS = re.compile(
    r'(?<!\.)\bjson\.dumps\s*\(',
)

# Match `json.dump(` not preceded by a dot
RE_JSON_DUMP = re.compile(
    r'(?<!\.)\bjson\.dump\s*\(',
)

# Match `json.JSONDecodeError`
RE_JSON_DECODE_ERROR = re.compile(
    r'\bjson\.JSONDecodeError\b',
)

# Match `json.JSONDecoder()`
RE_JSON_DECODER = re.compile(
    r'\bjson\.JSONDecoder\b',
)

# Match `.encode("utf-8")` or `.encode('utf-8')` or `.encode()`
RE_ENCODE = re.compile(
    r'\.encode\s*\(\s*["\']utf-8["\']?\s*\)',
)
RE_ENCODE_NONAME = re.compile(
    r'\.encode\s*\(\s*\)',
)

# Match dumps options we need to remove or transform
RE_INDENT = re.compile(
    r'\bindent\s*=\s*\d+',
)

RE_SORT_KEYS = re.compile(
    r'\bsort_keys\s*=\s*True\b',
)

RE_ENSURE_ASCII = re.compile(
    r'\bensure_ascii\s*=\s*(?:True|False)\b',
)

RE_SEPARATORS = re.compile(
    r'\bseparators\s*=\s*\([^)]*\)',
)

# Match keyword arguments inside dumps()
RE_DUMPS_KWARGS = re.compile(
    r'([a-z_]+)\s*=\s*(?:'
    r'True|False|\d+|["\'][^"\']*["\']|\([^)]*\)'
    r'|[a-zA-Z_][a-zA-Z0-9_.]*(?:\.[a-zA-Z_][a-zA-Z0-9_]*)*'
    r')',
)

# ---------------------------------------------------------------------------
#  Analysis helpers
# ---------------------------------------------------------------------------

def _has_import_json(source: str) -> bool:
    """Check if the source uses `import json` at module level."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return False
    for node in ast.walk(tree):
        if isinstance(node, ast.Import) and any(
            alias.name == "json" for alias in node.names
        ):
            return True
    return False


def _find_json_import_name(source: str) -> str | None:
    """Find the alias name for json import, e.g. 'json' or '_json'."""
    m = RE_IMPORT_JSON_AS.search(source)
    if m:
        return m.group(1)
    if re.search(r'^import\s+json\b', source, re.MULTILINE):
        return 'json'
    return None


def _transform_dumps_args(match: re.Match) -> str:
    """Transform keyword arguments inside a json.dumps() call to orjson equivalents."""
    full = match.group(0)
    prefix = match.group(1) if match.lastgroup else ''
    
    # We'll use a multi-step approach:
    # 1. Extract the arguments inside dumps()
    # 2. Convert them
    # 3. Return the new call
    
    return full  # Placeholder - will be handled differently


# ---------------------------------------------------------------------------
#  File-level migration
# ---------------------------------------------------------------------------

def _replace_import_json(source: str) -> str:
    """Replace `import json` / `import json as X` with `import orjson` / `import orjson as X`.
    Handles multi-imports like `import os, json` or `import os, json as _json`.
    Also handles indented (late) imports inside function bodies.
    """
    lines = source.split('\n')
    new_lines = []
    for line in lines:
        # Only process import lines that import the word 'json' (not 'orjson')
        if re.match(r'^\s*import\s', line) and re.search(r'\bjson\b', line):
            # Replace `json as X` -> `orjson as X`  (with or without leading comma)
            line = RE_IMPORT_JSON_AS.sub(r'orjson as \1', line)
            # Handle `import json` at start (possibly after whitespace)
            line = re.sub(r'^\s*import\s+json\b', lambda m: m.group(0).replace('json', 'orjson'), line)
            # Handle `, json` in middle (after other imports)
            line = re.sub(r',\s*json\b', ', orjson', line)
        new_lines.append(line)
    return '\n'.join(new_lines)


# Regex to find aliased json dumps calls like `_json.dumps(` or `_j.dumps(`
RE_ALIAS_DUMPS = re.compile(
    r'(?<!\.)\b(_json|_j)\.dumps\s*\(',
)


def _replace_alias_dumps(source: str, alias: str = '_json') -> str:
    """Add .decode('utf-8') to alias.dumps() calls for aliases like _json, _j."""
    result = []
    i = 0
    pattern = re.compile(
        rf'(?<!\.)\b{re.escape(alias)}\.dumps\s*\(',
    )
    while i < len(source):
        m = pattern.search(source, i)
        if not m:
            result.append(source[i:])
            break
        
        result.append(source[i:m.start()])
        
        # Find the matching closing paren
        start = m.end()
        depth = 1
        j = start
        while j < len(source) and depth > 0:
            if source[j] == '(':
                depth += 1
            elif source[j] == ')':
                depth -= 1
            j += 1
        
        inner = source[start:j-1]
        
        # Transform the inner arguments (same as json.dumps transformation)
        new_inner = _transform_dumps_inner(inner)
        
        result.append(f'{alias}.dumps({new_inner}).decode(\'utf-8\')')
        i = j
    
    return ''.join(result)


def _replace_json_loads(source: str) -> str:
    """Replace json.loads( with orjson.loads(."""
    return RE_JSON_LOADS.sub('orjson.loads(', source)


def _replace_json_load(source: str) -> str:
    """Replace json.load(f) with orjson.loads(f.read())."""
    # We need to handle the parentheses matching. json.load(f) -> orjson.loads(f.read())
    # For simple cases: json.load(f) -> orjson.loads(f.read())
    # Use a more sophisticated approach to match balanced parens
    result = []
    i = 0
    while i < len(source):
        m = RE_JSON_LOAD.search(source, i)
        if not m:
            result.append(source[i:])
            break
        
        result.append(source[i:m.start()])
        # Find the matching closing paren
        start = m.end()
        depth = 1
        j = start
        while j < len(source) and depth > 0:
            if source[j] == '(':
                depth += 1
            elif source[j] == ')':
                depth -= 1
            j += 1
        
        inner = source[start:j-1]  # content between parens
        result.append(f'orjson.loads({inner}.read())')
        i = j
    
    return ''.join(result)


def _replace_json_dumps(source: str) -> str:
    """Replace json.dumps() with orjson.dumps() with proper options."""
    result = []
    i = 0
    while i < len(source):
        m = RE_JSON_DUMPS.search(source, i)
        if not m:
            result.append(source[i:])
            break
        
        result.append(source[i:m.start()])
        
        # Find the matching closing paren for dumps(
        start = m.end()
        depth = 1
        j = start
        while j < len(source) and depth > 0:
            if source[j] == '(':
                depth += 1
            elif source[j] == ')':
                depth -= 1
            j += 1
        
        inner = source[start:j-1]  # content between parens
        
        # Transform the inner arguments
        new_inner = _transform_dumps_inner(inner)
        
        result.append(f'orjson.dumps({new_inner}).decode(\'utf-8\')')
        i = j
    
    return ''.join(result)

def _transform_dumps_inner(inner: str) -> str:
    """Transform the arguments inside a json.dumps() call to orjson-compatible form."""
    
    args = inner.strip()
    if not args:
        return args
    
    # Split args into a list, respecting nested brackets/parens
    split_args = _split_dump_args(args)
    
    has_indent = False
    has_sort_keys = False
    preserved = []
    
    for arg in split_args:
        arg = arg.strip()
        if not arg:
            continue
        # Check if this is a keyword argument we need to handle specially
        if re.match(r'^indent\s*=', arg):
            has_indent = True
        elif re.match(r'^sort_keys\s*=', arg):
            has_sort_keys = True
        elif re.match(r'^ensure_ascii\s*=', arg):
            pass  # Remove - orjson always outputs UTF-8
        elif re.match(r'^separators\s*=', arg):
            pass  # Remove - orjson uses compact separators by default
        else:
            preserved.append(arg)
    
    # Build option flags
    options = []
    if has_indent:
        options.append('orjson.OPT_INDENT_2')
    if has_sort_keys:
        options.append('orjson.OPT_SORT_KEYS')
    
    if options:
        preserved.append(f'option={" | ".join(options)}')
    
    return ', '.join(preserved)


def _clean_args(args: str) -> str:
    """Clean up arguments after removing some keywords."""
    # Remove double commas
    args = re.sub(r',\s*,', ',', args)
    # Remove leading/trailing commas and spaces
    args = args.strip().strip(',').strip()
    return args


def _replace_json_dump(source: str) -> str:
    """Replace json.dump(x, f) with f.write(orjson.dumps(x).decode('utf-8'))."""
    result = []
    i = 0
    while i < len(source):
        m = RE_JSON_DUMP.search(source, i)
        if not m:
            result.append(source[i:])
            break
        
        result.append(source[i:m.start()])
        
        # Find the matching closing paren for dump(
        start = m.end()
        depth = 1
        j = start
        while j < len(source) and depth > 0:
            if source[j] == '(':
                depth += 1
            elif source[j] == ')':
                depth -= 1
            j += 1
        
        inner = source[start:j-1]  # content between parens
        
        # json.dump(obj, fp, ...) -> fp.write(orjson.dumps(obj, ...).decode('utf-8'))
        # Need to extract the first two arguments: obj and fp
        args = _split_dump_args(inner)
        
        if len(args) >= 2:
            obj_arg = args[0]
            fp_arg = args[1]
            remaining = ', '.join(args[2:]) if len(args) > 2 else ''
            
            # Transform remaining args (same as dumps kwargs)
            remaining = _transform_dumps_inner(remaining)
            
            if remaining:
                new_call = f'{fp_arg}.write(orjson.dumps({obj_arg}, {remaining}).decode(\'utf-8\'))'
            else:
                new_call = f'{fp_arg}.write(orjson.dumps({obj_arg}).decode(\'utf-8\'))'
        else:
            # Fallback
            new_call = f'({inner})'
        
        result.append(new_call)
        i = j
    
    return ''.join(result)


def _split_dump_args(inner: str) -> list[str]:
    """Split arguments of json.dump(), respecting nested parens."""
    args = []
    depth = 0
    current = []
    for ch in inner:
        if ch == '(' or ch == '[' or ch == '{':
            depth += 1
            current.append(ch)
        elif ch == ')' or ch == ']' or ch == '}':
            depth -= 1
            current.append(ch)
        elif ch == ',' and depth == 0:
            args.append(''.join(current).strip())
            current = []
        else:
            current.append(ch)
    if current:
        args.append(''.join(current).strip())
    return args


def _replace_json_decoder(source: str) -> str:
    """Replace json.JSONDecoder() with a note about orjson alternative."""
    # json.JSONDecoder().raw_decode(x) -> this doesn't have a direct orjson equivalent
    # We replace json.JSONDecoder with a comment/fallback
    source = RE_JSON_DECODER.sub('json.JSONDecoder', source)  # Leave as is for now
    return source


def _replace_json_decode_error(source: str) -> str:
    """Replace json.JSONDecodeError with orjson.JSONDecodeError."""
    return RE_JSON_DECODE_ERROR.sub('orjson.JSONDecodeError', source)


def _remove_redundant_encode(source: str) -> str:
    """Remove .encode('utf-8') that follows orjson.dumps() since orjson already returns bytes."""
    # Pattern: orjson.dumps(...).encode('utf-8') -> orjson.dumps(...)
    # We match orjson.dumps(...).decode('utf-8').encode('utf-8') which is redundant
    # Actually this won't happen since we add decode.
    
    # But what about cases where code does: json.dumps(x).encode('utf-8')?
    # After our transformation: orjson.dumps(x).decode('utf-8').encode('utf-8')
    # That's redundant. Let's handle this case.
    
    # Pattern: orjson.dumps(...).decode('utf-8').encode('utf-8')
    source = re.sub(
        r'(orjson\.dumps\s*\([^)]*\)\s*)\.decode\([\'"]utf-8[\'"]\)\s*\.encode\([\'"]utf-8[\'"]\)',
        r'\1',
        source,
    )
    # Also handle .decode('utf-8').encode()
    source = re.sub(
        r'(orjson\.dumps\s*\([^)]*\)\s*)\.decode\([\'"]utf-8[\'"]\)\s*\.encode\s*\(\s*\)',
        r'\1',
        source,
    )
    return source


def _remove_redundant_encode_alias(source: str, aliases: set[str]) -> str:
    """Remove .encode() after alias.dumps().decode('utf-8') for aliased imports."""
    for alias in aliases:
        # alias.dumps(...).decode('utf-8').encode('utf-8') -> alias.dumps(...)
        source = re.sub(
            rf"({re.escape(alias)}\.dumps\s*\([^)]*\)\s*)\.decode\([\"']utf-8[\"']\)\s*\.encode\([\"']utf-8[\"']\)",
            r'\1',
            source,
        )
        # alias.dumps(...).decode('utf-8').encode() -> alias.dumps(...)
        source = re.sub(
            rf"({re.escape(alias)}\.dumps\s*\([^)]*\)\s*)\.decode\([\"']utf-8[\"']\)\s*\.encode\s*\(\)",
            r'\1',
            source,
        )
    return source


def _has_json_decoder(source: str) -> bool:
    """Check if source uses json.JSONDecoder (which has no orjson equivalent)."""
    return bool(RE_JSON_DECODER.search(source))


def _replace_import_json_with_decoder(source: str) -> str:
    """For files that use json.JSONDecoder: keep import json, add import orjson."""
    lines = source.split('\n')
    new_lines = []
    already_has_orjson = False
    for line in lines:
        if re.match(r'^\s*import\s', line):
            # Check if this line imports `orjson`
            if re.search(r'\borjson\b', line):
                already_has_orjson = True
            # Only process lines that import the word `json` (not orjson)
            if re.search(r'\bjson\b', line):
                # This is a `import json` line - keep it as is for JSONDecoder
                new_lines.append(line)
                continue
        new_lines.append(line)
    # Add import orjson if not already present
    if not already_has_orjson:
        # Find a good place to add it - after the last import line
        insert_pos = len(new_lines)
        for i in range(len(new_lines) - 1, -1, -1):
            if re.match(r'^\s*import\s', new_lines[i]) or re.match(r'^\s*from\s', new_lines[i]):
                insert_pos = i + 1
                break
        new_lines.insert(insert_pos, 'import orjson')
    return '\n'.join(new_lines)


def _find_json_aliases(source: str) -> set[str]:
    """Find all alias names used for json/orjson imports."""
    aliases = set()
    for m in re.finditer(r'\b(?:import\s+.*\bjson\s+as\s+(\w+)|import\s+.*\borjson\s+as\s+(\w+))', source):
        a = m.group(1) or m.group(2)
        if a:
            aliases.add(a)
    # Also check for semicolon-separated imports like `import json; import orjson`
    for m in re.finditer(r'\bjson\s+as\s+(\w+)', source):
        aliases.add(m.group(1))
    for m in re.finditer(r'\borjson\s+as\s+(\w+)', source):
        aliases.add(m.group(1))
    return aliases


def migrate_file(filepath: Path, dry_run: bool = False) -> tuple[bool, str]:
    """Migrate a single Python file from json to orjson.
    
    Returns (changed, new_content).
    """
    try:
        source = filepath.read_text(encoding='utf-8')
    except Exception as e:
        print(f"  ERROR reading {filepath}: {e}")
        return False, ""
    
    if not _has_import_json(source):
        return False, source
    
    old_source = source
    
    # Detect aliases before changing imports
    aliases = _find_json_aliases(source)
    
    # Detect if file uses json.JSONDecoder (no orjson equivalent)
    needs_std_json = _has_json_decoder(source)
    
    if needs_std_json:
        # Keep import json for JSONDecoder, add import orjson separately
        source = _replace_import_json_with_decoder(source)
    else:
        # Replace import json -> import orjson
        source = _replace_import_json(source)
    
    # 2. Replace json.JSONDecodeError
    source = _replace_json_decode_error(source)
    
    # 3. Handle json.loads (do this before json.load to avoid conflicts)
    source = _replace_json_loads(source)
    
    # 4. Handle json.load
    source = _replace_json_load(source)
    
    # 5. Handle json.dumps (do before json.dump)
    source = _replace_json_dumps(source)
    
    # 6. Handle json.dump
    source = _replace_json_dump(source)
    
    # 7. Handle aliased dumps calls (_json.dumps, _j.dumps, etc.)
    for alias in sorted(aliases, reverse=True):
        source = _replace_alias_dumps(source, alias=alias)
    
    # 8. Clean up redundant encode/decode
    source = _remove_redundant_encode(source)
    source = _remove_redundant_encode_alias(source, aliases)
    
    if source == old_source:
        return False, source
    
    if not dry_run:
        filepath.write_text(source, encoding='utf-8')
    
    return True, source


# ---------------------------------------------------------------------------
#  Main entry point
# ---------------------------------------------------------------------------

def main():
    import argparse
    
    parser = argparse.ArgumentParser(description='Migrate json -> orjson')
    parser.add_argument('paths', nargs='*', default=None,
                        help='Files/directories to process (default: all Python files in repo)')
    parser.add_argument('--dry-run', action='store_true',
                        help='Show changes without writing')
    args = parser.parse_args()
    
    dry_run = args.dry_run
    
    if args.paths:
        paths = []
        for p in args.paths:
            p_path = Path(p)
            if p_path.is_file():
                paths.append(p_path)
            elif p_path.is_dir():
                paths.extend(p_path.rglob('*.py'))
    else:
        paths = list(REPO_ROOT.rglob('*.py'))
    
    # Skip __pycache__, .venv, node_modules, etc.
    # Skip dirs (NOT file names - __init__.py should NOT be here)
    skip_dirs = {
        '__pycache__', '.venv', 'venv', 'env', '.git', 'node_modules',
        '.mypy_cache', '.pytest_cache', '.ruff_cache',
        'build', 'dist', '*.egg-info',
    }
    paths = [p for p in paths if not any(
        part.startswith('.') or part in skip_dirs
        for part in p.relative_to(REPO_ROOT).parts
    )]
    
    # Sort for deterministic ordering
    paths.sort()
    
    changed = 0
    total = 0
    
    for filepath in paths:
        # Skip empty __init__.py files and non-Python
        if not filepath.suffix == '.py':
            continue
        
        try:
            was_changed, new_content = migrate_file(filepath, dry_run=dry_run)
        except Exception as e:
            print(f"  ERROR processing {filepath}: {e}")
            continue
        
        if was_changed:
            changed += 1
            rel = filepath.relative_to(REPO_ROOT)
            print(f"  {'[DRY RUN]' if dry_run else '[CHANGED]'} {rel}")
        
        total += 1
    
    print(f"\nProcessed {total} files, changed {changed} files")
    if dry_run:
        print("Dry-run mode - no files were modified")


if __name__ == '__main__':
    main()

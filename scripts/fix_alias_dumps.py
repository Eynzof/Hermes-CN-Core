"""
Fix aliased orjson.dumps() calls that don't have .decode('utf-8').

After the initial migration, files with `import json as _json` were changed to
`import orjson as _json`, but `_json.dumps().decode('utf-8')` calls still return bytes. This
script adds `.decode('utf-8')` where needed and handles parameter conversion.

Usage: python scripts/fix_alias_dumps.py [--dry-run]
"""

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

def _find_files_with_aliased_orjson():
    """Find files that import orjson with an alias like _json, _j."""
    results = []
    for fp in REPO_ROOT.rglob('*.py'):
        # Skip unwanted dirs
        parts = fp.relative_to(REPO_ROOT).parts
        if any(p.startswith('.') or p in ('__pycache__', '.venv', 'venv', 'env', '.git', 'node_modules', 'build', 'dist') for p in parts):
            continue
        content = fp.read_text(encoding='utf-8')
        if re.search(r'\borjson\s+as\s+(\w+)', content):
            results.append((fp, content))
    return results


def _split_args(inner: str) -> list[str]:
    """Split arguments respecting nested brackets."""
    args = []
    depth = 0
    current = []
    for ch in inner:
        if ch in '({[':
            depth += 1
            current.append(ch)
        elif ch in ')}]':
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


def _transform_dumps_args(args_str: str) -> tuple[str, str]:
    """Transform dumps kwargs and return (new_args, extra_option_code).
    
    Returns (transformed_args, option_code_to_insert).
    """
    args = _split_args(args_str)
    has_indent = False
    has_sort_keys = False
    preserved = []
    
    for arg in args:
        arg = arg.strip()
        if not arg:
            continue
        if re.match(r'^indent\s*=', arg):
            has_indent = True
        elif re.match(r'^sort_keys\s*=', arg):
            has_sort_keys = True
        elif re.match(r'^ensure_ascii\s*=', arg):
            pass
        elif re.match(r'^separators\s*=', arg):
            pass
        else:
            preserved.append(arg)
    
    # Build option flags
    options = []
    if has_indent:
        options.append('orjson.OPT_INDENT_2')
    if has_sort_keys:
        options.append('orjson.OPT_SORT_KEYS')
    
    option_code = ''
    if options:
        option_code = ' | '.join(options)
    
    return ', '.join(preserved), option_code


def fix_file(content: str) -> tuple[bool, str]:
    """Fix aliased orjson.dumps() calls in a file."""
    original = content
    
    # Find all import aliases
    aliases = set()
    for m in re.finditer(r'\borjson\s+as\s+(\w+)', content):
        aliases.add(m.group(1))
    
    for alias in sorted(aliases, reverse=True):
        # Find all alias.dumps(...) calls
        pattern = re.compile(
            rf'(?<!\.)\b{re.escape(alias)}\.dumps\s*\(',
        )
        
        # We need to find calls and add .decode('utf-8')
        # But also skip calls that already have .decode after them
        result = []
        i = 0
        while i < len(content):
            m = pattern.search(content, i)
            if not m:
                result.append(content[i:])
                break
            
            result.append(content[i:m.start()])
            
            # Find the matching closing paren
            start = m.end()
            depth = 1
            j = start
            while j < len(content) and depth > 0:
                if content[j] == '(':
                    depth += 1
                elif content[j] == ')':
                    depth -= 1
                j += 1
            
            inner = content[start:j-1]
            
            # Check if this dumps call already has .decode after it
            rest = content[j:j+20] if j < len(content) else ''
            already_decoded = rest.lstrip().startswith('.decode(')
            
            if already_decoded:
                # Already has .decode - just copy as is
                result.append(content[m.start():j])
            else:
                # Transform kwargs
                new_inner, option_code = _transform_dumps_args(inner)
                
                if option_code:
                    if new_inner:
                        new_call = f'{alias}.dumps({new_inner}, option={option_code}).decode(\'utf-8\')'
                    else:
                        new_call = f'{alias}.dumps(option={option_code}).decode(\'utf-8\')'
                else:
                    new_call = f'{alias}.dumps({new_inner}).decode(\'utf-8\')' if new_inner else f'{alias}.dumps().decode(\'utf-8\')'
                
                result.append(new_call)
            
            i = j
        
        content = ''.join(result)
        
        # Remove redundant encode: alias.dumps(...).decode('utf-8').encode(...) -> alias.dumps(...)
        content = re.sub(
            rf"({re.escape(alias)}\.dumps\s*\([^)]*\)\s*)\.decode\(['\"]utf-8['\"]\)\s*\.encode\(['\"]utf-8['\"]\)",
            r'\1',
            content,
        )
        content = re.sub(
            rf"({re.escape(alias)}\.dumps\s*\([^)]*\)\s*)\.decode\(['\"]utf-8['\"]\)\s*\.encode\s*\(\)",
            r'\1',
            content,
        )
    
    return content != original, content


def main():
    dry_run = '--dry-run' in sys.argv
    
    files = _find_files_with_aliased_orjson()
    changed = 0
    
    for fp, content in files:
        was_changed, new_content = fix_file(content)
        if was_changed:
            changed += 1
            rel = fp.relative_to(REPO_ROOT)
            print(f"  {'[DRY RUN]' if dry_run else '[CHANGED]'} {rel}")
            if not dry_run:
                fp.write_text(new_content, encoding='utf-8')
    
    print(f"\nProcessed {len(files)} files, changed {changed} files")
    if dry_run:
        print("Dry-run mode - no files were modified")


if __name__ == '__main__':
    main()

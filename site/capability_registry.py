import json
import re
import yaml
from pathlib import Path
from typing import Dict, List, Optional, Union

TOKEN_RE = re.compile(r'^[a-z][a-z0-9]*(?::[a-z][a-z0-9-]*)+$')


def provider_token_type(token: str) -> str:
    """Return 'capability' for namespaced tokens (containing ':'), 'label' otherwise."""
    return 'capability' if ':' in token else 'label'



class CapabilityRegistry:
    """
    Loads capability token definitions from one or more capability/ directories.

    Directories are scanned in order — pass standard SRCROOT first so that
    built-in tokens are defined before user/OEM extensions that imply them.
    Within each directory, *.yaml files are loaded in lexical order.
    include: directives inside a file are resolved relative to that file and
    are useful for organising subdirectory trees; top-level siblings are
    discovered by the scan, not by include:.
    """

    def __init__(self, cap_dirs: Union[str, Path, List[Union[str, Path]]]):
        if not isinstance(cap_dirs, list):
            cap_dirs = [cap_dirs]
        cap_dirs = [Path(d) for d in cap_dirs]
        self._tokens: Dict[str, dict] = {}
        visited: set = set()
        for cap_dir in cap_dirs:
            if not cap_dir.is_dir():
                continue
            for yaml_file in sorted(cap_dir.glob('*.yaml')):
                self._load(yaml_file.resolve(), visited)
        self._check_parents()
        self._validate_schema(cap_dirs)

    def _load(self, path: Path, visited: set):
        if path in visited:
            return
        visited.add(path)

        try:
            with open(path) as f:
                data = yaml.safe_load(f) or {}
        except yaml.YAMLError as e:
            raise ValueError(f"Failed to parse {path}: {e}")

        if not isinstance(data, dict):
            raise ValueError(f"{path}: expected a mapping at root level")

        for inc in data.pop('include', []):
            inc_path = (path.parent / inc).resolve()
            if not inc_path.exists():
                raise FileNotFoundError(f"Include '{inc}' not found (from {path})")
            self._load(inc_path, visited)

        for token, entry in data.items():
            if not TOKEN_RE.match(token):
                raise ValueError(f"{path}: invalid token name '{token}'")
            if token in self._tokens:
                raise ValueError(
                    f"'{token}' already defined in {self._tokens[token]['source']}, "
                    f"redefined in {path}"
                )
            if not isinstance(entry, dict) or 'desc' not in entry:
                raise ValueError(f"{path}: '{token}' must have a 'desc' field")
            implies = entry.get('implies', [])
            for target in implies:
                if target not in self._tokens:
                    raise ValueError(
                        f"{path}: forward reference to '{target}' before it's defined"
                    )
            token_entry: dict = {'desc': entry['desc'], 'source': path}
            if implies:
                token_entry['implies'] = implies
            self._tokens[token] = token_entry

    def _check_parents(self):
        for token in self._tokens:
            parts = token.split(':')
            for i in range(2, len(parts)):
                parent = ':'.join(parts[:i])
                if parent not in self._tokens:
                    raise ValueError(
                        f"'{token}' requires parent '{parent}' to be defined in the registry"
                    )

    def _validate_schema(self, cap_dirs: List[Path]):
        schema_path = next(
            (d / 'known.schema.json' for d in cap_dirs if (d / 'known.schema.json').exists()),
            None
        )
        if not schema_path:
            return
        import jsonschema
        with open(schema_path) as f:
            schema = json.load(f)
        data = {
            token: {k: v for k, v in entry.items() if k != 'source'}
            for token, entry in self._tokens.items()
        }
        try:
            jsonschema.validate(data, schema)
        except jsonschema.ValidationError as e:
            raise ValueError(f"Capability registry schema validation failed: {e.message}")

    def expand(self, token: str) -> Dict[str, str]:
        if token not in self._tokens:
            raise ValueError(f"'{token}' is not defined in the registry")
        result: Dict[str, str] = {}
        self._expand(token, result, set())
        return result

    def _expand(self, token: str, result: Dict[str, str], seen: set):
        if token in seen:
            return
        seen.add(token)
        parts = token.split(':')
        for i in range(2, len(parts)):
            self._expand(':'.join(parts[:i]), result, seen)
        result[token] = str(self._tokens[token]['source'])
        for implied in self._tokens[token].get('implies', []):
            self._expand(implied, result, seen)

    def __contains__(self, token: str) -> bool:
        return token in self._tokens

    def get_desc(self, token: str) -> Optional[str]:
        entry = self._tokens.get(token)
        return entry['desc'] if entry else None

    @property
    def all_tokens(self) -> Dict[str, str]:
        return {t: str(v['source']) for t, v in self._tokens.items()}

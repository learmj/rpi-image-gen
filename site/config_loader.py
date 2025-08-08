import configparser
import os
import yaml
from pathlib import Path
from typing import Optional, Dict, Any, Tuple


class ConfigLoader:
    def __init__(self, cfg_path: str, *, expand_vars: bool = True, overrides_path: Optional[str] = None, search_paths: Optional[list[str]] = None):
        self.cfg_path = cfg_path
        self.overrides_path = overrides_path
        self.expand_vars = expand_vars
        # Additional include search paths (default ./config)
        self.search_paths = [Path(p).resolve() for p in (search_paths or ["./config"])]
        self.file_format = self._detect_format()

        # Data will be stored as Dict[str, Dict[str, str]] regardless of source format
        self.data: Dict[str, Dict[str, str]] = {}

        # Track which values are overridden
        self.overrides: Dict[str, str] = {}

        # For backward compatibility, maintain config attribute for INI files
        if self.file_format == 'ini':
            # Disable interpolation to allow literal % chars
            self.config = configparser.ConfigParser(interpolation=None)

        self._load()
        if self.overrides_path:
            self._load_overrides()

    def _detect_format(self) -> str:
        """Detect file format based on extension"""
        suffix = Path(self.cfg_path).suffix.lower()
        if suffix in ['.yaml', '.yml']:
            return 'yaml'
        elif suffix in ['.ini', '.cfg', '.conf']:
            return 'ini'
        else:
            # Default to INI for backward compatibility
            return 'ini'

    def _resolve_include(self, inc_name: str, parent_dir: Path) -> Path:
        """Resolve include file by trying parent dir then configured search paths."""
        # prevent absolute
        inc_rel = Path(inc_name)
        if inc_rel.is_absolute():
            raise ValueError(f"Absolute include paths not allowed (found {inc_name})")
        # 1. parent directory
        cand = (parent_dir / inc_rel).resolve()
        if cand.exists():
            return cand
        # 2. search additional include paths
        for base in self.search_paths:
            cand2 = (base / inc_rel).resolve()
            if cand2.exists():
                return cand2
        raise FileNotFoundError(f"Included file '{inc_name}' not found (searched parent dir and {self.search_paths})")

    def _load(self):
        if not os.path.exists(self.cfg_path):
            raise FileNotFoundError(f"Config file not found: {self.cfg_path}")

        if self.file_format == 'yaml':
            self._load_yaml()
        else:
            self._load_ini()

    def _load_yaml(self):
        """Load YAML file and convert to internal format"""
        def _load_yaml_recursive(path: Path, visited: set) -> Dict[str, Dict[str, str]]:
            if path in visited:
                raise ValueError(f"Circular include detected in YAML files: {path}")
            visited.add(path)

            try:
                with open(path, 'r') as f:
                    yaml_data = yaml.safe_load(f) or {}
            except yaml.YAMLError as e:
                raise ValueError(f"Failed to parse YAML file {path}: {e}")

            if not isinstance(yaml_data, dict):
                raise ValueError(f"YAML file {path} must contain a mapping at root level")

            # Handle include directive
            included_sections: Dict[str, Dict[str, str]] = {}
            if 'include' in yaml_data and isinstance(yaml_data['include'], dict):
                inc_file = yaml_data['include'].get('file')
                if not inc_file:
                    raise ValueError(f"YAML include directive in {path} missing 'file' key")
                inc_path = self._resolve_include(inc_file, path.parent)
                included_sections = _load_yaml_recursive(inc_path, visited)
                # Remove include key before merging
                yaml_data.pop('include', None)

            # Convert current file sections
            curr_sections: Dict[str, Dict[str, str]] = {}
            for sect, sect_data in yaml_data.items():
                if not isinstance(sect_data, dict):
                    raise ValueError(f"Section '{sect}' in {path} must be a mapping")
                curr_sections[sect] = {k: str(v) for k, v in sect_data.items()}

            # Merge: current overrides included
            merged = {**included_sections}
            for sect, mapping in curr_sections.items():
                merged.setdefault(sect, {}).update(mapping)
            return merged

        self.data = _load_yaml_recursive(Path(self.cfg_path).resolve(), set())

    def _load_ini(self):
        """Load INI file using configparser"""
        def _load_ini_recursive(path: Path, visited: set, cfg: configparser.ConfigParser):
            if path in visited:
                raise ValueError(f"Circular include detected in INI files: {path}")
            visited.add(path)

            buffer_lines = []
            with open(path, 'r') as f:
                for line in f:
                    stripped = line.strip()
                    if stripped.startswith('!include'):
                        parts = stripped.split(maxsplit=1)
                        if len(parts) != 2:
                            raise ValueError(f"Invalid !include directive in {path}: {line}")
                        inc_name = parts[1].strip()
                        inc_path = self._resolve_include(inc_name, path.parent)
                        _load_ini_recursive(inc_path, visited, cfg)
                    else:
                        buffer_lines.append(line)

            cfg.read_string(''.join(buffer_lines))

        # Disable interpolation
        self.config = configparser.ConfigParser(interpolation=None)
        _load_ini_recursive(Path(self.cfg_path).resolve(), set(), self.config)

        for section in self.config.sections():
            self.data[section] = dict(self.config[section].items())

    def _load_overrides(self):
        """Load override file with key=value pairs and expand variables"""
        if not self.overrides_path:
            return

        if not os.path.exists(self.overrides_path):
            raise FileNotFoundError(f"Override file not found: {self.overrides_path}")

        # Build context for variable expansion from config data
        expansion_context = {}
        for section_name, section_data in self.data.items():
            for key, value in section_data.items():
                env_key = self._env_key(section_name, key)
                expansion_context[env_key] = self._expand(value)

        try:
            with open(self.overrides_path, 'r') as f:
                for line_num, line in enumerate(f, 1):
                    line = line.strip()

                    # Skip empty lines and comments
                    if not line or line.startswith('#'):
                        continue

                    # Parse key=value format
                    if '=' not in line:
                        raise ValueError(f"Invalid format at line {line_num}: {line}")

                    key, value = line.split('=', 1)
                    key = key.strip()
                    value = value.strip()

                    # Remove quotes if present
                    if value.startswith('"') and value.endswith('"'):
                        value = value[1:-1]
                    elif value.startswith("'") and value.endswith("'"):
                        value = value[1:-1]

                    # Store raw override first
                    self.overrides[key] = value
                    # Also add to expansion context for subsequent overrides
                    expansion_context[key] = value

            # Now expand all override values in the context of config + previous overrides
            for key, value in self.overrides.items():
                try:
                    self.overrides[key] = self._expand_with_context(value, expansion_context)
                    # Update context with expanded value
                    expansion_context[key] = self.overrides[key]
                except ValueError as ve:
                    # Re-raise variable expansion errors with cleaner message including file context
                    raise ValueError(f"{ve} in override file {self.overrides_path}") from None

        except ValueError:
            # Re-raise ValueError (including variable expansion errors) without wrapping
            raise
        except Exception as e:
            raise ValueError(f"Failed to load override file {self.overrides_path}: {e}")

    def _expand_with_context(self, value: str, context: Dict[str, str]) -> str:
        """Expand variables using both environment and provided context"""
        if not self.expand_vars:
            return value

        # First expand using environment variables (standard behavior)
        expanded = os.path.expandvars(value)

        # Then expand using our context (for variables not in environment)
        import re
        def replace_var(match):
            var_name = match.group(1)
            if var_name in os.environ:
                return os.environ[var_name]
            elif var_name in context:
                return context[var_name]
            else:
                # Error on undefined variables
                raise ValueError(f"Undefined variable in override: ${{{var_name}}}")

        # Handle ${VAR} format
        expanded = re.sub(r'\$\{([^}]+)\}', replace_var, expanded)

        return expanded

    def _expand(self, value: str) -> str:
        return os.path.expandvars(value) if self.expand_vars else value

    def _env_key(self, section: str, key: str) -> str:
        return f"IGconf_{section.lower()}_{key.lower()}"

    def _parse_env_key(self, env_key: str) -> Optional[Tuple[str, str]]:
        """Parse an environment key back to (section, key) tuple"""
        if not env_key.startswith("IGconf_"):
            return None

        # Remove the IGconf_ prefix
        remaining = env_key[7:]  # len("IGconf_") = 7

        # Split on underscores and try to find a valid section_key combination
        # We need to be careful because sections or keys might contain underscores
        parts = remaining.split('_')
        if len(parts) < 2:
            return None

        # Try different combinations, starting with the assumption that
        # the last part is the key and everything before is the section
        for i in range(1, len(parts)):
            potential_section = '_'.join(parts[:i])
            potential_key = '_'.join(parts[i:])

            # Check if this section exists in our data
            if potential_section in self.data:
                return (potential_section, potential_key)

        # If no matching section found, assume single-part section name
        if len(parts) >= 2:
            return (parts[0], '_'.join(parts[1:]))

        return None

    def _set_env_if_unset(self, env_key: str, value: str):
        if env_key not in os.environ:
            # Check if this value is overridden
            if env_key in self.overrides:
                final_value = self._expand(self.overrides[env_key])
                os.environ[env_key] = final_value
                print(f"OVR {env_key}={final_value}")
            else:
                final_value = self._expand(value)
                os.environ[env_key] = final_value
                print(f"CFG {env_key}={final_value}")
        else:
            print(f"{env_key} already set, skipping")

    def _get_value(self, env_key: str, cfg_value: str) -> str:
        return os.environ.get(env_key, self._expand(cfg_value))

    def load_section(self, section: str):
        if section not in self.data:
            raise ValueError(f"Section [{section}] not found in {self.cfg_path}")
        for key, value in self.data[section].items():
            self._set_env_if_unset(self._env_key(section, key), self._expand(value))

    def load_all(self):
        for section in self.data.keys():
            self.load_section(section)

        # Process override variables that don't correspond to config file entries
        self._load_override_only_variables()

    def _load_override_only_variables(self):
        """Process override variables that don't correspond to any config file entries"""
        if not self.overrides:
            return

        # Build set of all environment keys that were processed from config file
        processed_env_keys = set()
        for section_name, section_data in self.data.items():
            for key in section_data.keys():
                env_key = self._env_key(section_name, key)
                processed_env_keys.add(env_key)

        # Process any override variables that weren't in the config file
        for override_key, override_value in self.overrides.items():
            if override_key not in processed_env_keys:
                self._set_env_if_unset(override_key, override_value)

    def _write_var(self, file_handle, section: str, key: str, value: str):
        """Write variable to file, use env value else override value else config value"""
        env_key = self._env_key(section, key)

        # Check precedence: environment -> override -> config
        if env_key in os.environ:
            effective_value = os.environ[env_key]
            source = "env"
        elif env_key in self.overrides:
            effective_value = self._expand(self.overrides[env_key])
            source = "override"
        else:
            effective_value = self._expand(value)
            source = "config"

        file_handle.write(f'{env_key}="{effective_value}"\n')

        # Use same output format as _set_env_if_unset for consistency
        if source == "env":
            print(f"ENV {env_key}={effective_value}")
        elif source == "override":
            print(f"OVR {env_key}={effective_value}")
        else:
            print(f"CFG {env_key}={effective_value}")

    def write_file(self, file_path: str, section: Optional[str] = None):
        with open(file_path, 'w') as f:
            processed_env_keys = set()

            # First, process all keys from the config file
            sections = [section] if section else list(self.data.keys())
            for sect in sections:
                if sect not in self.data:
                    raise ValueError(f"Section [{sect}] not found in {self.cfg_path}")
                for key, value in self.data[sect].items():
                    env_key = self._env_key(sect, key)
                    processed_env_keys.add(env_key)
                    self._write_var(f, sect, key, value)

            # Then, process any override-only keys that weren't in the config file
            for override_key in self.overrides:
                if override_key not in processed_env_keys:
                    # For override-only variables, write them directly
                    self._write_override_only_var(f, override_key, section)

    def _write_override_only_var(self, file_handle, override_key: str, section_filter: Optional[str]):
        """Write an override-only variable that doesn't exist in config file"""
        # Check precedence: environment -> override
        if override_key in os.environ:
            effective_value = os.environ[override_key]
            source = "env"
        else:
            effective_value = self._expand(self.overrides[override_key])
            source = "override"

        # Try to parse the key to see if it belongs to a specific section
        parsed = self._parse_env_key(override_key)
        if parsed:
            parsed_section, parsed_key = parsed
            # If we're filtering by section, only include keys from that section
            if section_filter is not None and parsed_section != section_filter:
                return
        else:
            # If we can't parse it and we're filtering by section, skip it
            # (assume it doesn't belong to the filtered section)
            if section_filter is not None:
                return

        file_handle.write(f'{override_key}="{effective_value}"\n')

        # Print to console for consistency
        if source == "env":
            print(f"ENV {override_key}={effective_value}")
        else:
            print(f"OVR {override_key}={effective_value}")


def ConfigLoader_register_parser(subparsers):
    parser = subparsers.add_parser("config", help="Config utilities (.ini/.yaml)")
    parser.add_argument("cfg_path", nargs="?", help="Path to config file (.ini/.yaml) – omit when using --gen")
    parser.add_argument("--section", help="Section to load (load all if omitted)")
    parser.add_argument("--path", metavar="DIRS", help="Colon-separated search path for included files")
    parser.add_argument("--no-expand", action="store_true", help="Disable $VAR expansion")
    parser.add_argument("--write-to", metavar="FILE", help="Write variables to file instead of env load")
    parser.add_argument("--overrides", metavar="FILE", help="Override file with key=value pairs")
    parser.add_argument("--gen", action="store_true", help="Generate example .ini and .yaml with include syntax")
    parser.set_defaults(func=_main)


def _main(args):
    if args.gen:
        _generate_boilerplate()
        return

    if not args.cfg_path:
        print("Error: cfg_path is required unless --gen is used", file=sys.stderr)
        return

    loader = ConfigLoader(
        args.cfg_path,
        expand_vars=not args.no_expand,
        overrides_path=args.overrides,
        search_paths=(args.path.split(":") if args.path else ["./config"]),
    )
    if args.write_to:
        loader.write_file(args.write_to, args.section)
    else:
        if args.section:
            loader.load_section(args.section)
        else:
            loader.load_all()


def _generate_boilerplate():
    ini_top = """
!include base.cfg

[device]
variant = lite
storage_type = sd
"""

    ini_base = """
[device]
class = cm5
storage_type = emmc
sector_size = 512
"""

    yaml_top = """
include:
  file: base.yaml

device:
  variant: lite
  storage_type: sd
"""

    yaml_base = """
device:
  class: cm5
  storage_type: emmc
  sector_size: 512
"""

    print("INI example (top.cfg):\n" + ini_top)
    print("base.cfg:\n" + ini_base)
    print("YAML example (top.yaml):\n" + yaml_top)
    print("base.yaml:\n" + yaml_base)

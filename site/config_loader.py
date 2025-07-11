import configparser
import os
import yaml
from pathlib import Path
from typing import Optional, Dict, Any


class ConfigLoader:
    def __init__(self, cfg_path: str, expand_vars: bool = True, overrides_path: Optional[str] = None):
        self.cfg_path = cfg_path
        self.overrides_path = overrides_path
        self.expand_vars = expand_vars
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

    def _load(self):
        if not os.path.exists(self.cfg_path):
            raise FileNotFoundError(f"Config file not found: {self.cfg_path}")

        if self.file_format == 'yaml':
            self._load_yaml()
        else:
            self._load_ini()

    def _load_yaml(self):
        """Load YAML file and convert to internal format"""
        try:
            with open(self.cfg_path, 'r') as f:
                yaml_data = yaml.safe_load(f)

            if not isinstance(yaml_data, dict):
                raise ValueError(f"YAML file must contain a dictionary at root level")

            # Convert YAML data to section-based format
            for section_name, section_data in yaml_data.items():
                if not isinstance(section_data, dict):
                    raise ValueError(f"Section '{section_name}' must be a dictionary")

                # Convert all values to strings
                self.data[section_name] = {}
                for key, value in section_data.items():
                    self.data[section_name][key] = str(value)

        except yaml.YAMLError as e:
            raise ValueError(f"Failed to parse YAML file {self.cfg_path}: {e}")
        except Exception as e:
            raise ValueError(f"Failed to load YAML file {self.cfg_path}: {e}")

    def _load_ini(self):
        """Load INI file using configparser"""
        if not self.config.read(self.cfg_path):
            raise FileNotFoundError(f"Failed to read config file: {self.cfg_path}")

        # Convert configparser data to internal format
        for section_name in self.config.sections():
            self.data[section_name] = dict(self.config[section_name].items())

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
            sections = [section] if section else list(self.data.keys())
            for sect in sections:
                if sect not in self.data:
                    raise ValueError(f"Section [{sect}] not found in {self.cfg_path}")
                for key, value in self.data[sect].items():
                    self._write_var(f, sect, key, value)


def ConfigLoader_register_parser(subparsers):
    parser = subparsers.add_parser("config", help="Load .ini or .yaml config file into environment")
    parser.add_argument("cfg_path", help="Path to config file (.ini, .cfg, .conf, .yaml, .yml)")
    parser.add_argument("--section", help="Section to load (load all if omitted)")
    parser.add_argument("--no-expand", action="store_true", help="Disable $VAR expansion")
    parser.add_argument("--write-to", metavar="FILE", help="Write variables to file instead of env load")
    parser.add_argument("--overrides", metavar="FILE", help="Override file with key=value pairs")
    parser.set_defaults(func=_main)


def _main(args):
    loader = ConfigLoader(args.cfg_path, expand_vars=not args.no_expand, overrides_path=args.overrides)
    if args.write_to:
        loader.write_file(args.write_to, args.section)
    else:
        if args.section:
            loader.load_section(args.section)
        else:
            loader.load_all()

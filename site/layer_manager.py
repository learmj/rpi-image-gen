import os
import glob
import shutil
import argparse
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple
from collections import OrderedDict
import yaml

from metadata_parser import Metadata
from metadata_parser import print_env_var_descriptions

from env_types import VariableResolver, EnvVariable
from logger import log_warning, log_success, log_failure, log_error


# Handles discovery, dependency resolution, and orchestration
class LayerManager:
    def __init__(self, search_paths: Optional[List[str]] = None, file_patterns: Optional[List[str]] = None, *, show_loaded: bool = False):
        if search_paths is None:
            search_paths = ['./meta']
        if file_patterns is None:
            file_patterns = ['*.yaml', '*.yml']

        self.search_paths = [Path(p).resolve() for p in search_paths]
        self.file_patterns = file_patterns
        self.layers: Dict[str, Metadata] = {}  # layer_name -> Metadata object
        self.layer_files: Dict[str, str] = {}  # layer_name -> file_path
        self.show_loaded = show_loaded
        # provider index will be built after layers are loaded
        self.provider_index: Dict[str, str] = {}
        self.provider_conflicts: Dict[str, Set[str]] = {}

        # Tracks write-out order
        self.write_log: OrderedDict[str, str] = OrderedDict()

        for path in self.search_paths:
            if not path.exists():
                log_warning(f"Search path '{path}' does not exist")

        self.load_layers()

        # now that self.layers is populated, build provider index
        self._build_provider_index()

    def _build_provider_index(self):
        """Index providers to unique layer names"""
        for lname, meta in self.layers.items():
            info = meta.get_layer_info()
            if not info:
                continue
            for prov in info.get('provides', []):
                existing = self.provider_index.get(prov)
                if existing and existing != lname:
                    # record conflict but keep the first provider mapping (first-wins semantics)
                    self.provider_conflicts.setdefault(prov, set()).update({existing, lname})
                else:
                    self.provider_index[prov] = lname

    def load_layers(self):
        """Discover and load all layer files, creating Metadata objects for each"""
        loaded_layers = set()

        for search_path in self.search_paths:
            if not search_path.exists():
                continue

            # Find all matching files
            all_files = []
            for pattern in self.file_patterns:
                files = glob.glob(str(search_path / "**" / pattern), recursive=True)
                all_files.extend(files)

            for metadata_file in all_files:
                try:
                    meta = Metadata(metadata_file)
                except Exception:
                    # Malformed YAML or metadata – skip
                    continue

                try:
                    layer_info = meta.get_layer_info()
                except ValueError:
                    # Malformed X-Env-Layer fields; treat as non-layer file
                    continue
                if not layer_info:
                    continue

                layer_name = layer_info['name']

                # lint on load
                lint_results = meta.lint_metadata_syntax()
                if lint_results:  # Any syntax errors found
                    if self.show_loaded:
                        relative_path = Path(metadata_file).relative_to(search_path)
                        log_warning(f"  Skipped layer: {layer_name} from {relative_path} (syntax errors)")
                    continue  # Don't add

                # Duplicate detection
                if layer_name in self.layers:
                    prev_path = self.layer_files[layer_name]
                    raise ValueError(
                        f"Duplicate layer name '{layer_name}' found in:\n  {prev_path}\n  {metadata_file}"
                    )

                self.layers[layer_name] = meta
                self.layer_files[layer_name] = metadata_file
                loaded_layers.add(layer_name)

                if self.show_loaded:
                    relative_path = Path(metadata_file).relative_to(search_path)
                    metadata_type = 'x-env-layer' if meta.has_layer_info() else 'standard'
                    print(f"  Loaded layer: {layer_name} from {relative_path} ({metadata_type})")

    def get_layer_info(self, layer_name: str) -> Optional[dict]:
        if layer_name not in self.layers:
            return None
        return self.layers[layer_name].get_layer_info()

    def get_dependencies(self, layer_name: str) -> List[str]:
        """Get hard deps"""
        layer_info = self.get_layer_info(layer_name)
        if not layer_info:
            return []
        return list(layer_info['depends'])

    def get_reverse_dependencies(self, target_layer: str) -> List[str]:
        """Get hard reverse deps"""
        reverse_deps = []

        # Resolve the target layer name first
        resolved_target = self.resolve_layer_name(target_layer)
        if not resolved_target:
            return []

        # Search through all loaded layers
        for layer_name, layer_obj in self.layers.items():
            layer_info = layer_obj.get_layer_info()
            if layer_info and layer_info.get('depends'):
                # Check if target layer is in this layer's dependencies
                if resolved_target in layer_info['depends']:
                    reverse_deps.append(layer_name)

        return sorted(reverse_deps)

    def get_optional_dependencies(self, layer_name: str) -> List[str]:
        """Get optional deps"""
        layer_info = self.get_layer_info(layer_name)
        return layer_info['optional_depends'] if layer_info else []

    def get_all_dependencies(self, layer_name: str, visited: Optional[Set[str]] = None, include_optional: bool = True) -> List[str]:
        """Get all deps (including transitive) for a layer"""
        if visited is None:
            visited = set()

        if layer_name in visited or layer_name not in self.layers:
            return []

        visited.add(layer_name)
        all_deps = []

        # Add required dependencies
        for dep in self.get_dependencies(layer_name):
            if dep not in all_deps:
                all_deps.append(dep)
            # Add transitive dependencies
            for trans_dep in self.get_all_dependencies(dep, visited.copy(), include_optional):
                if trans_dep not in all_deps:
                    all_deps.append(trans_dep)

        # Add optional dependencies if requested and they exist
        if include_optional:
            for opt_dep in self.get_optional_dependencies(layer_name):
                if opt_dep in self.layers and opt_dep not in all_deps:
                    all_deps.append(opt_dep)
                    # Add transitive dependencies of optional dependencies
                    for trans_dep in self.get_all_dependencies(opt_dep, visited.copy(), include_optional):
                        if trans_dep not in all_deps:
                            all_deps.append(trans_dep)

        return all_deps

    def check_dependencies(self, layer_name: str) -> Tuple[bool, List[str]]:
        """Check if all dependencies for a layer are available"""
        if layer_name not in self.layers:
            return False, [f"Layer '{layer_name}' not found in search paths"]

        missing_deps = []
        warnings = []

        # Check required dependencies
        required_deps = self.get_all_dependencies(layer_name, include_optional=False)
        for dep in required_deps:
            if dep not in self.layers:
                missing_deps.append(f"Missing required dependency: {dep}")

        # Check optional dependencies separately - these generate warnings only
        optional_deps = self.get_optional_dependencies(layer_name)
        for opt_dep in optional_deps:
            if opt_dep not in self.layers:
                warnings.append(f"Optional dependency not available: {opt_dep}")

        # Check for circular dependencies
        circular = self._check_circular_dependencies(layer_name)
        if circular:
            missing_deps.append(f"Circular dependency detected: {' -> '.join(circular)}")

        # Check provider requirements
        try:
            # Get all layers that would be included in the dependency chain
            build_order = self.get_build_order([layer_name])
            # Provider validation happens inside get_build_order now
        except ValueError as e:
            missing_deps.append(str(e))

        if warnings:
            for warning in warnings:
                print(f"[WARN] {warning}")

        return len(missing_deps) == 0, missing_deps + warnings

    def _check_circular_dependencies(self, layer_name: str, path: Optional[List[str]] = None) -> List[str]:
        """Check for circular dependencies"""
        if path is None:
            path = []

        if layer_name in path:
            return path + [layer_name]  # Found cycle

        if layer_name not in self.layers:
            return []

        path = path + [layer_name]

        for dep in self.get_dependencies(layer_name):
            cycle = self._check_circular_dependencies(dep, path)
            if cycle:
                return cycle

        return []

    def get_build_order(self, target_layers: List[str]) -> List[str]:
        """Get the correct build order for target layers"""
        build_order = []
        processed = set()

        def check_missing_dependencies(layer_name: str, checked: set = None):
            """Recursively check for missing dependencies and raise ValueError if any are found"""
            if checked is None:
                checked = set()

            if layer_name in checked:  # Avoid infinite recursion
                return
            checked.add(layer_name)

            if layer_name not in self.layers:
                raise ValueError(f"Missing required dependency: {layer_name}")

            # Recurse
            for dep in self.get_dependencies(layer_name):
                check_missing_dependencies(dep, checked)

        def add_layer_and_deps(layer_name: str):
            if layer_name in processed:
                return

            # Add required dependencies first
            for dep in self.get_dependencies(layer_name):
                add_layer_and_deps(dep)

            # Add optional dependencies if they exist and are available
            for opt_dep in self.get_optional_dependencies(layer_name):
                if opt_dep in self.layers:
                    add_layer_and_deps(opt_dep)

            if layer_name not in processed:
                build_order.append(layer_name)
                processed.add(layer_name)

        # First, validate that all required dependencies exist
        for layer in target_layers:
            check_missing_dependencies(layer)

        # Then build the order
        for layer in target_layers:
            add_layer_and_deps(layer)

        # Validate that all required providers are satisfied by the build order
        self._validate_provider_requirements(build_order)

        return build_order

    def _validate_provider_requirements(self, build_order: List[str]) -> None:
        """Validate that all required providers are satisfied by layers in the build order"""
        # Check for provider conflicts within the build order scope
        self._check_provider_conflicts_in_scope(build_order)

        # Collect all providers available in the build order
        available_providers = set()
        for layer_name in build_order:
            layer_info = self.get_layer_info(layer_name)
            if layer_info:
                available_providers.update(layer_info.get('provides', []))

        # Check each layer's provider requirements
        for layer_name in build_order:
            layer_info = self.get_layer_info(layer_name)
            if layer_info:
                for required_provider in layer_info.get('provider_requires', []):
                    if required_provider not in available_providers:
                        raise ValueError(f"Layer '{layer_name}' requires provider '{required_provider}' but no layer in the dependency chain provides it")

    def _check_provider_conflicts_in_scope(self, layer_names: List[str]) -> None:
        """Validate that no provider conflicts exist within the given scope of layers."""
        # Build provider mapping only for the layers in scope
        scope_providers = {}
        for layer_name in layer_names:
            layer_info = self.get_layer_info(layer_name)
            if layer_info:
                for provider in layer_info.get('provides', []):
                    if provider in scope_providers:
                        # Found a conflict within scope
                        existing_layer = scope_providers[provider]
                        raise ValueError(f"Provider conflict: '{provider}' is provided by multiple layers: {existing_layer}, {layer_name}")
                    scope_providers[provider] = layer_name

    def _load_layer_yaml(self, filepath: str) -> Optional[dict]:
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                return yaml.safe_load(f)
        except (FileNotFoundError, yaml.YAMLError, UnicodeDecodeError):
            return None

    def _get_mmdebstrap_config(self, layer_name: str) -> Optional[dict]:
        """Get mmdebstrap configuration if present """
        layer_path = self.layer_files.get(layer_name)
        if not layer_path:
            return None

        yaml_data = self._load_layer_yaml(layer_path)
        if not yaml_data:
            return None

        mmdebstrap = yaml_data.get('mmdebstrap')
        if isinstance(mmdebstrap, dict):
            return mmdebstrap

        return None

    def _collect_all_variable_definitions(self, build_order: List[str]) -> Dict[str, List[EnvVariable]]:
        """Collect all variable definitions from all layers in dependency order."""
        variable_definitions = {}

        for position, layer_name in enumerate(build_order):
            meta = self.layers[layer_name]

            # Get all variables from this layer's metadata container
            for var_name, env_var in meta._container.variables.items():
                # Create a new EnvVariable with position and source layer info
                var_with_position = EnvVariable(
                    name=env_var.name,
                    value=env_var.value,
                    description=env_var.description,
                    required=env_var.required,
                    validator=env_var.validator,
                    set_policy=env_var.set_policy,
                    source_layer=layer_name,
                    position=position
                )

                if var_name not in variable_definitions:
                    variable_definitions[var_name] = []
                variable_definitions[var_name].append(var_with_position)

        return variable_definitions

    def _apply_resolved_variables(self, resolved_variables: Dict[str, EnvVariable]):
        """Apply resolved variables to environment and record for file writing."""
        import os

        # The resolver provides variables in the correct dependency order.
        all_vars = list(resolved_variables.values())

        for env_var in all_vars:
            var_name = env_var.name
            value = env_var.value
            policy = env_var.set_policy
            layer_name = env_var.source_layer

            if policy == "force":
                os.environ[var_name] = value
                self._log_env_action("FORCE", var_name, value, layer_name)
                self.write_log[var_name] = value

            elif policy == "immediate":
                if var_name not in os.environ:
                    os.environ[var_name] = value
                    self._log_env_action("SET", var_name, value, layer_name)
                    self.write_log[var_name] = value
                else:
                    print(f"  [SKIP]  {var_name} (already set)")

            elif policy == "lazy":
                if var_name not in os.environ:
                    os.environ[var_name] = value
                    self._log_env_action("LAZY", var_name, value, layer_name)
                    self.write_log[var_name] = value
                else:
                    print(f"  [SKIP]  {var_name} (already set)")

            elif policy == "already_set":
                # Variable was already in environment before we started
                print(f"  [SKIP]  {var_name} (already set)")

            elif policy == "skip":
                if var_name in os.environ:
                    print(f"  [SKIP]  {var_name} (already set)")
                else:
                    print(f"  [SKIP]  {var_name} (Set: false/skip)")


    def _log_env_action(self, tag: str, var: str, value: str, layer_name: str):
        """Log environment variable action."""
        print(f"  [{tag}]  {var}={value} (layer: {layer_name})")



    def apply_env_vars_for_build_order(self, build_order: List[str]) -> bool:
        """Apply environment variables for all layers in build order using three-phase resolution"""
        if not build_order:
            return True

        # Pre-flight validation: ensure all layers exist and schemas are valid
        for layer_name in build_order:
            if layer_name not in self.layers:
                print(f"Layer '{layer_name}' not found")
                return False

            if not self.validate_single_layer_env_vars(layer_name, silent=True, ignore_missing_required=True):
                print(f"Validation failed for layer '{layer_name}' – aborting apply-env")
                return False

        self.write_log = OrderedDict()

        # Phase 1: Collect all variable definitions from all layers in build order
        variable_definitions = self._collect_all_variable_definitions(build_order)

        # Phase 2: Resolve final values using policy rules
        resolver = VariableResolver()
        resolved_variables = resolver.resolve(variable_definitions)

        # Phase 3: Apply resolved variables to environment and file
        self._apply_resolved_variables(resolved_variables)

        print("Environment variables applied successfully")
        return True

    def validate_single_layer_env_vars(self, layer_name: str, silent: bool = False, *, ignore_missing_required: bool = False) -> bool:
        """Validate environment variables for a single layer (no dependency resolution)"""
        if layer_name not in self.layers:
            if not silent:
                print(f"Layer '{layer_name}' not found")
            return False

        meta = self.layers[layer_name]
        results = meta.validate_env_vars()

        layer_valid = True
        for var, result in results.items():
            if result["status"] == "missing_required":
                if ignore_missing_required:
                    continue
                if not silent:
                    print(f"[FAIL] {var} - REQUIRED but not set (layer: {layer_name})")
                layer_valid = False
            elif result["status"] == "missing_required_var":
                if ignore_missing_required:
                    continue
                if not silent:
                    print(f"[FAIL] {result['required_var']} - REQUIRED but not set (layer: {layer_name})")
                layer_valid = False
            elif result["status"] == "validated" and not result["valid"]:
                if not silent:
                    print(f"[FAIL] {var}={result['value']} (invalid, layer: {layer_name})")
                layer_valid = False
            elif result["status"] == "required_validated" and not result["valid"]:
                if not silent:
                    print(f"[FAIL] {result['required_var']}={result['value']} (invalid, layer: {layer_name})")
                layer_valid = False
            # Handle other statuses for info output
            elif not silent:
                if result["status"] == "optional_var_unset":
                    print(f"[INFO] {result['optional_var']} - optional, not set (layer: {layer_name})")
                elif result["status"] == "optional_validated":
                    status = "OK" if result["valid"] else "WARN"
                    print(f"[{status}] {result['optional_var']}={result['value']} (optional, layer: {layer_name})")
                elif result["status"] == "optional_no_validation":
                    print(f"[SKIP] {result['optional_var']}={result['value']} (optional, no validation rule, layer: {layer_name})")

        # Additional check: unsupported layer fields
        unsupported_layer = meta._check_unsupported_layer_fields()
        if unsupported_layer:
            for fld, msg in unsupported_layer.items():
                if not silent:
                    print(f"[ERROR] {msg} (layer: {layer_name})")
            layer_valid = False

        return layer_valid

    def list_layers(self):
        """List available layers grouped by category"""

        BOLD = "\033[1m"
        RESET = "\033[0m"
        MAX_DESC = 60

        # Build category -> [layer_names]
        categories: Dict[str, List[str]] = {}
        for lname in self.layers.keys():
            info = self.get_layer_info(lname)
            if not info:
                continue
            cat = info.get('category', 'general')
            categories.setdefault(cat, []).append(lname)

        # Compute widest layer name for column alignment
        all_layer_names = [n for lst in categories.values() for n in lst]
        name_width = max(len(n) for n in all_layer_names) if all_layer_names else 0

        print("Available layers:")

        for cat in sorted(categories.keys()):
            print(f"{BOLD}Category: {cat}{RESET}")

            for layer_name in sorted(categories[cat]):
                layer_info = self.get_layer_info(layer_name)
                if not layer_info:
                    continue

                # Description trimming
                raw_desc = (layer_info.get('description') or '')
                desc = ' '.join(raw_desc.split())
                if len(desc) > MAX_DESC:
                    desc = desc[: MAX_DESC - 3] + '...'

                # Bold layer name column
                print(f"  {BOLD}{layer_name:<{name_width}}{RESET}  {desc}")

                # Dependencies line
                deps = ', '.join(layer_info['depends']) if layer_info['depends'] else 'none'
                print(f"    deps: {deps}")

                # Capability info
                provides = ', '.join(layer_info.get('provides', [])) or 'none'
                reqprov = ', '.join(layer_info.get('provider_requires', [])) or 'none'
                print(f"    provides: {provides}")
                print(f"    requires-provider: {reqprov}")

    def show_search_paths(self):
        print("Layer search paths:")
        for i, path in enumerate(self.search_paths, 1):
            exists = "✓" if path.exists() else "✗"
            print(f"  {i}. {exists} {path}")

    def resolve_layer_name(self, layer_identifier: str) -> Optional[str]:
        # Direct layer name lookup
        if layer_identifier in self.layers:
            return layer_identifier

        # File path lookup for already loaded layers
        for layer_name, file_path in self.layer_files.items():
            if Path(file_path).resolve() == Path(layer_identifier).resolve():
                return layer_name

        return None

    def process_layers(self, layer_ids: List[str], operation: str, **kwargs) -> bool:
        """Top level API for processing multiple layers with coordinated dependency resolution"""
        # Resolve all target layers first
        resolved_layers = []
        for layer_id in layer_ids:
            layer_name = self.resolve_layer_name(layer_id)
            if layer_name:
                resolved_layers.append(layer_name)
            else:
                if operation == "check":
                    print(f"✗ Layer '{layer_id}' not found")
                else:
                    log_failure(f"Layer '{layer_id}' not found")
                return False

        # Get build order for ALL target layers together (validates providers and dependencies)
        try:
            build_order = self.get_build_order(resolved_layers)
        except ValueError as e:
            if operation == "check":
                log_failure(f"Dependency resolution failed: {e}")
            else:
                log_failure(f"Dependency resolution failed: {e}")
            return False

        # Delegate to appropriate operation
        if operation == "apply":
            if not self.apply_env_vars_for_build_order(build_order):
                return False
            # Final validation for all target layers
            failed_layers = [layer for layer in resolved_layers if not self.validate_single_layer_env_vars(layer)]
            if failed_layers:
                print(f"Validation failed for layers: {', '.join(failed_layers)}; skipping write-out")
                return False

            # Write variables to file if requested
            write_out = kwargs.get('write_out')
            if write_out and self.write_log:
                try:
                    with open(write_out, 'w') as f:
                        for var_name, value in self.write_log.items():
                            f.write(f'{var_name}="{value}"\n')
                    print(f"Environment variables written to: {write_out}")
                except Exception as e:
                    print(f"Error writing to file {write_out}: {e}")
                    return False

            return True

        elif operation == "validate":
            # Validate each target layer individually
            all_valid = True
            for layer_name in resolved_layers:
                if self.validate_single_layer_env_vars(layer_name):
                    log_success(f"Layer '{layer_name}' validation passed")
                else:
                    log_failure(f"Layer '{layer_name}' validation failed")
                    all_valid = False
            return all_valid

        elif operation == "check":
            # If we get here, all dependencies and providers are satisfied
            for layer_name in resolved_layers:
                log_success(f"Layer '{layer_name}' dependencies satisfied")
            return True

        else:
            raise ValueError(f"Unknown operation: {operation}")


def _generate_layer_boilerplate():
    """Generate boilerplate example layer with metadata"""
    boilerplate = """# METABEGIN
# X-Env-Layer-Name: my-example-layer
# X-Env-Layer-Desc: Example layer with options
# X-Env-Layer-Version: 1.0.0
# X-Env-Layer-Provides: debian-base
# X-Env-Layer-RequiresProvider:
# X-Env-Layer-Requires: base-layer,common-tools

# X-Env-VarRequires: SITE
# X-Env-VarRequires-Valid: regex:^/.*,string,string

# X-Env-VarPrefix: example

# X-Env-Var-service_port: 8080
# X-Env-Var-service_port-Desc: Port number for the service
# X-Env-Var-service_port-Required: false
# X-Env-Var-service_port-Valid: int:1024-65535
# X-Env-Var-service_port-Set: true
# METAEND
---
mmdebstrap:
  mirrors:
    - deb http://archive.example.com/debian suite main
  packages:
    - ca-certificates
  setup-hooks:
    - echo hello
  essential-hooks:
    - echo world
  customize-hooks:
    - echo ${SITE}:${IGconf_example_service_port} > ${1}/port.spec
  cleanup-hooks:
    - rm ${1}/port.spec

# Using:
# 1. Copy this template to your desired location.
# 2. Customise the X-Env-* fields for your layer
# 3. Customise the YAML for your use case
# 4. For validation, run: ig meta --help-validation
#
# Notes:
# Depending on script needs, YAML scalar/block constructs may be required."""

    print(boilerplate)


# CLI integration
def LayerManager_register_parser(subparsers, root=None):
    if root:
        default_paths = f'{root}/meta:{root}/device:{root}/image'
        help_text = 'Colon-separated search paths for layers'
    else:
        default_paths = './meta:./device:./image'
        help_text = 'Colon-separated search paths for layers'

    # Use terminal width for help formatting
    terminal_width = shutil.get_terminal_size().columns
    formatter_class = lambda prog: argparse.HelpFormatter(prog, width=terminal_width)

    parser = subparsers.add_parser("layer", help="Layer utilities", add_help=False,
                                   formatter_class=formatter_class)

    class HelpAction(argparse.Action):
        def __init__(self, option_strings, dest=argparse.SUPPRESS, default=argparse.SUPPRESS, help=None):
            super().__init__(option_strings=option_strings, dest=dest, default=default, nargs=0, help=help)

        def __call__(self, parser, namespace, values, option_string=None):
            # Get the current search paths from the --path argument
            current_paths = getattr(namespace, 'path', None) or default_paths
            search_paths = [p.strip() for p in current_paths.split(':') if p.strip()]

            parser.print_help()
            # Then print search path without wrapping
            print(f"\nSearch path: {':'.join(search_paths)}")
            parser.exit()

    parser.add_argument('-h', '--help', action=HelpAction,
                       help='show this help message and exit')

    parser.add_argument('--path', '-p', default=default_paths, help=help_text)
    parser.add_argument('--patterns', nargs='+', default=['*.yaml', '*.yml'],
                       help='File patterns to search (default: *.yaml *.yml)')
    parser.add_argument('--list', '-l', action='store_true',
                       help='List all available layers')
    parser.add_argument('--describe', metavar='LAYER',
                       help='Show detailed information for a layer (use layer name)')
    parser.add_argument('--validate', nargs='+', metavar='LAYER',
                       help='Validate one or more layer(s) metadata and environment variables (use layer names)')
    parser.add_argument('--check', '-c', nargs='+', metavar='LAYER',
                       help='Check dependencies for one or more layer(s) (use layer names)')
    parser.add_argument('--rdep', '--reverse-deps', metavar='LAYER',
                       help='Show layers that depend on the specified layer')
    # Build-order related options
    parser.add_argument('--build-order', '-b', nargs='+', metavar='LAYER',
                       help='Show build order for layers (use layer names)')
    parser.add_argument('--full-paths', action='store_true',
                       help='Include full file paths when showing build order')
    parser.add_argument('--output', metavar='FILE',
                       help='Write build-order list to file (works with --build-order)')
    parser.add_argument('--show-paths', action='store_true',
                       help='Show search paths')
    parser.add_argument('--apply-env', nargs='+', metavar='LAYER',
                       help='Apply environment variables from one or more layers (use layer names, not file paths)')

    parser.add_argument('--write-out', metavar='FILE',
                       help='Write key=value pairs (changed vars) to file (works with --apply-env)')

    parser.add_argument('--gen', action='store_true',
                       help='Generate boilerplate layer template with  metadata')
    parser.add_argument('--help-fields', action='store_true',
                       help='Show reference for X-Env-Layer-* fields')
    parser.set_defaults(func=_layer_main)


def _show_layer_fields_help():
    """Print reference information for X-Env-Layer-* fields"""
    help_text = """
Layer Field Reference (X-Env-Layer-*)
+------------------------------------
Required basics:
  X-Env-Layer-Name        Unique layer identifier (token)
  X-Env-Layer-Version     Version string (free-form)
  X-Env-Layer-Desc        Human-readable description

Optional classification:
  X-Env-Layer-Category           Category/group name (e.g. base, service, device)

Dependencies:
  X-Env-Layer-Requires           Comma-separated concrete layer names this layer needs
  X-Env-Layer-Conflicts          Layers that cannot co-exist with this one

Virtual capabilities:
  X-Env-Layer-Provides           Comma-separated capability tokens this layer offers
  X-Env-Layer-RequiresProvider   Comma-separated capability tokens this layer requires;
                                 each must be provided by exactly one loaded layer

Environment-variable support:
  X-Env-VarPrefix                Prefix applied to IGconf_ env var names in this layer
  (plus all X-Env-Var-* variable definition fields – see `ig meta --help-validation`)

Notes:
  • Provides/RequresProvider enable abstract dependencies; use them instead of hard-coding
    a specific base layer name when multiple alternatives could satisfy the need.
  • Field values are trimmed of whitespace; tokens must be comma-separated without spaces.
"""
    print(help_text)


def _layer_main(args):
    """Main function for layer management CLI"""

    if args.gen:
        _generate_layer_boilerplate()
        return

    if getattr(args, 'help_fields', False):
        _show_layer_fields_help()
        return

    # Create manager with specified paths and patterns
    search_paths = [p.strip() for p in args.path.split(':') if p.strip()]
    manager = LayerManager(search_paths, args.patterns, show_loaded=bool(args.list))
    print()

    if args.show_paths:
        manager.show_search_paths()
        print()

    if args.list:
        # Always show the search paths when listing layers
        manager.show_search_paths()
        print()
        manager.list_layers()

    if args.describe:
        layer_name = manager.resolve_layer_name(args.describe)
        if not layer_name:
            print(f"✗ Layer '{args.describe}' not found")
            exit(1)

        layer_info = manager.get_layer_info(layer_name)
        if layer_info:
            print(f"Layer: {layer_info['name']}")
            print(f"Version: {layer_info['version']}")
            print(f"Category: {layer_info['category']}")
            print(f"Description: {layer_info['description']}")

            if layer_info.get('provides'):
                provides_list = ', '.join(layer_info['provides'])
                print(f"Provides: {provides_list}")

            if layer_info.get('provider_requires'):
                requires_list = ', '.join(layer_info['provider_requires'])
                print(f"Requires Provider: {requires_list}")

            layer_path = manager.layer_files.get(layer_name, "<unknown>")
            rel_layer_path = layer_path
            for sp in manager.search_paths:
                try:
                    rel_layer_path = Path(layer_path).relative_to(sp)
                    break
                except Exception:
                    continue
            print(f"  Path: {rel_layer_path}")

            if layer_info['depends']:
                print("Depends:")

                def _show_deps(dep_layer: str, seen: set[str], indent: int = 1):
                    pad = "  " * indent
                    for dep in manager.get_dependencies(dep_layer):
                        # guard against cycles / duplicates
                        if dep in seen:
                            print(f"{pad}└─ {dep} (already shown)")
                            continue
                        seen.add(dep)

                        dep_path = manager.layer_files.get(dep, "<unknown>")
                        rel_path = dep_path
                        for sp in manager.search_paths:
                            try:
                                rel_path = Path(dep_path).resolve().relative_to(sp)
                                break
                            except Exception:
                                continue
                        print(f"{pad}- {dep}: {rel_path}")

                        # recurse into dependencies of this dependency
                        _show_deps(dep, seen, indent + 1)

                _show_deps(layer_name, set())
            if layer_info['optional_depends']:
                print(f"Optional-Depends: {', '.join(layer_info['optional_depends'])}")
            if layer_info['conflicts']:
                print(f"Conflicts: {', '.join(layer_info['conflicts'])}")

            # Show mmdebstrap configuration if any
            # TODO can extend for other maps
            mmdebstrap_config = manager._get_mmdebstrap_config(layer_name)
            if mmdebstrap_config:
                print()

                architectures = mmdebstrap_config.get('architectures')
                if architectures and isinstance(architectures, list):
                    arch_list = ', '.join(architectures)
                    print(f"Architectures: {arch_list}")

                packages = mmdebstrap_config.get('packages')
                if packages and isinstance(packages, list):
                    print("Packages:")
                    for package in packages:
                        print(f"  - {package}")

            # Print environment variables for this layer
            meta_obj = manager.layers.get(layer_name)
            if meta_obj and meta_obj.get_all_env_vars():
                print()
                print_env_var_descriptions(meta_obj, indent=2)

    if args.validate:
        if not manager.process_layers(args.validate, "validate"):
            exit(1)

    if args.check:
        if not manager.process_layers(args.check, "check"):
            exit(1)

    if args.rdep:
        layer_name = manager.resolve_layer_name(args.rdep)
        if not layer_name:
            print(f"✗ Layer '{args.rdep}' not found")
            exit(1)

        reverse_deps = manager.get_reverse_dependencies(layer_name)

        if reverse_deps:
            print(f"Reverse dependencies for '{layer_name}':")
            print()
            for dep_layer in reverse_deps:
                dep_info = manager.get_layer_info(dep_layer)
                if dep_info:
                    print(f"Layer: {dep_info['name']}")
                    print(f"Category: {dep_info.get('category', 'unknown')}")
                    print(f"Description: {dep_info.get('description', 'No description')}")
                    print()

            print(f"{len(reverse_deps)} layer(s) depend on '{layer_name}'")

    if args.build_order:
        # Resolve all layer names
        resolved_layers = []
        for layer_id in args.build_order:
            layer_name = manager.resolve_layer_name(layer_id)
            if layer_name:
                resolved_layers.append(layer_name)
            else:
                print(f"✗ Layer '{layer_id}' not found")
                exit(1)

        build_order = manager.get_build_order(resolved_layers)

        # Prepare output lines
        output_display = []  # what goes to stdout
        output_file = []     # what goes to --output file
        if build_order:
            print("Build order:")

            # Compute dynamic column widths
            num_width = len(str(len(build_order)))
            name_width = max(len(l) for l in build_order) if args.full_paths else 0

            for i, layer in enumerate(build_order, 1):
                if args.full_paths:
                    path = manager.layer_files.get(layer, "<unknown>")
                    display_line = (
                        f"  {i:{num_width}d}. "
                        f"{layer:<{name_width}}  "
                        f"{path}"
                    )
                    file_line = f"{layer}=\"{path}\""
                else:
                    display_line = f"  {i:{num_width}d}. {layer}"
                    file_line = layer

                print(display_line)
                output_display.append(display_line)
                output_file.append(file_line)
        else:
            print("No layers to build")

        # Optionally write to file
        if args.output and output_file:
            try:
                with open(args.output, 'w') as f:
                    for line in output_file:
                        f.write(line + "\n")
                print(f"Build order written to: {args.output}")
            except Exception as e:
                print(f"Error writing build order to {args.output}: {e}")

    if args.apply_env:
        if not manager.process_layers(args.apply_env, "apply", write_out=getattr(args, 'write_out', None)):
            exit(1)

        return

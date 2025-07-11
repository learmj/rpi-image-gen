import os
import glob
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

# Import our metadata parser
from metadata_parser import Metadata


class LayerManager:
    """
    Lightweight layer manager that delegates all parsing to the Metadata class.
    Handles layer discovery, dependency resolution, and orchestration.
    """

    def __init__(self, search_paths: Optional[List[str]] = None, file_patterns: Optional[List[str]] = None):
        """Initialize LayerManager with search paths and file patterns"""
        if search_paths is None:
            search_paths = ['./meta']
        if file_patterns is None:
            file_patterns = ['*.yaml', '*.yml']

        self.search_paths = [Path(p).resolve() for p in search_paths]
        self.file_patterns = file_patterns
        self.layers: Dict[str, Metadata] = {}  # layer_name -> Metadata object
        self.layer_files: Dict[str, str] = {}  # layer_name -> file_path

        # Validate search paths exist
        for path in self.search_paths:
            if not path.exists():
                print(f"Warning: Search path '{path}' does not exist")

        self.load_layers()

    def load_layers(self):
        """Discover and load all layer files, creating Metadata objects for each"""
        loaded_layers = set()

        for search_path in self.search_paths:
            if not search_path.exists():
                continue

            print(f"Searching for layers recursively in: {search_path}")

            # Find all matching files
            all_files = []
            for pattern in self.file_patterns:
                files = glob.glob(str(search_path / "**" / pattern), recursive=True)
                all_files.extend(files)

            for metadata_file in all_files:
                try:
                    # Let Metadata class handle all parsing and validation
                    meta = Metadata(metadata_file)

                    # Check if this file contains layer information
                    layer_info = meta.get_unified_layer_info()
                    if not layer_info:
                        continue

                    layer_name = layer_info['name']

                    # Skip if already loaded (first found wins)
                    if layer_name in loaded_layers:
                        print(f"  Skipping {layer_name} (already found in earlier search path)")
                        continue

                    # Store the Metadata object and file path
                    self.layers[layer_name] = meta
                    self.layer_files[layer_name] = metadata_file
                    loaded_layers.add(layer_name)

                    relative_path = Path(metadata_file).relative_to(search_path)
                    metadata_type = 'x-env-layer' if meta.has_x_env_layer_info() else 'standard'
                    print(f"  Loaded layer: {layer_name} from {relative_path} ({metadata_type})")

                except Exception:
                    # Skip files that don't have valid metadata or layer info
                    continue

    def get_layer_info(self, layer_name: str) -> Optional[dict]:
        """Get layer information - delegates to Metadata class"""
        if layer_name not in self.layers:
            return None
        return self.layers[layer_name].get_unified_layer_info()

    def get_dependencies(self, layer_name: str) -> List[str]:
        """Get direct required dependencies for a layer"""
        layer_info = self.get_layer_info(layer_name)
        return layer_info['depends'] if layer_info else []

    def get_optional_dependencies(self, layer_name: str) -> List[str]:
        """Get direct optional dependencies for a layer"""
        layer_info = self.get_layer_info(layer_name)
        return layer_info['optional_depends'] if layer_info else []

    def get_conflicts(self, layer_name: str) -> List[str]:
        """Get conflicts for a layer"""
        layer_info = self.get_layer_info(layer_name)
        return layer_info['conflicts'] if layer_info else []

    def get_all_dependencies(self, layer_name: str, visited: Optional[Set[str]] = None, include_optional: bool = True) -> List[str]:
        """Get all dependencies (including transitive) for a layer"""
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

        # Print warnings about optional dependencies
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

        def add_layer_and_deps(layer_name: str):
            if layer_name in processed or layer_name not in self.layers:
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

        for layer in target_layers:
            add_layer_and_deps(layer)

        return build_order

    def apply_layer_env_vars(self, layer_name: str, write_out: Optional[str] = None) -> bool:
        """Apply environment variables from a layer - delegates to Metadata class"""
        if layer_name not in self.layers:
            print(f"Layer '{layer_name}' not found")
            return False

        meta = self.layers[layer_name]

        try:
            # Delegate all the work to the Metadata class
            results = meta.set_env_vars()

            # Validate that all required variables are now set
            validation_results = meta.validate_env_vars()
            has_validation_errors = any(
                result["status"] in ["missing_required", "missing_required_var"] or
                (result["status"] in ["validated", "required_validated"] and not result["valid"])
                for result in validation_results.values()
            )

            if has_validation_errors:
                print(f"Cannot apply environment variables for layer '{layer_name}' - validation failed")
                return False

            print(f"Applied environment variables for layer: {layer_name}")
            for var, result in results.items():
                if result["status"] == "set":
                    print(f"  [SET] {var}={result['value']}")
                elif result["status"] == "already_set":
                    print(f"  [SKIP] {var} (already set)")

            # Write key=value pairs to file if requested
            if write_out:
                try:
                    all_vars = meta.get_all_env_vars()
                    with open(write_out, 'a') as f:
                        for fullvar, default_value in all_vars.items():
                            env_value = os.environ.get(fullvar, default_value)
                            f.write(f"{fullvar}={env_value}\n")
                    print(f"Environment variables written to: {write_out}")
                except Exception as e:
                    print(f"Error writing to file {write_out}: {e}")
                    return False

            print("Environment variables applied successfully")
            return True

        except Exception as e:
            print(f"Error applying environment variables for layer '{layer_name}': {e}")
            return False

    def validate_layer_env_vars(self, layer_name: str, silent: bool = False) -> bool:
        """Validate environment variables for a layer - delegates to Metadata class"""
        if layer_name not in self.layers:
            if not silent:
                print(f"Layer '{layer_name}' not found")
            return False

        meta = self.layers[layer_name]
        results = meta.validate_env_vars()

        all_valid = True
        for var, result in results.items():
            if result["status"] == "missing_required":
                if not silent:
                    print(f"[FAIL] {var} - REQUIRED but not set")
                all_valid = False
            elif result["status"] == "missing_required_var":
                if not silent:
                    print(f"[FAIL] {result['required_var']} - REQUIRED but not set")
                all_valid = False
            elif result["status"] == "validated" and not result["valid"]:
                if not silent:
                    print(f"[FAIL] {var}={result['value']} (invalid)")
                all_valid = False
            elif result["status"] == "required_validated" and not result["valid"]:
                if not silent:
                    print(f"[FAIL] {result['required_var']}={result['value']} (invalid)")
                all_valid = False
            # Handle other statuses for info output
            elif not silent:
                if result["status"] == "optional_var_unset":
                    print(f"[INFO] {result['optional_var']} - optional, not set")
                elif result["status"] == "optional_validated":
                    status = "OK" if result["valid"] else "WARN"
                    print(f"[{status}] {result['optional_var']}={result['value']} (optional)")
                elif result["status"] == "optional_no_validation":
                    print(f"[SKIP] {result['optional_var']}={result['value']} (optional, no validation rule)")

        return all_valid

    def list_layers(self):
        """List all available layers"""
        print("Available layers:")
        for layer_name in self.layers:
            layer_info = self.get_layer_info(layer_name)
            if layer_info:
                deps = ', '.join(layer_info['depends']) if layer_info['depends'] else 'none'
                print(f"  {layer_name}: {layer_info['description']} (deps: {deps})")

    def show_search_paths(self):
        """Show configured search paths"""
        print("Layer search paths:")
        for i, path in enumerate(self.search_paths, 1):
            exists = "✓" if path.exists() else "✗"
            print(f"  {i}. {exists} {path}")

    def resolve_layer_name(self, layer_identifier: str) -> Optional[str]:
        """Resolve layer identifier to layer name"""
        # Direct layer name lookup
        if layer_identifier in self.layers:
            return layer_identifier

        # File path lookup for already loaded layers
        for layer_name, file_path in self.layer_files.items():
            if Path(file_path).resolve() == Path(layer_identifier).resolve():
                return layer_name

        return None




# CLI
def LayerManager_register_parser(subparsers):
    """Register layer management commands with argparse"""
    parser = subparsers.add_parser("layer", help="Layer dependency management")
    parser.add_argument('--paths', '-p', nargs='+', default=['./meta'],
                       help='Search paths for layers (default: ./meta)')
    parser.add_argument('--patterns', nargs='+', default=['*.yaml', '*.yml'],
                       help='File patterns to search (default: *.yaml *.yml)')
    parser.add_argument('--list', '-l', action='store_true',
                       help='List all available layers')
    parser.add_argument('--info', '-i', metavar='LAYER',
                       help='Show detailed information for a layer (use layer name)')
    parser.add_argument('--validate', metavar='LAYER',
                       help='Validate layer metadata and environment variables (use layer name)')
    parser.add_argument('--check', '-c', metavar='LAYER',
                       help='Check dependencies for a layer (use layer name)')
    parser.add_argument('--build-order', '-b', nargs='+', metavar='LAYER',
                       help='Show build order for layers (use layer names)')
    parser.add_argument('--show-paths', action='store_true',
                       help='Show search paths')
    parser.add_argument('--apply-env', metavar='LAYER',
                       help='Apply environment variables from layer (use layer name, not file path)')
    parser.add_argument('--validate-env', metavar='LAYER',
                       help='Validate environment variables for layer (use layer name)')
    parser.add_argument('--write-out', metavar='FILE',
                       help='Write key=value pairs to file (works with --apply-env)')
    parser.set_defaults(func=_layer_main)


def _layer_main(args):
    """Main function for layer management CLI"""
    # Create manager with specified paths and patterns
    manager = LayerManager(args.paths, args.patterns)
    print()

    if args.show_paths:
        manager.show_search_paths()
        print()

    if args.list:
        manager.list_layers()

    if args.info:
        layer_name = manager.resolve_layer_name(args.info)
        if not layer_name:
            print(f"✗ Layer '{args.info}' not found")
            exit(1)

        layer_info = manager.get_layer_info(layer_name)
        if layer_info:
            print(f"Layer: {layer_info['name']}")
            print(f"Version: {layer_info['version']}")
            print(f"Category: {layer_info['category']}")
            print(f"Description: {layer_info['description']}")
            print(f"Config File: {layer_info['config_file']}")
            if layer_info['depends']:
                print(f"Depends: {', '.join(layer_info['depends'])}")
            if layer_info['optional_depends']:
                print(f"Optional-Depends: {', '.join(layer_info['optional_depends'])}")
            if layer_info['conflicts']:
                print(f"Conflicts: {', '.join(layer_info['conflicts'])}")

    if args.validate:
        layer_name = manager.resolve_layer_name(args.validate)
        if not layer_name:
            print(f"✗ Layer '{args.validate}' not found")
            exit(1)

        # First, validate the layer's dependencies
        is_valid, errors = manager.check_dependencies(layer_name)
        if not is_valid:
            print(f"✗ Layer '{layer_name}' has dependency issues:")
            for error in errors:
                print(f"  - {error}")
            exit(1)

        # Then validate the layer's environment variables
        if manager.validate_layer_env_vars(layer_name):
            print("✓ Layer validation passed")
        else:
            print("✗ Layer validation failed")
            exit(1)

    if args.check:
        layer_name = manager.resolve_layer_name(args.check)
        if not layer_name:
            print(f"✗ Layer '{args.check}' not found")
            exit(1)

        is_valid, errors = manager.check_dependencies(layer_name)
        if is_valid:
            print(f"✓ Layer '{layer_name}' dependencies satisfied")
        else:
            print(f"✗ Layer '{layer_name}' has issues:")
            for error in errors:
                print(f"  - {error}")
            exit(1)

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
        if build_order:
            print(f"Build order: {' -> '.join(build_order)}")
        else:
            print("No layers to build")

    if args.apply_env:
        layer_name = manager.resolve_layer_name(args.apply_env)
        if not layer_name:
            print(f"✗ Layer '{args.apply_env}' not found")
            print(f"💡 Tip: Use --paths to specify search directories, then reference by layer name")
            exit(1)

        write_out = getattr(args, 'write_out', None)
        if not manager.apply_layer_env_vars(layer_name, write_out):
            exit(1)

    if args.validate_env:
        layer_name = manager.resolve_layer_name(args.validate_env)
        if not layer_name:
            print(f"✗ Layer '{args.validate_env}' not found")
            exit(1)

        if manager.validate_layer_env_vars(layer_name):
            print("✓ All environment variables valid")
        else:
            print("✗ Some environment variables are invalid")
            exit(1)

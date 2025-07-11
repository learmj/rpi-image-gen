import os
import re
import argparse
from debian import deb822

# Central definition of supported field patterns
SUPPORTED_FIELD_PATTERNS = {
    # Variable prefix
    "X-Env-VarPrefix": {"type": "single", "description": "Variable prefix for environment variables"},

    # Layer management fields
    "X-Env-Layer-Name": {"type": "single", "description": "Layer name identifier"},
    "X-Env-Layer-Description": {"type": "single", "description": "Layer description"},
    "X-Env-Layer-Version": {"type": "single", "description": "Layer version"},
    "X-Env-Layer-Requires": {"type": "single", "description": "Required layer dependencies"},
    "X-Env-Layer-Optional-Requires": {"type": "single", "description": "Optional layer dependencies"},
    "X-Env-Layer-Conflicts": {"type": "single", "description": "Conflicting layers"},
    "X-Env-Layer-Config-File": {"type": "single", "description": "Configuration file name"},
    "X-Env-Layer-Category": {"type": "single", "description": "Layer category"},

    # Variable definition patterns (these match multiple fields)
    "X-Env-Var-": {"type": "pattern", "description": "Environment variable definition"},
    "X-Env-Var-*-Desc": {"type": "pattern", "description": "Environment variable description"},
    "X-Env-Var-*-Required": {"type": "pattern", "description": "Whether variable is required"},
    "X-Env-Var-*-Valid": {"type": "pattern", "description": "Variable validation rule"},
    "X-Env-Var-*-Set": {"type": "pattern", "description": "Whether to auto-set variable"},

    # Variable requirements (any environment variables)
    "X-Env-VarRequires": {"type": "single", "description": "Environment variables required by this layer"},
    "X-Env-VarRequires-Valid": {"type": "single", "description": "Validation rules for required environment variables"},
    "X-Env-VarOptional": {"type": "single", "description": "Optional environment variables used by this layer"},
    "X-Env-VarOptional-Valid": {"type": "single", "description": "Validation rules for optional environment variables"},
}

def is_field_supported(field_name: str) -> bool:
    """Check if a field name is supported based on our defined patterns"""
    # Check exact matches first
    if field_name in SUPPORTED_FIELD_PATTERNS:
        return True

    # Check pattern matches
    for pattern, info in SUPPORTED_FIELD_PATTERNS.items():
        if info["type"] == "pattern":
            if pattern.endswith("-"):
                # Variable base pattern (X-Env-Var-)
                if field_name.startswith(pattern) and '-' not in field_name[len(pattern):]:
                    return True
            elif "*" in pattern:
                # Variable attribute pattern (X-Env-Var-*-Desc)
                pattern_parts = pattern.split("*")
                if len(pattern_parts) == 2:
                    prefix, suffix = pattern_parts
                    if field_name.startswith(prefix) and field_name.endswith(suffix):
                        # Extract the variable name part
                        var_part = field_name[len(prefix):-len(suffix) if suffix else len(field_name)]
                        if var_part and '-' not in var_part:  # Valid variable name
                            return True

    return False

def get_supported_fields_list() -> list:
    """Get a formatted list of supported fields for display"""
    fields = []
    for field, info in SUPPORTED_FIELD_PATTERNS.items():
        if info["type"] == "single":
            fields.append(field)
        elif info["type"] == "pattern":
            fields.append(field.replace("*", "*"))  # Keep the * for display
    return sorted(fields)

class Metadata:
    def __init__(self, filepath):
        self.filepath = filepath
        self._metadata = self._load_metadata(filepath)

    def _load_metadata(self, path):
        if not os.path.isfile(path):
            raise FileNotFoundError(f"Metadata source not found: {path}")

        with open(path, 'r', encoding='utf-8') as f:
            lines = f.readlines()

        # Detect and extract metadata block if embedded
        if any(line.strip().startswith('# METABEGIN') for line in lines):
            in_meta = False
            meta_lines = []
            for line in lines:
                line = line.strip()
                if line.startswith('# METABEGIN'):
                    in_meta = True
                    continue
                elif line.startswith('# METAEND'):
                    break
                elif in_meta:
                    if line.startswith('#'):
                        meta_lines.append(line.lstrip('#').strip())

            # Filter out empty lines for deb822 compat
            non_empty_lines = [line for line in meta_lines if line.strip()]

            # Check for malformed before parsing
            for line_num, line in enumerate(non_empty_lines, 1):
                line = line.strip()
                if line and ':' not in line:
                    raise ValueError(f"Malformed metadata line {line_num}: '{line}' - metadata fields must be in 'Field: value' format")

            meta_str = "\n".join(non_empty_lines)
        else:
            meta_str = "".join(lines)

        try:
            return deb822.Deb822(meta_str)
        except Exception as e:
            raise ValueError(f"Failed to parse metadata: {e}")

    def get_metadata(self):
        return self._metadata

    def get_env_vars(self):
        """Get environment variables that are not currently set in the environment"""
        return self._get_env_vars_internal(only_unset=True)

    def get_all_env_vars(self):
        """Get all environment variables defined in metadata, regardless of whether they're set in environment"""
        return self._get_env_vars_internal(only_unset=False)

    def _get_env_vars_internal(self, only_unset=True):
        """Internal method to get environment variables with optional filtering"""
        result = {}
        if not self._metadata:
            return result

        # Check for unsupported fields first
        unsupported_fields = self._check_unsupported_fields()
        if unsupported_fields:
            raise ValueError(f"Cannot process variables with unsupported fields: {list(unsupported_fields.keys())}. Run 'validate' command for details.")

        # Check if variables are defined but no prefix is provided
        has_env_vars = any(key.startswith("X-Env-Var-") and is_field_supported(key) and '-' not in key[len("X-Env-Var-"):]
                          for key in self._metadata.keys())
        prefix = self._metadata.get("X-Env-VarPrefix", "").lower()

        if has_env_vars and not prefix:
            raise ValueError("Cannot process variables: X-Env-Var-* fields are defined but X-Env-VarPrefix is missing. Environment variables require a valid prefix.")

        for key in self._metadata.keys():
            # Use centralised field checking for variable definitions
            if key.startswith("X-Env-Var-") and is_field_supported(key) and '-' not in key[len("X-Env-Var-"):]:
                varname = key[len("X-Env-Var-"):].lower()
                fullvar = f"IGconf_{prefix}_{varname}"

                # Apply filtering based on only_unset parameter
                if only_unset:
                    if fullvar not in os.environ:
                        result[fullvar] = self._metadata[key]
                else:
                    result[fullvar] = self._metadata[key]

        return result

    def apply_env_vars(self):
        envs = self.get_env_vars()
        for k, v in envs.items():
            os.environ[k] = v

    def get_variable_description(self, varname):
        desc_key = f"X-Env-Var-{varname.upper()}-Desc"
        valid_key = f"X-Env-Var-{varname.upper()}-Valid"
        name_key = f"X-Env-Var-{varname.upper()}"
        return {
            "name": name_key,
            "description": self._metadata.get(desc_key, ""),
            "valid": self._metadata.get(valid_key, ""),
            "value": self._metadata.get(name_key, "")
        }

    def _parse_boolean(self, value, default=False):
        """Parse a boolean value from string using same logic as bool validation"""
        if not value:
            return default
        return str(value).lower() in ["true", "1", "yes", "y"]

    def _is_valid(self, value, rule):
        if rule == "string":
            return value is not None and isinstance(value, str) and value.strip() != ""
        if rule == "string-or-unset":
            return value is None or (isinstance(value, str) and value.strip() != "")
        if rule == "int":
            return value.isdigit() if value else False
        if rule.startswith("int:"):
            try:
                min_val, max_val = map(int, rule[4:].split("-"))
                return value is not None and value.isdigit() and min_val <= int(value) <= max_val
            except Exception:
                return False
        if rule == "bool":
            return str(value).lower() in ["true", "false", "1", "0", "yes", "no", "y", "n"]
        if rule.startswith("regex:"):
            pattern = rule[len("regex:"):]
            try:
                return bool(re.fullmatch(pattern, value or ""))
            except re.error:
                return False
        if "," in rule:
            allowed = [v.strip() for v in rule.split(",")]
            return value in allowed
        return True  # fallback if no known rule

    def _check_unsupported_fields(self):
        """Check for unsupported field names and return error messages"""
        unsupported_fields = {}

        # Check all fields against our centralized supported field patterns
        for field_name in self._metadata.keys():
            if not is_field_supported(field_name):
                unsupported_fields[field_name] = f"'{field_name}' is not supported"

        return unsupported_fields

    def validate_env_vars(self):
        """Validate environment variables - check required vars and validate set vars"""
        results = {}
        if not self._metadata:
            return results

        # First, check for unsupported field names and fail validation
        unsupported_fields = self._check_unsupported_fields()
        if unsupported_fields:
            for field_name, message in unsupported_fields.items():
                results[f"UNSUPPORTED_FIELD_{field_name}"] = {
                    "status": "unsupported_field",
                    "value": self._metadata.get(field_name),
                    "valid": False,
                    "required": False,
                    "message": message
                }
            return results

        # Check if variables are defined but no prefix is provided
        has_env_vars = any(key.startswith("X-Env-Var-") and is_field_supported(key) and '-' not in key[len("X-Env-Var-"):]
                          for key in self._metadata.keys())
        prefix = self._metadata.get("X-Env-VarPrefix", "").lower()

        if has_env_vars and not prefix:
            results["MISSING_VAR_PREFIX"] = {
                "status": "missing_var_prefix",
                "value": None,
                "valid": False,
                "required": True,
                "message": "X-Env-Var-* fields are defined but X-Env-VarPrefix is missing. Environment variables require a valid prefix."
            }
            return results

        for key in self._metadata.keys():
            # Use centralized field checking for variable definitions
            if key.startswith("X-Env-Var-") and is_field_supported(key) and '-' not in key[len("X-Env-Var-"):]:
                varname = key[len("X-Env-Var-"):].lower()
                fullvar = f"IGconf_{prefix}_{varname}"

                required = self._parse_boolean(self._metadata.get(f"X-Env-Var-{varname.upper()}-Required", ""), default=False)
                valid_rule = self._metadata.get(f"X-Env-Var-{varname.upper()}-Valid")
                current_value = os.environ.get(fullvar)

                if required and current_value is None:
                    results[fullvar] = {
                        "status": "missing_required",
                        "value": None,
                        "valid": False,
                        "required": True
                    }
                elif current_value is not None:
                    # Variable is set - validate it
                    if valid_rule:
                        is_valid = self._is_valid(current_value, valid_rule)
                        results[fullvar] = {
                            "status": "validated",
                            "value": current_value,
                            "valid": is_valid,
                            "required": required,
                            "rule": valid_rule
                        }
                    else:
                        results[fullvar] = {
                            "status": "no_validation",
                            "value": current_value,
                            "valid": None,
                            "required": required
                        }
                else:
                    # Variable not set and not required
                    results[fullvar] = {
                        "status": "optional_unset",
                        "value": None,
                        "valid": None,
                        "required": False
                    }

        # Check required environment variables
        required_vars = self._metadata.get("X-Env-VarRequires", "")
        required_valid_rules = self._metadata.get("X-Env-VarRequires-Valid", "")

        if required_vars.strip():
            var_list = [v.strip() for v in required_vars.split(',') if v.strip()]
            valid_rules = [r.strip() for r in required_valid_rules.split(',') if r.strip()] if required_valid_rules.strip() else []

            for i, req_var in enumerate(var_list):
                current_value = os.environ.get(req_var)
                valid_rule = valid_rules[i] if i < len(valid_rules) else None

                if current_value is None:
                    results[f"REQUIRED_{req_var}"] = {
                        "status": "missing_required_var",
                        "value": None,
                        "valid": False,
                        "required": True,
                        "required_var": req_var
                    }
                else:
                    # Required variable is set - validate it if rule provided
                    if valid_rule:
                        is_valid = self._is_valid(current_value, valid_rule)
                        results[f"REQUIRED_{req_var}"] = {
                            "status": "required_validated",
                            "value": current_value,
                            "valid": is_valid,
                            "required": True,
                            "rule": valid_rule,
                            "required_var": req_var
                        }
                    else:
                        results[f"REQUIRED_{req_var}"] = {
                            "status": "required_no_validation",
                            "value": current_value,
                            "valid": None,
                            "required": True,
                            "required_var": req_var
                        }

        # Check optional environment variables
        optional_vars = self._metadata.get("X-Env-VarOptional", "")
        optional_valid_rules = self._metadata.get("X-Env-VarOptional-Valid", "")

        if optional_vars.strip():
            var_list = [v.strip() for v in optional_vars.split(',') if v.strip()]
            valid_rules = [r.strip() for r in optional_valid_rules.split(',') if r.strip()] if optional_valid_rules.strip() else []

            for i, opt_var in enumerate(var_list):
                current_value = os.environ.get(opt_var)
                valid_rule = valid_rules[i] if i < len(valid_rules) else None

                if current_value is None:
                    results[f"OPTIONAL_{opt_var}"] = {
                        "status": "optional_var_unset",
                        "value": None,
                        "valid": None,
                        "required": False,
                        "optional_var": opt_var
                    }
                else:
                    # Optional variable is set - validate it if rule provided
                    if valid_rule:
                        is_valid = self._is_valid(current_value, valid_rule)
                        results[f"OPTIONAL_{opt_var}"] = {
                            "status": "optional_validated",
                            "value": current_value,
                            "valid": is_valid,
                            "required": False,
                            "rule": valid_rule,
                            "optional_var": opt_var
                        }
                    else:
                        results[f"OPTIONAL_{opt_var}"] = {
                            "status": "optional_no_validation",
                            "value": current_value,
                            "valid": None,
                            "required": False,
                            "optional_var": opt_var
                        }

        return results

    def set_env_vars(self):
        """Set environment variables from metadata defaults (unless marked Set: false)"""
        results = {}
        if not self._metadata:
            return results

        # Check for unsupported fields first
        unsupported_fields = self._check_unsupported_fields()
        if unsupported_fields:
            raise ValueError(f"Cannot process variables with unsupported fields: {list(unsupported_fields.keys())}. Run 'validate' command for details.")

        # Check if variables are defined but no prefix is provided
        has_env_vars = any(key.startswith("X-Env-Var-") and is_field_supported(key) and '-' not in key[len("X-Env-Var-"):]
                          for key in self._metadata.keys())
        prefix = self._metadata.get("X-Env-VarPrefix", "").lower()

        if has_env_vars and not prefix:
            raise ValueError("Cannot process variables: X-Env-Var-* fields are defined but X-Env-VarPrefix is missing. Environment variables require a valid prefix.")

        for key in self._metadata.keys():
            # Use centralized field checking for variable definitions
            if key.startswith("X-Env-Var-") and is_field_supported(key) and '-' not in key[len("X-Env-Var-"):]:
                varname = key[len("X-Env-Var-"):].lower()
                fullvar = f"IGconf_{prefix}_{varname}"

                # Default behavior: set if not already set (unless Set: false)
                can_set = self._parse_boolean(self._metadata.get(f"X-Env-Var-{varname.upper()}-Set", "true"), default=True)
                metadata_value = self._metadata[key]
                current_value = os.environ.get(fullvar)

                if current_value is None and can_set:
                    # Variable not set and allowed to be set
                    # Special handling for empty values with string-or-unset validation
                    valid_rule = self._metadata.get(f"X-Env-Var-{varname.upper()}-Valid")
                    if valid_rule == "string-or-unset" and not metadata_value.strip():
                        # Empty value with string-or-unset validation should remain unset
                        results[fullvar] = {
                            "status": "no_set_policy",
                            "value": None,
                            "reason": "empty value with string-or-unset validation"
                        }
                    else:
                        # Set the variable normally
                        os.environ[fullvar] = metadata_value
                        results[fullvar] = {
                            "status": "set",
                            "value": metadata_value,
                            "reason": "auto-set from metadata"
                        }
                elif current_value is not None:
                    results[fullvar] = {
                        "status": "already_set",
                        "value": current_value,
                        "reason": "already in environment"
                    }
                else:
                    # Variable not set and marked as Set: false
                    results[fullvar] = {
                        "status": "no_set_policy",
                        "value": None,
                        "reason": "marked as Set: false"
                    }

        return results

    def get_x_env_layer_info(self):
        """Get layer management information from X-Env-Layer metadata fields"""
        if not self._metadata:
            return None

        # Check for X-Env-Layer-Name field (primary identifier)
        layer_name = self._metadata.get("X-Env-Layer-Name", "")
        if not layer_name:
            return None

        return {
            "name": layer_name,
            "depends": self._parse_x_env_dependency_list(self._metadata.get("X-Env-Layer-Requires", "")),
            "optional_depends": self._parse_x_env_dependency_list(self._metadata.get("X-Env-Layer-Optional-Requires", "")),
            "conflicts": self._parse_x_env_dependency_list(self._metadata.get("X-Env-Layer-Conflicts", "")),
            "description": self._metadata.get("X-Env-Layer-Description", ""),
            "config_file": self._metadata.get("X-Env-Layer-Config-File", f"{layer_name}.yaml"),
            "version": self._metadata.get("X-Env-Layer-Version", "1.0.0"),
            "category": self._metadata.get("X-Env-Layer-Category", "general")
        }

    def _parse_x_env_dependency_list(self, depends_str: str) -> list:
        """Parse X-Env-Layer dependency string into list of layer names/IDs"""
        if not depends_str.strip():
            return []

        deps = []
        for dep in depends_str.split(','):
            # Clean up whitespace and add to list
            dep_name = dep.strip()
            if dep_name:
                deps.append(dep_name)
        return deps

    def has_x_env_layer_info(self) -> bool:
        """Check if this metadata contains X-Env-Layer information"""
        return bool(self._metadata and self._metadata.get("X-Env-Layer-Name"))

    def get_unified_layer_info(self):
        """Get layer information in unified format (X-Env-* fields only)"""
        if self.has_x_env_layer_info():
            return self.get_x_env_layer_info()
        else:
            return None

    def has_layer_info(self) -> bool:
        """Check if this metadata contains layer information (X-Env-* only)"""
        return self.has_x_env_layer_info()

    def get_layer_info(self):
        """Get layer management information (X-Env-* only, for compatibility)"""
        return self.get_x_env_layer_info()


def _generate_boilerplate():
    """Generate boilerplate metadata with all supported fields and examples"""
    boilerplate = """# METABEGIN
# Layer Management Fields
# X-Env-Layer-Name: my-layer
# X-Env-Layer-Description: Layer description
# X-Env-Layer-Version: 1.0.0
# X-Env-Layer-Category: general
# X-Env-Layer-Config-File: my-layer.yaml
#
# Dependencies (comma-separated layer names)
# X-Env-Layer-Requires:
# X-Env-Layer-Optional-Requires:
# X-Env-Layer-Conflicts:
#
# Environment Variables
# X-Env-VarPrefix: my
#
# Variable requirements (any environment variables)
# X-Env-VarRequires: HOME,IGconf_device_user1,DOCKER_HOST
# X-Env-VarRequires-Valid: regex:^/.*,string,regex:^(unix|tcp)://.*
#
# Optional variables (validated if present, not required)
# X-Env-VarOptional: IGconf_device_user1pass,LOG_LEVEL
# X-Env-VarOptional-Valid: string,debug,info,warn,error
#
# Example variables with different validation schemes:
# X-Env-Var-hostname: localhost
# X-Env-Var-hostname-Desc: Server hostname
# X-Env-Var-hostname-Required: false
# X-Env-Var-hostname-Valid: regex:^[a-zA-Z0-9.-]+$
# X-Env-Var-hostname-Set: true
#
# X-Env-Var-port: 8080
# X-Env-Var-port-Desc: Port number (integer range)
# X-Env-Var-port-Required: false
# X-Env-Var-port-Valid: int:1024-65535
# X-Env-Var-port-Set: true
#
# X-Env-Var-environment: development
# X-Env-Var-environment-Desc: Deployment environment (enum)
# X-Env-Var-environment-Required: false
# X-Env-Var-environment-Valid: development,staging,production
# X-Env-Var-environment-Set: true
#
# X-Env-Var-debug: false
# X-Env-Var-debug-Desc: Enable debug mode (boolean)
# X-Env-Var-debug-Required: false
# X-Env-Var-debug-Valid: bool
# X-Env-Var-debug-Set: true
#
# X-Env-Var-name: myapp
# X-Env-Var-name-Desc: Application name (required non-empty string)
# X-Env-Var-name-Required: false
# X-Env-Var-name-Valid: string
# X-Env-Var-name-Set: true
#
# Validation schemes: run 'ig meta help-validation' for details
# METAEND"""

    print(boilerplate)

def _show_validation_help():
    """Show detailed help about validation schemes"""
    help_text = """Validation Schemes for X-Env-Var-*-Valid Fields:

BASIC TYPES:
  int                    - Must be a valid integer
  bool                   - Must be: true/false, 1/0, yes/no, y/n (case insensitive)
  string                 - Must be a non-empty string (required)
  string-or-unset        - Must be non-empty string or unset (null)

RANGES:
  int:MIN-MAX           - Integer within range (inclusive)
  Examples:
    int:1-100           - Integer from 1 to 100
    int:1024-65535      - Port numbers
    int:0-255           - Byte values

PATTERNS:
  regex:PATTERN         - Must match regular expression
  Examples:
    regex:^[a-zA-Z0-9.-]+$     - Hostname format
    regex:^[0-9]{3}-[0-9]{2}$  - Format like 123-45
    regex:^(http|https)://     - URLs starting with http/https

ENUMERATIONS:
  value1,value2,value3  - Must be one of the listed values
  Examples:
    development,staging,production    - Environment names
    small,medium,large               - Size options
    debug,info,warn,error            - Log levels

EXAMPLES:
  X-Env-Var-port-Valid: int:1024-65535
  X-Env-Var-env-Valid: development,staging,production
  X-Env-Var-hostname-Valid: regex:^[a-zA-Z0-9.-]+$
  X-Env-Var-debug-Valid: bool
  X-Env-Var-count-Valid: int:1-1000

VARIABLE REQUIREMENTS:
  X-Env-VarRequires: var1,var2,var3         - Comma-separated environment variables (required)
  X-Env-VarRequires-Valid: rule1,rule2,rule3 - Validation rules (same order)

  X-Env-VarOptional: var1,var2,var3         - Comma-separated environment variables (optional)
  X-Env-VarOptional-Valid: rule1,rule2,rule3 - Validation rules (same order)

  Variables are checked as-is (no IGconf_ prefix or VarPrefix applied).
  Can be used for system variables, IGconf variables from other layers, or any environment variables.

  Required variables must be set or validation fails.
  Optional variables are validated if present but don't cause failure if missing.

  Examples:
    X-Env-VarRequires: HOME,IGconf_device_user1,DOCKER_HOST
    X-Env-VarRequires-Valid: regex:^/.*,string,regex:^(unix|tcp)://.*

    X-Env-VarOptional: IGconf_device_user1pass,LOG_LEVEL
    X-Env-VarOptional-Valid: string,debug,info,warn,error

NOTE: Variables without validation rules accept any value."""

    print(help_text)

def Metadata_register_parser(subparsers):
    parser = subparsers.add_parser("meta", help="Metadata tools (parse, validate, describe, set, gen)")
    parser.add_argument("--parse", metavar="PATH", help="Parse metadata from file and output environment variables")
    parser.add_argument("--validate", metavar="PATH", help="Validate metadata and environment variables")
    parser.add_argument("--describe", metavar="PATH", help="Describe layer and variable information")
    parser.add_argument("--describe-var", nargs=2, metavar=("PATH", "VARNAME"), help="Describe specific variable information")
    parser.add_argument("--set", metavar="PATH", help="Set environment variables from metadata")
    parser.add_argument("--gen", action="store_true", help="Generate boilerplate metadata template")
    parser.add_argument("--help-validation", action="store_true", help="Show validation help")
    parser.add_argument("--write-out", metavar="FILE", help="Write key=value pairs to file (works with --parse)")
    parser.set_defaults(func=_main)


def _main(args):
    if args.gen:
        _generate_boilerplate()
        return

    if args.help_validation:
        _show_validation_help()
        return

    # Determine which command is being run and get the path
    path = None
    command = None
    varname = None

    if args.parse:
        command = "parse"
        path = args.parse
    elif args.validate:
        command = "validate"
        path = args.validate
    elif args.describe:
        command = "describe"
        path = args.describe
    elif args.describe_var:
        command = "describe"
        path = args.describe_var[0]
        varname = args.describe_var[1]
    elif args.set:
        command = "set"
        path = args.set
    else:
        print("Error: Please specify a command (--parse, --validate, --describe, --set, --gen, or --help-validation)")
        exit(1)

    try:
        meta = Metadata(path)
    except Exception as e:
        print(f"Error loading metadata: {e}")
        exit(1)

    if command == "parse":
        try:
            # First, automatically set variables with Set: true if not already set
            results = meta.set_env_vars()

            # Show SET/SKIP status for each variable
            for var, result in results.items():
                if result["status"] == "set":
                    print(f"[SET] {var}={result['value']}")
                elif result["status"] == "already_set":
                    print(f"[SKIP] {var} (already set)")
                elif result["status"] == "no_set_policy":
                    print(f"[SKIP] {var} (Set: false)")

            # Then validate that required variables are set
            validation_results = meta.validate_env_vars()
            has_validation_errors = False

            for var, result in validation_results.items():
                if result["status"] in ["missing_required", "missing_required_var"]:
                    if result["status"] == "missing_required":
                        print(f"Error: Required variable {var} is not set")
                    else:
                        print(f"Error: Required variable {result['required_var']} is not set")
                    has_validation_errors = True
                elif result["status"] == "validated" and not result["valid"]:
                    print(f"Error: Variable {var} has invalid value: {result['value']}")
                    has_validation_errors = True
                elif result["status"] == "required_validated" and not result["valid"]:
                    print(f"Error: Required variable {result['required_var']} has invalid value: {result['value']}")
                    has_validation_errors = True
                elif result["status"] == "unsupported_field":
                    print(f"Error: {result['message']}")
                    has_validation_errors = True
                elif result["status"] == "missing_var_prefix":
                    print(f"Error: {result['message']}")
                    has_validation_errors = True

            if has_validation_errors:
                exit(1)

            # Write key=value pairs to file if write_out is specified
            if hasattr(args, 'write_out') and args.write_out:
                try:
                    all_vars = meta.get_all_env_vars()
                    with open(args.write_out, 'a') as f:
                        for fullvar, default_value in all_vars.items():
                            env_value = os.environ.get(fullvar, default_value)
                            f.write(f"{fullvar}={env_value}\n")
                    print(f"Environment variables written to: {args.write_out}")
                except Exception as e:
                    print(f"Error writing to file {args.write_out}: {e}")
                    exit(1)
            else:
                # If no write_out specified, output to stdout (backward compatibility)
                print()
                all_vars = meta.get_all_env_vars()
                for fullvar, default_value in all_vars.items():
                    env_value = os.environ.get(fullvar, default_value)
                    print(f"{fullvar}={env_value}")
        except ValueError as e:
            print(f"Error: {e}")
            exit(1)
    elif command == "validate":
        results = meta.validate_env_vars()
        has_errors = False
        unsupported_count = 0
        for var, result in results.items():
            if result["status"] == "unsupported_field":
                print(f"[ERROR] {result['message']}")
                has_errors = True
                unsupported_count += 1
            elif result["status"] == "missing_required":
                print(f"[FAIL] {var} - REQUIRED but not set")
                has_errors = True
            elif result["status"] == "missing_required_var":
                print(f"[FAIL] {result['required_var']} - REQUIRED but not set")
                has_errors = True
            elif result["status"] == "validated":
                status = "OK" if result["valid"] else "FAIL"
                print(f"[{status}] {var}={result['value']} (rule: {result['rule']})")
                if not result["valid"]:
                    has_errors = True
            elif result["status"] == "required_validated":
                status = "OK" if result["valid"] else "FAIL"
                print(f"[{status}] {result['required_var']}={result['value']} (required, rule: {result['rule']})")
                if not result["valid"]:
                    has_errors = True
            elif result["status"] == "no_validation":
                print(f"[SKIP] {var}={result['value']} (no validation rule)")
            elif result["status"] == "required_no_validation":
                print(f"[SKIP] {result['required_var']}={result['value']} (required, no validation rule)")
            elif result["status"] == "optional_var_unset":
                print(f"[INFO] {result['optional_var']} - optional, not set")
            elif result["status"] == "optional_validated":
                status = "OK" if result["valid"] else "WARN"
                print(f"[{status}] {result['optional_var']}={result['value']} (optional, rule: {result['rule']})")
                # Note: Optional variable validation failure doesn't cause overall failure
            elif result["status"] == "optional_no_validation":
                print(f"[SKIP] {result['optional_var']}={result['value']} (optional, no validation rule)")
            elif result["status"] == "optional_unset":
                print(f"[INFO] {var} - optional, not set")
            elif result["status"] == "missing_var_prefix":
                print(f"[ERROR] {result['message']}")
                has_errors = True

        # If there were unsupported fields, show all supported fields
        if unsupported_count > 0:
            print()
            print("Supported fields:")
            supported_fields = get_supported_fields_list()
            for field in supported_fields:
                print(f"  {field}")

        if has_errors:
            exit(1)
    elif command == "set":
        try:
            results = meta.set_env_vars()
            for var, result in results.items():
                if result["status"] == "set":
                    print(f"[SET] {var}={result['value']}")
                elif result["status"] == "already_set":
                    print(f"[SKIP] {var}={result['value']} (already set)")
                elif result["status"] == "no_set_policy":
                    print(f"[SKIP] {var} (Set: false)")
        except ValueError as e:
            print(f"Error: {e}")
            exit(1)
    elif command == "describe":
        try:
            if not varname:
                # Show all information when no specific variable requested
                has_content = False

                # Check and display layer information
                layer_info = meta.get_unified_layer_info()
                if layer_info:
                    print("Layer Information:")
                    print(f"  Name: {layer_info['name']}")
                    print(f"  Category: {layer_info['category']}")
                    print(f"  Version: {layer_info['version']}")

                    if layer_info['description']:
                        print(f"  Description: {layer_info['description']}")

                    deps = ', '.join(layer_info['depends']) if layer_info['depends'] else 'none'
                    print(f"  Required Dependencies: {deps}")

                    opt_deps = ', '.join(layer_info['optional_depends']) if layer_info['optional_depends'] else 'none'
                    print(f"  Optional Dependencies: {opt_deps}")

                    conflicts = ', '.join(layer_info['conflicts']) if layer_info['conflicts'] else 'none'
                    print(f"  Conflicts: {conflicts}")

                    print(f"  Config File: {layer_info['config_file']}")

                    # Show required environment variables if any
                    required_vars = meta._metadata.get("X-Env-VarRequires", "")
                    if required_vars.strip():
                        req_vars = ', '.join([v.strip() for v in required_vars.split(',') if v.strip()])
                        print(f"  Required Variables: {req_vars}")

                    # Show optional environment variables if any
                    optional_vars = meta._metadata.get("X-Env-VarOptional", "")
                    if optional_vars.strip():
                        opt_vars = ', '.join([v.strip() for v in optional_vars.split(',') if v.strip()])
                        print(f"  Optional Variables: {opt_vars}")

                    print()
                    has_content = True

                # Check and display environment variables
                env_vars = meta.get_all_env_vars()
                if env_vars:
                    print("Environment Variables:")
                    print()

                    # Get prefix for better display
                    prefix = meta._metadata.get("X-Env-VarPrefix", "").lower()

                    for full_var_name, value in env_vars.items():
                        # Extract variable name from full env var name (remove IGconf_prefix_)
                        if full_var_name.startswith(f"IGconf_{prefix}_"):
                            var_name = full_var_name[len(f"IGconf_{prefix}_"):]
                        else:
                            var_name = full_var_name

                        info = meta.get_variable_description(var_name)
                        print(f"  Variable: {var_name.upper()}")
                        print(f"    Full Name: {full_var_name}")
                        print(f"    Default Value: {value}")
                        if info['description']:
                            print(f"    Description: {info['description']}")
                        if info['valid']:
                            print(f"    Validation: {info['valid']}")
                        print()
                    has_content = True

                if not has_content:
                    print("No layer information or environment variables defined in metadata")
            else:
                # Show specific variable when specified
                info = meta.get_variable_description(varname)
                print(f"Variable: {info['name']}")
                print(f"Value: {info['value']}")
                print(f"Description: {info['description']}")
                print(f"Valid Rule: {info['valid']}")
        except ValueError as e:
            print(f"Error: {e}")
            exit(1)

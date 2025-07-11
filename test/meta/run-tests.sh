#!/bin/bash

# rpi-image-gen metadata parsing test suite
# Usage: just run it

IGTOP=$(readlink -f "$(dirname "$0")/../../")
META="${IGTOP}/test/meta"

PATH="$IGTOP/bin:$PATH"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

TOTAL_TESTS=0
PASSED_TESTS=0
FAILED_TESTS=0

# Result tracking
declare -a FAILED_TEST_NAMES=()

print_header() {
    echo -e "${BLUE}================================${NC}"
    echo -e "${BLUE}$1${NC}"
    echo -e "${BLUE}================================${NC}"
}

print_test() {
    echo -e "${YELLOW}Testing: $1${NC}"
}

print_pass() {
    echo -e "${GREEN}✓ PASS: $1${NC}"
    ((PASSED_TESTS++))
}

print_fail() {
    echo -e "${RED}✗ FAIL: $1${NC}"
    echo -e "${RED}  Error: $2${NC}"
    ((FAILED_TESTS++))
    FAILED_TEST_NAMES+=("$1")
}

run_test() {
    local test_name="$1"
    local command="$2"
    local expected_exit_code="$3"
    local description="$4"

    ((TOTAL_TESTS++))
    print_test "$test_name"

    # Run the command and capture both stdout and stderr
    local output
    output=$(eval "$command" 2>&1)
    local actual_exit_code=$?

    if [ "$actual_exit_code" -eq "$expected_exit_code" ]; then
        print_pass "$description"
    else
        print_fail "$description" "Expected exit code $expected_exit_code, got $actual_exit_code. Output: $output"
    fi

    echo ""
}

print_summary() {
    echo -e "${BLUE}================================${NC}"
    echo -e "${BLUE}TEST SUMMARY${NC}"
    echo -e "${BLUE}================================${NC}"
    echo -e "Total tests: $TOTAL_TESTS"
    echo -e "${GREEN}Passed: $PASSED_TESTS${NC}"
    echo -e "${RED}Failed: $FAILED_TESTS${NC}"

    if [ ${#FAILED_TEST_NAMES[@]} -gt 0 ]; then
        echo -e "\n${RED}Failed tests:${NC}"
        for test in "${FAILED_TEST_NAMES[@]}"; do
            echo -e "${RED}  - $test${NC}"
        done
    fi

    if [ $FAILED_TESTS -eq 0 ]; then
        echo -e "\n${GREEN}All tests passed!${NC}"
        exit 0
    else
        echo -e "\n${RED}Some tests failed. Please check the output above.${NC}"
        exit 1
    fi
}

cleanup_env() {
    unset $(env | grep '^IGconf_' | cut -d= -f1)
    unset NONEXISTENT_VAR
}

setup_test_env() {
    export IGconf_basic_hostname="test-host"
    export IGconf_types_name="test-app"
    export IGconf_types_timeout="60"
    export IGconf_types_debug="true"
    export IGconf_types_environment="development"
    export IGconf_types_email="test@example.com"
    export IGconf_valfail_port="99999"
    export IGconf_valfail_email="not-an-email"
    export IGconf_valfail_required=""
}


# Valid basic metadata
print_header "VALID METADATA TESTS"

run_test "valid-basic-parse" \
    "ig meta --parse ${META}/valid-basic.yaml" \
    0 \
    "Valid basic metadata should parse successfully"

run_test "valid-basic-validate" \
    "ig meta --validate ${META}/valid-basic.yaml" \
    0 \
    "Valid basic metadata should validate successfully"

run_test "valid-basic-describe" \
    "ig meta --describe ${META}/valid-basic.yaml" \
    0 \
    "Valid basic metadata should describe successfully"


# Valid all-types metadata
setup_test_env
run_test "valid-all-types-parse" \
    "ig meta --parse ${META}/valid-all-types.yaml" \
    0 \
    "Valid all-types metadata should parse successfully"

run_test "valid-all-types-validate" \
    "ig meta --validate ${META}/valid-all-types.yaml" \
    0 \
    "Valid all-types metadata should validate successfully"

run_test "valid-all-types-set" \
    "ig meta --set ${META}/valid-all-types.yaml" \
    0 \
    "Valid all-types metadata should set variables successfully"


# Valid requirements-only metadata
run_test "valid-requirements-only-parse" \
    "ig meta --parse ${META}/valid-requirements-only.yaml" \
    0 \
    "Valid requirements-only metadata should parse successfully (no output expected)"

run_test "valid-requirements-only-validate" \
    "ig meta --validate ${META}/valid-requirements-only.yaml" \
    0 \
    "Valid requirements-only metadata should validate successfully"


# Set policies
cleanup_env
run_test "set-policies-set" \
    "ig meta --set ${META}/set-policies.yaml" \
    0 \
    "Set policies should work correctly"


print_header "INVALID METADATA TESTS"


# Invalid - no prefix
cleanup_env
run_test "invalid-no-prefix-parse" \
    "ig meta --parse ${META}/invalid-no-prefix.yaml" \
    1 \
    "Metadata with variables but no prefix should fail to parse"

run_test "invalid-no-prefix-validate" \
    "ig meta --validate ${META}/invalid-no-prefix.yaml" \
    1 \
    "Metadata with variables but no prefix should fail to validate"


# Invalid - malformed syntax
run_test "invalid-malformed-parse" \
    "ig meta --parse ${META}/invalid-malformed.yaml" \
    1 \
    "Malformed metadata should fail to parse"

run_test "invalid-malformed-validate" \
    "ig meta --validate ${META}/invalid-malformed.yaml" \
    1 \
    "Malformed metadata should fail to validate"


# Invalid - unsupported fields
run_test "invalid-unsupported-parse" \
    "ig meta --parse ${META}/invalid-unsupported-fields.yaml" \
    1 \
    "Metadata with unsupported fields should fail to parse"

run_test "invalid-unsupported-validate" \
    "ig meta --validate ${META}/invalid-unsupported-fields.yaml" \
    1 \
    "Metadata with unsupported fields should fail to validate"


# Invalid - YAML syntax
run_test "invalid-yaml-syntax-layer-validate" \
    "ig layer --validate ${META}/invalid-yaml-syntax.yaml" \
    1 \
    "Invalid YAML syntax should fail layer validation"


# Validation failures
cleanup_env
setup_test_env
run_test "validation-failures-parse" \
    "ig meta --parse ${META}/validation-failures.yaml" \
    1 \
    "Metadata with validation failures should fail to parse"

run_test "validation-failures-validate" \
    "ig meta --validate ${META}/validation-failures.yaml" \
    1 \
    "Metadata with validation failures should fail to validate"


print_header "LAYER FUNCTIONALITY TESTS"


# Layer with dependencies
setup_test_env
run_test "layer-with-deps-info" \
    "ig layer --paths ${META} --info test-with-deps" \
    0 \
    "Layer with dependencies should show info successfully"

run_test "layer-with-deps-validate" \
    "ig layer --paths ${META} --validate test-with-deps" \
    0 \
    "Layer with dependencies should validate successfully"


# Layer with missing dependencies
run_test "layer-missing-dep-validate" \
    "ig layer --paths ${META} --validate test-missing-dep" \
    1 \
    "Layer with missing dependencies should fail validation"

run_test "layer-missing-dep-check" \
    "ig layer --paths ${META} --check test-missing-dep" \
    1 \
    "Layer dependency check should fail for missing dependencies"


# Circular dependencies
run_test "layer-circular-deps-check" \
    "ig layer --paths ${META} --check test-circular-a" \
    1 \
    "Circular dependency check should fail"

run_test "layer-build-order-circular" \
    "ig layer --paths ${META} --build-order test-circular-a" \
    1 \
    "Build order should fail for circular dependencies"


# Duplicate layer names - this test expects the second test-basic to be skipped
run_test "layer-duplicate-name-handling" \
    "ig layer --paths ${META} --info test-basic" \
    0 \
    "Duplicate layer name should use first-found-wins policy"


print_header "OTHER TESTS"


# Help commands
run_test "meta-help-validation" \
    "ig meta --help-validation" \
    0 \
    "Help validation should work"

run_test "meta-gen" \
    "ig meta --gen" \
    0 \
    "Metadata generation should work"


# Layer build order (valid case)
run_test "layer-build-order-valid" \
    "ig layer --paths ${META} --build-order test-with-deps" \
    0 \
    "Build order should work for valid dependencies"


# Layer management discovery
run_test "layer-discovery" \
    "ig layer --paths ${META} --info test-basic" \
    0 \
    "Layer discovery should find test layers"


print_header "AUTO-SET AND APPLY-ENV TESTS"


# Metadata parse with auto-set from policy
cleanup_env
unset IGconf_net_interface
# Temporarily change Set policy to y for this test
sed -i 's/X-Env-Var-INTERFACE-Set: n/X-Env-Var-INTERFACE-Set: y/' ${META}/network-x-env.yaml
run_test "meta-parse-auto-set" \
    "ig meta --parse ${META}/network-x-env.yaml" \
    0 \
    "Meta parse should auto-set variables with Set: y policy"
# Restore original setting
sed -i 's/X-Env-Var-INTERFACE-Set: y/X-Env-Var-INTERFACE-Set: n/' ${META}/network-x-env.yaml


# Layer apply-env with valid metadata
cleanup_env
run_test "layer-apply-env-valid" \
    "ig layer --paths ${META} --apply-env test-set-policies" \
    0 \
    "Layer apply-env should work with valid metadata"


# Layer apply-env with invalid metadata
run_test "layer-apply-env-invalid" \
    "ig layer --paths ${META} --apply-env test-unsupported" \
    1 \
    "Layer apply-env should fail with invalid metadata"


# Verify meta parse auto-sets required variables with Set: y
cleanup_env
unset IGconf_net_interface
IGconf_net_interface_before=$(env | grep IGconf_net_interface || echo "UNSET")
# Temporarily change Set policy to y for this test
sed -i 's/X-Env-Var-INTERFACE-Set: n/X-Env-Var-INTERFACE-Set: y/' ${META}/network-x-env.yaml
run_test "meta-parse-sets-required-vars" \
    "test \"\$IGconf_net_interface_before\" = \"UNSET\" && ig meta --parse ${META}/network-x-env.yaml | grep 'IGconf_net_interface=eth0'" \
    0 \
    "Meta parse should set required variables from defaults when Set: y"
# Restore original setting
sed -i 's/X-Env-Var-INTERFACE-Set: y/X-Env-Var-INTERFACE-Set: n/' ${META}/network-x-env.yaml



# Test layer apply-env sets variables instead of skipping them
cleanup_env
unset IGconf_net_interface IGconf_net_ip IGconf_net_dns
# Temporarily change Set policy to y for this test
sed -i 's/X-Env-Var-INTERFACE-Set: n/X-Env-Var-INTERFACE-Set: y/' ${META}/network-x-env.yaml
run_test "layer-apply-env-sets-vars" \
    "ig layer --paths ${META} --apply-env network-setup | grep -E '\\[SET\\].*IGconf_net_interface=eth0'" \
    0 \
    "Layer apply-env should SET variables, not skip them when they are unset"
# Restore original setting
sed -i 's/X-Env-Var-INTERFACE-Set: y/X-Env-Var-INTERFACE-Set: n/' ${META}/network-x-env.yaml


# Test metadata parse with required+auto-set variables works
cleanup_env
unset IGconf_net_interface IGconf_net_ip IGconf_net_dns
# Temporarily change Set policy to y for this test
sed -i 's/X-Env-Var-INTERFACE-Set: n/X-Env-Var-INTERFACE-Set: y/' ${META}/network-x-env.yaml
run_test "meta-parse-required-auto-set-regression" \
    "ig meta --parse ${META}/network-x-env.yaml | grep 'IGconf_net_interface=eth0'" \
    0 \
    "Meta parse should work with required variables that have Set: y (regression test)"
# Restore original setting
sed -i 's/X-Env-Var-INTERFACE-Set: y/X-Env-Var-INTERFACE-Set: n/' ${META}/network-x-env.yaml


# Test both meta parse and layer apply-env work
cleanup_env
unset IGconf_net_interface IGconf_net_ip IGconf_net_dns
# Temporarily change Set policy to y for this test
sed -i 's/X-Env-Var-INTERFACE-Set: n/X-Env-Var-INTERFACE-Set: y/' ${META}/network-x-env.yaml
run_test "meta-parse-layer-apply-env-consistency" \
    "ig meta --parse ${META}/network-x-env.yaml >/dev/null && ig layer --paths ${META} --apply-env network-setup | grep -E '\\[SET\\].*IGconf_net_interface=eth0'" \
    0 \
    "Both meta parse and layer apply-env should work consistently with required+auto-set variables"
# Restore original setting
sed -i 's/X-Env-Var-INTERFACE-Set: y/X-Env-Var-INTERFACE-Set: n/' ${META}/network-x-env.yaml


# Test layer apply-env fails when required variable has Set: n and is not provided
cleanup_env
unset IGconf_net_interface IGconf_net_ip IGconf_net_dns
run_test "layer-apply-env-fails-required-no-set" \
    "ig layer --paths ${META} --apply-env network-setup" \
    1 \
    "Layer apply-env should fail when required variables have Set: n and are not provided in environment"


# Test layer apply-env succeeds when required variable has Set: n but is provided
cleanup_env
unset IGconf_net_interface IGconf_net_ip IGconf_net_dns
export IGconf_net_interface=wlan0
run_test "layer-apply-env-succeeds-required-manually-set" \
    "ig layer --paths ${META} --apply-env network-setup | grep -E '\\[SKIP\\].*IGconf_net_interface.*already set'" \
    0 \
    "Layer apply-env should succeed when required variables have Set: n but are manually provided"


# Test metadata parse fails when required variable has Set: n and is not provided
cleanup_env
unset IGconf_net_interface IGconf_net_ip IGconf_net_dns
run_test "meta-parse-fails-required-no-set" \
    "ig meta --parse ${META}/network-x-env.yaml" \
    1 \
    "Meta parse should fail when required variables have Set: n and are not provided in environment"


# Test metadata parse succeeds when required variable has Set: n but is manually provided
cleanup_env
unset IGconf_net_interface IGconf_net_ip IGconf_net_dns
export IGconf_net_interface=wlan0
run_test "meta-parse-succeeds-required-manually-set" \
    "ig meta --parse ${META}/network-x-env.yaml | grep 'IGconf_net_interface=wlan0'" \
    0 \
    "Meta parse should succeed when required variables have Set: n but are manually provided"


cleanup_env
print_summary

"""Pre-deployment validation script.

Quick checks to catch obvious issues before deploying to ECS.
Run: python validate_setup.py
"""

import os
import sys
from pathlib import Path


def check_lambda_handlers():
    """Verify Lambda handlers exist and are valid Python."""
    infra_dir = Path(__file__).parent.parent

    handlers = [
        ("api_ingester", infra_dir / "lambda" / "api_ingester" / "handler.py"),
        ("mock_api", infra_dir / "lambda" / "mock_api" / "handler.py"),
    ]

    for name, handler_path in handlers:
        if not handler_path.exists():
            return False, f"Lambda handler not found: {handler_path}"

        try:
            with open(handler_path) as f:
                code = f.read()
                compile(code, str(handler_path), "exec")
        except SyntaxError as e:
            return False, f"Lambda handler {name} syntax error: {e}"

    return True, "All Lambda handlers are valid"


def check_cdk_stack():
    """Verify CDK stack file exists."""
    infra_dir = Path(__file__).parent.parent
    stack_path = infra_dir / "cdk" / "stacks" / "ecs_airflow_stack.py"
    if not stack_path.exists():
        return False, f"CDK stack not found: {stack_path}"
    return True, "CDK stack exists"


def check_requirements():
    """Verify requirements.txt exists."""
    infra_dir = Path(__file__).parent.parent
    req_path = infra_dir / "requirements" / "requirements.txt"
    if not req_path.exists():
        return False, f"requirements.txt not found: {req_path}"
    return True, "requirements.txt exists"


def main():
    """Run all validation checks."""
    checks = [
        ("Lambda Handlers", check_lambda_handlers),
        ("CDK Stack", check_cdk_stack),
        ("Requirements", check_requirements),
    ]

    print("=" * 60)
    print("Pre-Deployment Validation")
    print("=" * 60)
    print()

    all_passed = True
    for name, check_func in checks:
        passed, message = check_func()
        status = "✓" if passed else "✗"
        print(f"{status} {name}: {message}")
        if not passed:
            all_passed = False

    print()
    if all_passed:
        print("✓ All checks passed! Ready to deploy.")
        return 0
    else:
        print("✗ Some checks failed. Fix issues before deploying.")
        return 1


if __name__ == "__main__":
    sys.exit(main())

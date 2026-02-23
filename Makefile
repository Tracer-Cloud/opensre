-include .env
export

.PHONY: install test test-full demo clean lint format deploy deploy-lambda deploy-prefect deploy-flink destroy destroy-lambda destroy-prefect destroy-flink prefect-local-test test-k8s-local test-k8s test-k8s-datadog deploy-dd-monitors cleanup-dd-monitors deploy-eks destroy-eks test-k8s-eks

PYTHON = python3
PIP = python3 -m pip
PIP_INSTALL_FLAGS = --user --break-system-packages
USER_BASE := $(shell $(PYTHON) -m site --user-base)
USER_BIN := $(USER_BASE)/bin
export PATH := $(USER_BIN):$(PATH)

# Create venv and install dependencies
install:
	$(PIP) install $(PIP_INSTALL_FLAGS) -r requirements.txt
	$(PIP) install $(PIP_INSTALL_FLAGS) -e .

# Run Prefect ECS demo (default demo) - shows Investigation Trace in RCA
demo:
	$(PYTHON) -m tests.test_case_upstream_prefect_ecs_fargate.test_agent_e2e

# Run Superfluid test case demo
superfluid-demo:
	$(PYTHON) -m tests.test_case_superfluid.test_orchestrator

# Run CloudWatch demo
cloudwatch-demo:
	$(PYTHON) -m tests.test_case_cloudwatch_demo.test_orchestrator

# Run Prefect ECS Fargate E2E test (alias for demo)
prefect-demo:
	$(PYTHON) -m tests.test_case_upstream_prefect_ecs_fargate.test_agent_e2e

# Run Kubernetes local test (kind)
test-k8s-local:
	$(PYTHON) -m tests.test_case_kubernetes.test_local --both

# Run Kubernetes test (matches CI)
test-k8s:
	$(PYTHON) -m tests.test_case_kubernetes.test_local

# Run Kubernetes + Datadog test (kind + DD Agent)
test-k8s-datadog:
	$(PYTHON) -m tests.test_case_kubernetes.test_datadog

# Deploy Datadog monitors (requires DD_API_KEY + DD_APP_KEY)
deploy-dd-monitors:
	$(PYTHON) -c "from tests.test_case_kubernetes.test_datadog import deploy_monitors; deploy_monitors()"

# Remove Datadog monitors created by tracer tests
cleanup-dd-monitors:
	$(PYTHON) -c "from tests.test_case_kubernetes.test_datadog import cleanup_monitors; cleanup_monitors()"

# Deploy EKS cluster + ECR image for Kubernetes tests
deploy-eks:
	$(PYTHON) -c "from tests.test_case_kubernetes.infrastructure_sdk.eks import deploy_eks_stack; deploy_eks_stack()"

# Destroy EKS cluster and all associated resources
destroy-eks:
	$(PYTHON) -c "from tests.test_case_kubernetes.infrastructure_sdk.eks import destroy_eks_stack; destroy_eks_stack()"

# Run Kubernetes + Datadog test on EKS
test-k8s-eks:
	$(PYTHON) -m tests.test_case_kubernetes.test_eks

# Fast: trigger a K8s alert in ~15s (fire-and-forget)
trigger-alert:
	$(PYTHON) -m tests.test_case_kubernetes.trigger_alert --configure-kubectl

# Fast trigger + wait for Slack confirmation
trigger-alert-verify:
	$(PYTHON) -m tests.test_case_kubernetes.trigger_alert --configure-kubectl --wait-slack

# Run Prefect ECS local test
prefect-local-test:
	$(PYTHON) -m tests.test_case_upstream_prefect_ecs_fargate.test_local $(if $(CLOUD),--cloud,)

# Run upstream/downstream pipeline E2E test
upstream-downstream:
	$(PYTHON) -m tests.test_case_upstream_lambda.test_agent_e2e

# Run Apache Flink ECS E2E test
flink-demo:
	$(PYTHON) -m tests.test_case_upstream_apache_flink_ecs.test_agent_e2e

grafana-demo:
	$(PYTHON) -m tests.test_case_grafana.grafana_pipeline

# Run the generic CLI (reads from stdin or --input)
run:
	$(PYTHON) -m app.main

dev: 
	langgraph dev


# Deploy all test case infrastructure in parallel (SDK - fast!)
deploy:
	@echo "Deploying all stacks in parallel..."
	@$(PYTHON) -m tests.test_case_upstream_lambda.infrastructure_sdk.deploy & \
	$(PYTHON) -m tests.test_case_upstream_prefect_ecs_fargate.infrastructure_sdk.deploy & \
	$(PYTHON) -m tests.test_case_upstream_apache_flink_ecs.infrastructure_sdk.deploy & \
	wait
	@echo "All stacks deployed."

# Deploy Lambda test case
deploy-lambda:
	@echo "Deploying Lambda stack..."
	$(PYTHON) -m tests.test_case_upstream_lambda.infrastructure_sdk.deploy

# Deploy Prefect ECS test case
deploy-prefect:
	@echo "Deploying Prefect ECS stack..."
	$(PYTHON) -m tests.test_case_upstream_prefect_ecs_fargate.infrastructure_sdk.deploy

# Deploy Flink ECS test case
deploy-flink:
	@echo "Deploying Flink ECS stack..."
	$(PYTHON) -m tests.test_case_upstream_apache_flink_ecs.infrastructure_sdk.deploy

# Destroy all test case infrastructure in parallel
destroy:
	@echo "Destroying all stacks in parallel..."
	@$(PYTHON) -m tests.test_case_upstream_lambda.infrastructure_sdk.destroy & \
	$(PYTHON) -m tests.test_case_upstream_prefect_ecs_fargate.infrastructure_sdk.destroy & \
	$(PYTHON) -m tests.test_case_upstream_apache_flink_ecs.infrastructure_sdk.destroy & \
	wait
	@echo "All stacks destroyed."

# Destroy Lambda test case
destroy-lambda:
	@echo "Destroying Lambda stack..."
	$(PYTHON) -m tests.test_case_upstream_lambda.infrastructure_sdk.destroy

# Destroy Prefect ECS test case
destroy-prefect:
	@echo "Destroying Prefect ECS stack..."
	$(PYTHON) -m tests.test_case_upstream_prefect_ecs_fargate.infrastructure_sdk.destroy

# Destroy Flink ECS test case
destroy-flink:
	@echo "Destroying Flink ECS stack..."
	$(PYTHON) -m tests.test_case_upstream_apache_flink_ecs.infrastructure_sdk.destroy

# Run fast tests + Prefect cloud E2E
test:
	$(PYTHON) -m pytest -v app tests/utils
	$(PYTHON) -m tests.test_case_upstream_prefect_ecs_fargate.test_agent_e2e

# Run full test suite (CI/CD)
test-full:
	$(PYTHON) -m pytest -v

# Run tests with coverage
test-cov:
	$(PYTHON) -m pytest -v --cov=app --cov-report=term-missing

# Run Grafana integration tests
test-grafana:
	@echo "Running Grafana agent integration tests..."
	$(PYTHON) -m pytest app/agent/tools/tool_actions/grafana/grafana_actions_test.py tests/test_case_grafana_validation/test_grafana_cloud_queries.py -v
	@echo ""
	@echo "Running Grafana live action checks..."
	$(PYTHON) -m app.agent.tools.tool_actions.grafana.test_agent_grafana_actions

# Clean up
clean:
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete 2>/dev/null || true
	find . -type d -name ".pytest_cache" -exec rm -rf {} + 2>/dev/null || true
	rm -rf .coverage htmlcov/ 2>/dev/null || true

# Lint code
lint:
	ruff check app/ tests/

# Format code
format:
	ruff format app/ tests/

# Type check
typecheck:
	mypy app/

# Run all checks
check: lint typecheck test-full

# Show help
help:
	@echo "Available commands:"
	@echo ""
	@echo "  DEPLOYMENT (AWS SDK - fast!)"
	@echo "  make deploy          - Deploy all test case infrastructure"
	@echo "  make deploy-lambda   - Deploy Lambda stack (~50s)"
	@echo "  make deploy-prefect  - Deploy Prefect ECS stack (~55s)"
	@echo "  make deploy-flink    - Deploy Flink ECS stack (~90s)"
	@echo "  make destroy         - Destroy all test case infrastructure"
	@echo "  make destroy-lambda  - Destroy Lambda stack"
	@echo "  make destroy-prefect - Destroy Prefect ECS stack"
	@echo "  make destroy-flink   - Destroy Flink ECS stack"
	@echo ""
	@echo "  DEMOS"
	@echo "  make demo            - Run Prefect ECS E2E test (default, shows Investigation Trace)"
	@echo "  make prefect-demo    - Run Prefect ECS Fargate E2E test (alias for demo)"
	@echo "  make prefect-local-test - Run Prefect ECS local test (CLOUD=1 for ECS)"
	@echo "  make flink-demo      - Run Apache Flink ECS E2E test"
	@echo "  make superfluid-demo - Run Superfluid test case demo"
	@echo "  make cloudwatch-demo - Run CloudWatch demo"
	@echo "  make upstream-downstream - Run upstream/downstream Lambda E2E test"
	@echo ""
	@echo "  KUBERNETES"
	@echo "  make test-k8s-local  - Run Kubernetes local test (kind)"
	@echo "  make test-k8s        - Run Kubernetes test (matches CI)"
	@echo "  make test-k8s-datadog - Run Kubernetes + Datadog test"
	@echo "  make deploy-dd-monitors - Deploy Datadog monitors (DD_API_KEY + DD_APP_KEY)"
	@echo "  make cleanup-dd-monitors - Remove Datadog test monitors"
	@echo "  make deploy-eks      - Deploy EKS cluster + ECR image"
	@echo "  make destroy-eks     - Destroy EKS cluster and resources"
	@echo "  make test-k8s-eks    - Run Kubernetes + Datadog test on EKS"
	@echo ""
	@echo "  LOCAL DEVELOPMENT"
	@echo "  make install         - Install dependencies"
	@echo ""
	@echo "  TESTING & QUALITY"
	@echo "  make test            - Run fast unit tests + Prefect cloud E2E"
	@echo "  make test-full       - Run full test suite (CI/CD)"
	@echo "  make test-cov        - Run tests with coverage"
	@echo "  make test-grafana    - Run Grafana integration tests"
	@echo "  make clean           - Clean up cache files"
	@echo "  make lint            - Lint code with ruff"
	@echo "  make format          - Format code with ruff"
	@echo "  make typecheck       - Type check with mypy"
	@echo "  make check           - Run all checks"


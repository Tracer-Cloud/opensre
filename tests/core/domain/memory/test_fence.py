"""The demo fence: product demo and sample output is fenced, real facts are not.

The fenced cases are memories onboarding demos actually left in a user's
store. The kept cases are the near misses a broad ``demo``/``sample`` keyword
match would have thrown away.
"""

from __future__ import annotations

import pytest

from core.domain.memory import is_fenced


@pytest.mark.parametrize(
    ("slug", "memory_type", "description"),
    [
        (
            "repository-davincios-opensre-ci-repair-demo-yx06",
            "repository",
            "davincios/opensre-ci-repair-demo-yx06 is a retained private local CI-repair demo "
            "with successfully repaired PR #1.",
        ),
        (
            "repository-opensre-ci-fix-demo-e9yabj",
            "repository",
            "davincios/opensre-ci-fix-demo-E9YaBJ PR #1 was automatically repaired.",
        ),
        (
            "ci-target",
            "repository",
            "octocat/opensre-onboarding-ci-repair-demo is the active repository for CI repair.",
        ),
        (
            "scheduled-ci-repair-demo-2026-09-12",
            "investigation_learning",
            "Two disposable scheduled CI repair demos pushed no repair.",
        ),
        (
            "github-organization-tracer-cloud",
            "infrastructure",
            "Disposable CI-repair demos belong under tracer-cloud.",
        ),
        ("checkout-latency", "investigation_learning", "Sample alert: checkout p99 at 2s."),
        ("db-pool-exhaustion", "investigation_learning", "Synthetic scenario for RDS pool."),
        ("benchmark-notes", "infrastructure", "Cloudopsbench run 4 failed on step 2."),
    ],
)
def test_demo_and_sample_output_is_fenced(slug: str, memory_type: str, description: str) -> None:
    assert is_fenced(slug, memory_type, description)


@pytest.mark.parametrize(
    ("slug", "memory_type", "description"),
    [
        ("apm-sampling", "infrastructure", "Datadog APM sample rate is 0.1 in prod."),
        ("datadog-synthetic-monitors", "infrastructure", "Synthetic monitors run in us-east-1."),
        ("customer-demo-app", "repository", "acme/customer-demo-app hosts the sales demo app."),
        ("repository-opensre", "repository", "Tracer-Cloud/opensre runs CI/CD on GitHub Actions."),
        # A person's own words are never fenced, even when they are about demos.
        ("reuse-ci-demo-repositories", "preference", "Reuse an existing CI demo repository."),
        ("user-profile", "user", "Runs the OpenSRE onboarding demo for customers."),
    ],
)
def test_real_facts_near_the_keywords_are_kept(
    slug: str, memory_type: str, description: str
) -> None:
    assert not is_fenced(slug, memory_type, description)

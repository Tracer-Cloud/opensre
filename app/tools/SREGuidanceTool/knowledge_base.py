"""SRE knowledge base with indexed content from Google SRE books.

Source: Google SRE Book and Workbook - Data Processing Pipelines chapters
- https://sre.google/sre-book/data-processing-pipelines/
- https://sre.google/workbook/data-processing/
"""

from dataclasses import dataclass


@dataclass
class SREKnowledgeTopic:
    """A topic from SRE literature with associated keywords and content."""

    name: str
    keywords: list[str]
    content: str
    source: str


SRE_TOPICS: dict[str, SREKnowledgeTopic] = {
    "pipeline_types": SREKnowledgeTopic(
        name="Pipeline Applications",
        keywords=["etl", "ml", "analytics", "batch", "streaming", "transform", "load"],
        content="""Pipeline Application Types (Google SRE Workbook):

1. ETL (Extract Transform Load): Data extracted from source, transformed/denormalized,
   reloaded into specialized format. Common uses:
   - Preprocessing for ML or business intelligence
   - Computing aggregations (counting events in time intervals)
   - Calculating billing reports
   - Indexing pipelines

2. Data Analytics/Business Intelligence: Aggregating data across users/devices to
   identify issues or successes. Key characteristics:
   - Monthly/daily aggregate reports
   - Cross-source data joins
   - Triggered by new data arrival

3. Machine Learning Pipelines: Multi-stage process including:
   - Feature/label extraction from larger dataset
   - Model training on extracted features
   - Model evaluation on test set
   - Model serving to other services
   - Decisions made using model responses""",
        source="SRE Workbook Ch.13 - Pipeline Applications",
    ),
    "slo_freshness": SREKnowledgeTopic(
        name="Data Freshness SLO",
        keywords=["freshness", "latency", "delay", "stale", "slo", "sli", "timeliness"],
        content="""Data Freshness SLO Patterns (Google SRE Workbook):

Most pipeline data freshness SLOs use one of these formats:
- X% of data processed in Y [seconds, days, minutes]
- The oldest data is no older than Y [seconds, days, minutes]
- Pipeline job completed successfully within Y [seconds, days, minutes]

Key Principles:
1. Measure end-to-end, not per-stage - customers care about total latency
2. Timeliness measured as delay from when bucket could theoretically close
3. Downstream jobs can't start until dependencies are delivered
4. Any delay in delivery affects downstream job timeliness

Priority Tiers: High, Normal, Low - allows prioritizing delivery during incidents.""",
        source="SRE Workbook Ch.13 - Define and Measure SLOs",
    ),
    "slo_correctness": SREKnowledgeTopic(
        name="Data Correctness SLO",
        keywords=["correctness", "accuracy", "validation", "quality", "skewness"],
        content="""Data Correctness SLO Patterns (Google SRE Workbook):

Correctness Targets:
- Use test accounts to calculate expected output ("golden data")
- Compare expected vs actual output
- Monitor for errors/discrepancies with threshold-based alerting
- Backward-looking analysis (e.g., no more than 0.1% incorrect invoices/quarter)

Skewness: Maximal percentage of data misplaced on daily basis
- Occurs when heuristics place events in wrong time buckets
- Can cause under-reporting then over-reporting for time periods

Completeness: Percentage of events delivered after successful publishing
- Compare counts of published vs delivered events
- Any mismatch requires investigation""",
        source="SRE Workbook Ch.13 - Data Correctness",
    ),
    "failure_delayed_data": SREKnowledgeTopic(
        name="Delayed Data Failure Mode",
        keywords=["delayed", "timeout", "hung", "stuck", "slow", "waiting", "blocked"],
        content="""Delayed Data Failure Mode (Google SRE Book & Workbook):

Causes of Delayed Data:
1. Input/output is delayed from upstream
2. Downstream job starts without necessary data
3. Pipeline stalls waiting for dependencies
4. Hanging chunks - work units requiring disproportionate resources

Impact:
- Stale data is almost always better than incorrect data
- If pipeline processes incomplete data, errors propagate downstream
- Data dependencies must be respected by all stages

"Hanging Chunk" Problem:
- Some chunks require uneven resources (e.g., large customer data)
- End-to-end runtime capped to worst-case chunk performance
- Killing hung job wastes all previous work (no checkpointing)

Batch Scheduling Delays:
- Lower-priority batch jobs experience startup delays
- Excessive batch scheduler use risks preemptions
- Reducing interval below effective lower bound causes overlap/stacking""",
        source="SRE Book Ch.25 - Challenges with Periodic Pipeline Pattern",
    ),
    "failure_corrupt_data": SREKnowledgeTopic(
        name="Corrupt Data Failure Mode",
        keywords=["corrupt", "incorrect", "bad", "invalid", "error", "bug", "regression"],
        content="""Corrupt Data Failure Mode (Google SRE Workbook):

Causes of Data Corruption:
- Software bugs in pipeline code
- Data incompatibility between stages
- Unavailable regions causing partial data
- Configuration bugs
- Schema changes without backward compatibility

Recovery Steps:
1. Mitigate: Prevent further corrupt data from entering system
2. Restore: Restore from known good version OR reprocess to repair

If single region serving corrupt data:
- Drain serving/processing jobs from affected region
- Roll back offending binary/config quickly

Downstream Impact:
- Corrupt output propagates to dependent jobs
- Serving jobs may serve incorrect data to users
- May need to reprocess windows of incorrect data after fix""",
        source="SRE Workbook Ch.13 - Corrupt Data",
    ),
    "hotspotting": SREKnowledgeTopic(
        name="Hotspotting and Load Patterns",
        keywords=[
            "hotspot",
            "bottleneck",
            "overload",
            "cpu",
            "memory",
            "resource",
            "contention",
        ],
        content="""Hotspotting in Pipelines (Google SRE Workbook):

Definition: Resource becomes overloaded from excessive access, causing failures.

Common Examples:
- Multiple workers accessing single serving task causing overload
- CPU exhaustion from concurrent access to data on one machine
- Row-level lock contention in databases
- Concurrent hard drive access exceeding physical limits
- Single large work unit consuming disproportionate resources

Mitigation Strategies:
1. Block fine-grained data (individual records) to let rest of pipeline progress
2. Dynamic rebalancing - break large work into smaller pieces
3. Build emergency shutdown into client logic
4. Skip problematic input data via flag/config
5. Restructure data/access patterns to spread load evenly
6. Reduce lock granularity to avoid contention

Moiré Load Pattern:
- Two or more pipelines occasionally overlap execution
- Simultaneous consumption of shared resources
- Peak impact when aggregate load spikes
- Most apparent in plots of shared resource usage""",
        source="SRE Workbook Ch.13 - Reduce Hotspotting",
    ),
    "thundering_herd": SREKnowledgeTopic(
        name="Thundering Herd Problem",
        keywords=["thundering", "herd", "spike", "burst", "concurrent", "retry", "flood"],
        content="""Thundering Herd Problem (Google SRE Book):

Definition: For each cycle of large periodic pipeline, potentially thousands of
workers immediately start work, overwhelming:
- Servers running the workers
- Underlying shared cluster services
- Networking infrastructure

Compounding Factors:
- Missing retry logic: Work dropped on failure, job not retried
- Naive retry logic: Retry on failure compounds the problem
- Human intervention: Adding more workers when job doesn't complete

Result: Nothing harder on cluster infrastructure than a buggy 10,000 worker job.

Prevention:
- Implement exponential backoff with jitter for retries
- Rate limit worker startup
- Use circuit breakers for dependent services
- Monitor aggregate resource usage across pipelines""",
        source="SRE Book Ch.25 - Thundering Herd Problems",
    ),
    "monitoring_pipelines": SREKnowledgeTopic(
        name="Pipeline Monitoring",
        keywords=["monitoring", "metrics", "alerting", "observability", "telemetry"],
        content="""Pipeline Monitoring Best Practices (Google SRE Workbook):

Standard Model Issues:
- Metrics collected during execution, reported only on completion
- If job fails, no statistics provided
- Real-time data important for operational support and emergency response

Continuous vs Periodic:
- Continuous pipelines have tasks constantly running with real-time metrics
- Periodic pipelines often lack real-time monitoring by design

Required Monitoring:
1. Number of work units in various completion stages
2. Latency and aging information for each stage
3. Throttling and pushback rationale
4. Resource usage limiting factors
5. Worker machine state distribution
6. Failing, stuck, or slow work unit counts
7. Historical run statistics

End-to-End Measurement:
- Don't just measure per-stage SLOs
- Per-stage monitoring misses customer experience
- Can miss end-to-end data corruption bugs
- Both stages may report "well" while user doesn't see data""",
        source="SRE Workbook Ch.13 - Monitoring",
    ),
    "dependency_failure": SREKnowledgeTopic(
        name="Dependency Failure Planning",
        keywords=["dependency", "upstream", "downstream", "external", "third-party", "sla"],
        content="""Planning for Dependency Failure (Google SRE Workbook):

Key Principle: Don't overdepend on SLOs/SLAs of other products.

Steps:
1. Identify third-party dependencies
2. Design for largest failure in their advertised SLAs
3. Example: If single-region uptime guarantee insufficient, replicate across regions

When Dependencies Break SLAs:
- Can negatively impact dependent pipelines
- If you depend on stricter guarantees than advertised, you fail within their SLA
- May need to accept lower reliability and offer looser SLA to customers

Google DiRT (Disaster Recovery Testing):
- Stage planned outages to test resilience
- Simulate regional outages
- Well-prepared pipelines auto-failover
- Others delayed until manual intervention
- Manual failover assumes sufficient resources in another region""",
        source="SRE Workbook Ch.13 - Plan for Dependency Failure",
    ),
    "recovery_remediation": SREKnowledgeTopic(
        name="Recovery and Remediation",
        keywords=["recovery", "rollback", "remediation", "fix", "restore", "reprocess"],
        content="""Pipeline Recovery Strategies (Google SRE Workbook):

Immediate Response:
1. Mitigate impact - prevent further bad data entering system
2. Roll back binary/config if software/config bug
3. Drain affected region if regional issue

Data Restoration:
- Restore from previously known good version
- Reprocess to repair data
- Consider selective reprocessing (only impacted users/accounts)
- Use intermediate checkpoints to avoid full end-to-end reprocess

For Incompleteness: Redeliver events from last known-good checkpoint
For Excessive Skewness: Reshuffle events to correct hourly buckets

Post-Recovery:
- Strongly advise customers to reprocess their downstream data
- Document recovery steps taken
- Update runbooks with lessons learned

Rollback Considerations:
- Tie code changes to releases for fast rollbacks
- Have tested backup/restore procedures
- Ensure easy region draining capability""",
        source="SRE Workbook Ch.13 - Pipeline Failures",
    ),
    "resource_planning": SREKnowledgeTopic(
        name="Resource Planning and Autoscaling",
        keywords=["autoscaling", "capacity", "resource", "quota", "scaling", "provision"],
        content="""Resource Planning for Pipelines (Google SRE Workbook):

Autoscaling Benefits:
- Handle workload spikes without manual intervention
- Don't provision for peak load 100% of time
- Turn down idle workers to save costs
- Critical for streaming pipelines and variable workloads

Capacity Planning:
- Predict future growth and allocate accordingly
- Weigh resource cost vs engineering effort for efficiency
- Consider: storage costs, network bandwidth, cross-region replication
- Periodically examine dataset and prune unused content

Resource Measurement:
- Measure efficiency at each individual stage (not just end-to-end SLO)
- Track which jobs responsible for resource usage increases
- Focus engineering effort on high-usage jobs

Autoscaler Pitfalls:
- Requires strong correlation between CPU and work performed
- Can scale indefinitely if CPU-work correlation breaks
- Limit maximum instances Autoscaler can use
- Restrict CPU usage of daemons on instances
- Throttle CPU when no useful work being done""",
        source="SRE Workbook Ch.13 - Autoscaling and Resource Planning",
    ),
    "pipeline_documentation": SREKnowledgeTopic(
        name="Pipeline Documentation",
        keywords=["documentation", "runbook", "diagram", "playbook", "process"],
        content="""Pipeline Documentation Best Practices (Google SRE Workbook):

Three Categories of Documentation:

1. System Diagrams:
   - Show each component (pipeline apps and data stores)
   - Show transformations at each step
   - Include quick links to monitoring/debugging info
   - Display current status of each stage
   - Show historical runtime information

2. Process Documentation:
   - How to release new pipeline version
   - How to introduce data format changes
   - Initial service turnup procedures
   - Final service turndown in new region
   - Automate documented tasks where possible

3. Playbook Entries:
   - Each alert condition should have corresponding playbook entry
   - Link documentation in alert messages
   - Describe steps to recovery
   - Keep playbooks up to date with system changes""",
        source="SRE Workbook Ch.13 - Create and Maintain Documentation",
    ),
    "workflow_patterns": SREKnowledgeTopic(
        name="Continuous Pipeline Patterns",
        keywords=["workflow", "continuous", "leader", "follower", "prevalence", "mvc"],
        content="""Continuous Pipeline Patterns (Google SRE Book):

Google Workflow Design (Leader-Follower + System Prevalence):
- Model: Task Master holds all job states in memory
- View: Workers continually update state transactionally
- Controller: Optional component for auxiliary activities

Workflow Correctness Guarantees:
1. Configuration tasks create barriers for work
2. All committed work requires valid lease held by worker
3. Output files uniquely named by workers
4. Client/server validate Task Master via server token

Benefits over Periodic Pipelines:
- Strong guarantees about job completion
- Global consistency via distributed storage
- Automatic failover between regions
- No undefined state on failures

When to Use Continuous vs Periodic:
- If problem is continuous or will grow to be continuous, use continuous
- Periodic pipelines are fragile under organic growth
- Continuous provides better scaling and reliability""",
        source="SRE Book Ch.25 - Google Workflow",
    ),
}


def get_topics_for_keywords(keywords: list[str]) -> list[str]:
    """Find topic names that match the given keywords.

    Args:
        keywords: List of keywords to match against topic keywords

    Returns:
        List of matching topic names, sorted by relevance (most matches first)
    """
    if not keywords:
        return []

    keywords_lower = [kw.lower() for kw in keywords]
    topic_scores: list[tuple[str, int]] = []

    for topic_name, topic in SRE_TOPICS.items():
        score = sum(
            1
            for kw in keywords_lower
            if any(kw in topic_kw or topic_kw in kw for topic_kw in topic.keywords)
        )
        if score > 0:
            topic_scores.append((topic_name, score))

    topic_scores.sort(key=lambda x: -x[1])
    return [name for name, _ in topic_scores]


def get_sre_guidance(
    topic: str | None = None,
    keywords: list[str] | None = None,
    max_topics: int = 3,
) -> dict:
    """Retrieve SRE best practices for data pipeline incidents.

    Useful for:
    - Understanding pipeline failure patterns
    - Applying SLO concepts to data freshness issues
    - Getting remediation guidance for common failures
    - Structuring postmortem findings

    Args:
        topic: Specific topic to retrieve (e.g., "failure_delayed_data")
        keywords: Keywords to match against SRE content
        max_topics: Maximum number of topics to return when using keywords

    Returns:
        Dictionary with matched topics, content, and source references
    """
    result: dict = {
        "success": True,
        "topics": [],
        "guidance": [],
        "sources": [],
    }

    # If specific topic requested, return it directly
    if topic and topic in SRE_TOPICS:
        sre_topic = SRE_TOPICS[topic]
        result["topics"] = [topic]
        result["guidance"] = [
            {
                "topic": sre_topic.name,
                "content": sre_topic.content,
                "source": sre_topic.source,
            }
        ]
        result["sources"] = [sre_topic.source]
        return result

    # If keywords provided, find matching topics
    if keywords:
        matching_topics = get_topics_for_keywords(keywords)[:max_topics]
        for topic_name in matching_topics:
            sre_topic = SRE_TOPICS[topic_name]
            result["topics"].append(topic_name)
            result["guidance"].append(
                {
                    "topic": sre_topic.name,
                    "content": sre_topic.content,
                    "source": sre_topic.source,
                }
            )
            result["sources"].append(sre_topic.source)

    # If no matches found
    if not result["topics"]:
        result["success"] = False
        result["message"] = "No matching SRE guidance found for provided keywords"

    return result

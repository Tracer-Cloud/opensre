# How To Build Our Agent Overtime (notes for humans)
**Core Mission Up To Mid February**
- Build commercial momentum through incremental technical demonstrations that showcase value and establish domain authority.

# Strategic Priorities
**what to optimize for**
What We're Optimizing For
1. Technical demonstrations of successful investigations
2. Content marketing from engineering results to build domain authority
3. Incremental complexity while marketing each milestone clearly

**Sweet Spot: Medium Difficulty Issues**
- Focus area: Medium to medium-hard difficulty problems (maximum value delivery)
- Avoid: Hard/extremely hard issues requiring code changes or vendor-specific knowledge (unreliable ROI at this stage)
- Easy cases still valuable: Aid in surfacing root causes buried in log

**Content Marketing Philosophy** 
- Most effective approach: Deliver actual value to the audience
- How: Share our own learnings and insights from investigations
- Key action: Document learnings systematically to build commercial momentum

# Alert and Issue Difficulty Framework

**Classification Dimensions v0.1** 
Test cases evaluated across 3 axes:
1. Information availability: In alert → needs investigation → needs code changes → external vendor
2. Required expertise: Junior → senior → team → external
3. Time to resolution: Minutes → hours → days → weeks

**classification todo's**
- We need to validate our understanding of test cases 

### 🟢🟢 Very Easy testcases with no need for tracer 
- Information Availability: Root cause analysis is already available in the alert due to good configuration and accurate pinpointing

### 🟢 Easy testcases 
- Resolution Time: Minutes to 1 hour
- Information Availability: Complete but needs to be dug up - error is surfaced in logs
- Required Level: Junior developer
- Collaboration: None needed

**Characteristics:**
- Clear actionable error messages that directly state the problem
- Obvious root cause from logs or system output
- Well-documented solutions exist
- No additional data needed - existing logs are sufficient

**Examples:**
- "API key not set" → Add API key to config
- "Billing limit exceeded, upgrade your plan" → Upgrade subscription
- "Dependency incompatibility: update package X to v2.1.3" → Update package
- HTTP 404 with clear missing endpoint → Add route or fix URL
- Database connection refused → Check if database is running

**Impact Tracer Can Have - Value Proposition for Easy Cases:** 
- Root cause surfacing: Alert says "Airflow DAG failed" but doesn't say why. Tracer surfaces the actual error: "S3 credentials expired" from buried logs
- Historical context: "This is the 3rd time this month credentials expired for this connector - suggests rotation automation needed"
- Contextual routing (future add on that we can add): Auto-route "BigQuery quota exceeded" to data platform team, "Stripe API auth failed" to billing team
- Cognitive load reduction: Engineer doesn't need to check 5 different places (Airflow UI, CloudWatch, S3 logs, Slack alerts, PagerDuty)

**Impact Tracer Can Have - Competitive Positioning:** 
- Even though the fix is simple, Tracer delivers value through:
- Time savings: 30 seconds to identify vs 10 minutes of log diving
- Better triage: Right person gets the right context immediately
- Pattern detection: Spots recurring issues that suggest systemic problems
- Onboarding aid: Junior engineers can resolve without senior help

**Critical Question: Is this enough to get data engineers to try Tracer?**
- Hypothesis: Yes, if we can demonstrate 50-80% reduction in time-to-identify-root-cause
- Validation needed: Run 10 real customer scenarios and time with/without Tracer


### 🟡 Medium Difficulty Test Cases
**overview**
- Resolution Time: 2-6 hours
- Required Level: Mid-level engineer with domain knowledge
- Information Availability: Partial - needs correlation across multiple sources

**Characteristics:**
- Vague error messages requiring investigation and research, (now a days a lot of information can be extracted from ChatGPT)
- Multiple potential root causes need to be eliminated
- Could Require correlation of multiple logs or metrics
- Domain knowledge needed to understand system behavior
- Existing observability is sufficient but needs analysis

**Examples:**
- Github CI/CD pipeline deployments for test cases, where the issues do not appear in local development, but do in production.
- Intermittent timeouts → Investigate load patterns, database queries, network latency
- Memory leak → Analyze heap dumps, identify leaking objects
- Race condition → Reproduce, add timing logs, identify critical section
- Performance degradation → Profile code, check resource utilization
- Failed test with unclear assertion → Debug test logic, understand expected behavior

**Resolution Pattern:**
- Gather relevant logs/metrics from existing tools
- Form hypotheses about root cause
- Test hypotheses through experimentation
- Implement fix
- Verify across multiple scenarios

### 🟠 Hard Test Cases 
**overview** 
Resolution Time: Days to 1 week
Required Level: Senior engineer, potentially small team
Collaboration: Within team (developers, DevOps, maybe QA)

**Characteristics:**
- Information deficit - existing logs don't show the problem
- Requires code instrumentation - need to add logging, metrics, or tracing
- Complex system interactions - multiple services or components involved
- Non-reproducible in dev environment - happens only in production/specific conditions
- Requires system knowledge of internal architecture

**Examples:**
- Data corruption with no audit trail → Add detailed logging to track state changes
- Distributed system failure with incomplete traces → Add distributed tracing
- Silent data loss → Instrument data pipeline to find where data disappears
- Flaky integration test → Add detailed step logging to identify failure point
- Unexpected behavior in specific customer environment → Add customer-specific telemetry

**Resolution Pattern:**
- Identify information gaps in current observability
- Add instrumentation (logging, metrics, tracing)
- Deploy changes and wait for issue to recur
- Analyze new data to form hypothesis
- Implement fix
- Verify with enhanced monitoring

### 🔴 Extremely Hard Test Case
- Resolution Time: Weeks to months
- Required Level: Senior engineers + external experts
- Collaboration: Cross-team + vendor support + possibly community

**Characteristics:** 
- External dependencies causing the issue
- Limited community knowledge
- Vendor bugs or undocumented behavior
- Is typically only resolved by software engineers after working with vendors on a call or public forum posts



# Technical Learnings & Priorities
Core Mantra
- Bet on vectors of maximum progress
- Follow paths of least resistance
- Avoid "hard" problems at this stage
- Focus on "easy" and learn maximum information

### Metrics Interpretation Challenge
Current Issue:
- LLMs struggle to accurately interpret metrics data (e.g., Superfluid pipeline RAM data)
- Agent makes inaccurate claims about API-outputted metrics

Action Item:
- Create Linear task to resolve metrics interpretation

Strategic Decision:
- Prioritize text-based issues: Maximum vector of progress
- Don't exclude metrics entirely: Be targeted and mindful to avoid time burial

### Platform Advantages
Context Ontology:
- Single place aggregating pipeline steps, data, and cloud infrastructure
- Extremely helpful for agent investigations

User Network Effects:
- Daily monitoring usage → Users configure pipelines for visibility in Tracer
- Accumulates accurate data pipeline ontology
- More pipelines covered → More integrations → More agent actions/information available

Current Weakness:
- Deep OS-level information from Rust agent has lower ROI on effort investend than expected
- eBPF agent is enabling technology but hasn't delivered proportional value yet

Engineering Resource Allocation
- Top Priority: API Restructuring for Agents

Why this matters:
- Restructure API responses to provide context for AI agents (units, explanations, etc.)
- Making APIs agent-friendly is a major competitive advantage
- Small startup = can move and adjust quickly

eBPF Agent Caution
- Historical ROI lower than hoped
- Enabling technology that can improve agent capabilities
- Be judicious about time investment


# Fundamental Challenges to Solve
### Feedback Loop Problem
The Issue:
- Development agents get direct, fast feedback from environment
- This fast feedback loop is why they're incredibly effective
- Our investigation agents lack this same rapid feedback mechanism

Implication:
- Need to design faster feedback loops for our investigation workflow

# Roadmap 
### Milestone #1: First Successful Investigation
Goal: 
- Complete our first successful easy investigation

Path to Success:
- Create simple test cases the agent can actually accomplish
- Current Superfluid test cases are too difficult
- Start with easy wins to build momentum

Next Steps
- Validate classification understanding of test cases
- Document learnings from each investigation
- Build content marketing from successful cases
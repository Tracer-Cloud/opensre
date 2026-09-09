

# LIST OF METRICS THAT CAN BE USED THROUGHOUT
**objective**
- Optimize GitHub CICD operations for high developer productivity

**list of metrics:**

**Metric #1: Red time on main**
Best top-line reliability indicator. Answers: “How unavailable/unhealthy is our mainline CI?”

**Metric #2: Mean time to green**
Measures recovery. Two orgs can have the same red time but radically different incident/recovery patterns.

**Metric #3: Flake rate**
Probably the strongest CI-specific developer-friction signal you can derive without logs. Same-SHA fail→pass is simple and explainable.

**Metric #4: Normal CI duration**
Captures the unavoidable feedback-loop latency developers experience when everything works correctly.

**Metric #5: Merged PRs delayed by a flake**
Connects CI instability to actual delivery impact instead of just reporting infrastructure statistics.



# We will start by delivering some high level analytics first

The following metrics should be useful for users, and do not provide any complicated metrics. 


# Phase #1 High Level Metrics (that are simple to calculate)
The high level metrics that we want to calculate are:

[1] Avg time CICD is red 
[2] Separate approval gates from CI failures

# Phase #2 Delivery of insights 
- In this stage we make a comparison to other well known libraries such as Kubernetes. 
- Because we can deliver 
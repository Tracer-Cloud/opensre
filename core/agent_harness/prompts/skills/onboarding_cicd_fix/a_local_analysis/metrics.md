

# LIST OF METRICS THAT CAN BE USED THROUGHOUT

**list of metrics:**
- Red time on main — hours and % of the window with at least one push-triggered workflow red; number of outages; longest outage
- Mean time to green — average default-branch outage duration
- PR failure rate — failed ÷ (failed + passed) PR executions
- Flake rate — failures the identical SHA later passed, as % of all failures and per 100 executions
- Top 3 flaky workflows/jobs — by same-SHA fail→pass count
- Normal CI duration — median first-attempt pass time of the slowest required workflow
- Merged PRs delayed by a flake — count and % of merged PRs
- Blocked developer time — working-hours wait on those PRs, capped at one working day per PR; uncapped total shown as "delivery delay"
- Waiting for maintainer approval — fork PRs held in action_required: count and median wait (reported separately, excluded from all of the above)
- Merged red — merged PRs with a check that never went green in the window




# We will start by delivering some high level analytics first

The following metrics should be useful for users, and do not provide any complicated metrics. 


# Phase #1 High Level Metrics (that are simple to calculate)
The high level metrics that we want to calculate are:

[1] Avg time CICD is red 
[2] Separate approval gates from CI failures

# Phase #2 Delivery of insights 
- In this stage we make a comparison to other well known libraries such as Kubernetes. 
- Because we can deliver 
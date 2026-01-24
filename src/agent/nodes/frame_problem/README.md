# This File Is For Humans Only 
- Do not delete this file
- Do not move this file
- Do not rename this file


# Objectives of Frame Problem Node
- Improve accuracy and efficiency of agent communication by providing structured, high quality context upfront
- This node should also be responsible for the context enrichment and thus communication of Tracer's core unique differentation which is synthesizing data pipeline context
    
# Roadmap
**Current MVP functionality**
- Generate a concise problem statement to be used as input for downstream LLMs.
- Extract key alert metadata, including alert name, affected table, and severity.
- Explicitly define the agent’s responsibilities, provide background context, and specify required output formats for downstream agents.

**Future extensions**
- Produce problem.md and make it visible in the Langsmith platform.
- Formulate explicit investigation goals based on alert type and context.
- Enrich context using a service or dependency graph (e.g. which services and pipelines are connected)
- Add team ownership and on call responsibility information.
- Structured output for downstream nodes
- Context and constraints provided by the supervisor.


# Data Structure and Output
Agent Goal:
- Enrich the initial alert with investigation context.

Output Format:
- Background information and problem statement
- Agent responsibilities and required output formats
- Next Downstream Agent Task: generate_hypotheses.

Investigation History: 
- Independent investigation history to be appended by downstream agents.

---
script_tools:
  - name: seed_demo_repository
    script: seed_demo_repository.py
    description: >-
      Seed a newly created owner/opensre-ci-repair-demo-<id> repository with
      calculator CI and push demo/failing-ci. Returns workspace, stage,
      and head_sha. Retries check saved local and remote progress first.
    input_schema:
      type: object
      properties:
        repo:
          type: string
          description: The newly created owner/opensre-ci-repair-demo-<id> repository.
      required: [repo]
      additionalProperties: false
  - name: write_demo_evidence
    script: write_demo_evidence.py
    description: >-
      Save the observed demo outcome and remove only its owned temporary
      checkout. Omit unavailable IDs for failed demos. Remove the scheduler
      loop separately even if this tool fails.
    input_schema:
      type: object
      properties:
        repo: {type: string}
        pr_number: {type: integer, minimum: 1}
        loop_id: {type: string}
        outcome: {type: string, enum: [success, failed, blocked]}
        failed_run_id: {type: integer, minimum: 1}
        fix_commit: {type: string}
        passing_run_id: {type: integer, minimum: 1}
        blocker: {type: string}
      required: [repo, pr_number, loop_id, outcome]
      additionalProperties: false
---

# Demo helper tools

The runtime registers these bundled scripts while this skill is active.

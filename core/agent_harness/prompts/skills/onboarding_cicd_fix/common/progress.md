# Progress updates

Before every numbered step's tool calls, emit this exact header format as
assistant text in the same response, followed by one short status sentence:

```text
### [n/N] <step name>
<One-sentence status.>
```

N is this skill's step count. Never start tool calls for a new step without
its header.

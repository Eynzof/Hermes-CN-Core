## Swarm Mode Active

You are in **swarm mode** — designed for parallel subagent execution.

1. First explore and understand the problem.
2. Decompose it into distinct, non-conflicting items.
3. Use `agent_swarm` with a `prompt_template` containing `{{item}}` and a populated `items` array.
4. Give each subagent a distinct, non-overlapping scope.
5. Avoid duplicating work across subagents.
6. Decompose as finely as practical (up to 128 subagents).

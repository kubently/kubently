Recommended Architecture: SSE + POST + Redis Pub/Sub
This architecture cleanly separates the different communication channels, ensuring maximum reliability and scalability.

Here is the step-by-step data flow:

Phase 1: Agent Connects

One of your Cluster Agents starts up. It establishes a persistent SSE connection (a simple HTTP GET request) with your server's load balancer/ingress.

Your K8s Ingress routes this to any available server pod (let's say Pod A).

Pod A holds this connection open. It then subscribes to a specific Redis Pub/Sub channel dedicated to that agent (e.g., agent-commands:agent-123). Now, Pod A is just listening to Redis, waiting for a message on that specific channel.

Phase 2: User Sends Command
4.  A User sends a command from their Chat Client (e.g., /run-check agent-123).
5.  This is a standard HTTP POST request that hits your K8s Ingress. The ingress routes it to any available pod (let's say Pod B, which is a different pod).
6.  Pod B receives the command. It knows it needs agent-123 to execute it.
7.  Pod B does NOT try to find Pod A. Instead, it generates a unique request-id and simply PUBLISHES the command payload (including the request-id) to the Redis channel: agent-commands:agent-123.

Phase 3: Command Execution
8.  Redis instantly pushes this message to all its subscribers for that channel. The only subscriber is Pod A.
9.  Pod A receives the message from Redis and immediately sends it down the open SSE connection it's holding for agent-123.
10. The Agent receives the command via SSE, executes it, and gets the output.

Phase 4: Output is Returned
11. The Agent takes the output, bundles it with the unique request-id it was given, and sends it back in a brand new, stateless HTTP POST request to your server (e.g., /api/agent-output).
12. Your Ingress receives this POST and routes it to any available pod (let's say Pod C).
13. Pod C looks at the request-id, matches it to the original request (likely stored in Redis or a database), and then sends the final result back to the Chat Client.


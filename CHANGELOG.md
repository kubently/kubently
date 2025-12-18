# Changelog

## [Unreleased] - 2025-12-03

### Added
- **Executor Capability Reporting (Optional Feature)**
  - Executors can now advertise their DynamicCommandWhitelist configuration to the central API
  - New `CapabilityModule` stores capability data in Redis with TTL-based expiration
  - API endpoints: `POST /executor/capabilities`, `POST /executor/heartbeat`, `GET /api/v1/clusters/{cluster_id}/capabilities`
  - New cluster detail endpoint: `GET /debug/clusters/{cluster_id}` shows comprehensive cluster status including capabilities
  - Helm configuration: `executor.capabilities.enabled` and `executor.capabilities.heartbeatInterval`
  - Graceful degradation: feature is optional, disabled by default, and failures don't affect core functionality
  - Backwards compatible: older executors work with new API, new executors work with older API
  - Input validation: mode enum restriction, max list sizes to prevent oversized payloads
  - Capability cleanup on token revoke: prevents stale advertisements
  - Reviewed by Gemini 3 and GPT-5 with identified issues addressed

## [Unreleased] - 2025-09-27

### Added
- **Maintainer Attribution**
  - Added Adam Dickinson as project maintainer to README.md
  - Added Adam Dickinson as project lead to CONTRIBUTING.md
  - Contact email: hello@kubently.io

## [Unreleased] - 2025-09-27

### Added - Open Source Infrastructure
- **Critical Open Source Documentation**
  - Added SECURITY.md with vulnerability reporting process and security best practices
  - Created comprehensive GitHub issue templates (bug report, feature request, question)
  - Added pull request template with checklist and DCO requirements
  - Configured Dependabot for automated dependency updates (Python, Node.js, Docker, GitHub Actions)
  - Added project badges to README for license, versions, and contribution status

### Changed
- **Removed cnoe-io/agent-chat-cli References**
  - Removed all documentation references to the cnoe-io/agent-chat-cli tool
  - Updated test scripts to use curl with proper x-api-key authentication instead
  - The cnoe-io tool doesn't support our x-api-key header authentication method

### Validated
- **CONTRIBUTING.md** - Comprehensive contribution guidelines including:
  - Development setup instructions
  - Testing and code style requirements
  - DCO (Developer Certificate of Origin) sign-off process
  - Pull request workflow
- **LICENSE** - Apache 2.0 license already in place

## [Unreleased] - 2025-09-17

### Added
- **TodoWrite Tool Implementation**
  - Added task management tool similar to Claude Code for systematic debugging workflows
  - Created `TodoManager` class to track debugging tasks with status (pending, in_progress, completed)
  - Integrated `todo_write` tool into the A2A agent for planning and tracking investigation steps
  - Provides progress visibility with completion percentages and formatted displays
  - Updated system prompts to include TodoWrite tool usage guidelines
  - Essential for complex multi-step debugging scenarios

- **Enhanced Test Runner rerun-failed Command**
  - Added `--from` parameter to specify source of test results for reruns
  - Can point to specific test result directories (e.g., `test-results-20241210_143022/`)
  - Can point to specific result files (e.g., `report.json` or individual scenario JSON files)
  - Automatically extracts and reruns only failed tests from specified results
  - Example: `./run_tests.sh rerun-failed --api-key KEY --from test-results-20241210_143022/`
  - Fixed issue where rerun-failed was using incorrect `--scenarios` parameter instead of `--scenario`

### Changed
- **Major Agent Refactoring for Thorough Investigation**
  - **Tool Consolidation**: Removed `get_pod_logs` and `debug_resource` tools, keeping only `execute_kubectl`
    - Single flexible tool matches Q CLI's proven approach
    - Supports full kubectl command syntax with all flags and options
    - Enables more natural investigation patterns
    - Reduces cognitive overhead for the LLM
  - **Enhanced execute_kubectl Tool**:
    - Now accepts full kubectl commands as strings (e.g., "get pods -o wide")
    - Added security validation to block dangerous verbs (delete, create, apply, etc.)
    - Improved namespace handling for explicit and default namespaces
    - Added command parsing for structured logging
    - Increased timeout to 30 seconds for complex operations
  - **Investigation Tracking**:
    - Added `track_investigation_step()` method to monitor investigation progress
    - Tracks command, purpose, findings, and timestamp for each step
    - Implements minimum investigation steps (4) to ensure thoroughness
    - Added `should_continue_investigation()` to encourage deeper analysis
  - **System Prompt Overhaul** (version 3):
    - Emphasizes THOROUGH INVESTIGATION with 5-10+ kubectl commands
    - Provides explicit investigation patterns and examples
    - Prioritizes completeness and accuracy over efficiency
    - Includes detailed 10-step investigation example for service issues
    - Removes JSON response format constraints for natural investigation flow
  - **Implementation Based on Analysis**:
    - Follows KUBENTLY_IMPLEMENTATION_PLAN.md specifications
    - Implements TOOL_CONSOLIDATION_PLAN.md architecture
    - Matches Q CLI's thoroughness approach (average 4+ commands vs 2)
    - Enables unrestricted LLM investigation patterns

### Fixed
- **Test Automation Analyzer**: Fixed root cause determination to rely solely on Gemini analysis
  - Analyzer now ensures all required fields (root_cause_analysis, efficiency_analysis, quality_assessment) are present in response
  - Added intelligent fallback heuristics when Gemini fails to return expected JSON structure
  - Fallback compares agent response against expected fix keywords for confidence scoring
  - Test runner already relies on analyzer's assessment, no changes needed there
  - Fixes issue where valid root cause identifications were marked as failures

## [Unreleased] - 2025-09-15

### Changed
- **Executor RBAC Configuration**
  - Updated default RBAC rules to use generic read permissions for all APIs (`apiGroups: ["*"]`, verbs: `["get", "list", "watch"]`)
  - Simplified RBAC configuration from specific resource lists to wildcard permissions
  - Added support for overriding executor RBAC rules via Helm values
  - Added `executor.rbacRules` configuration option in values.yaml

## [Unreleased] - 2025-09-15

### Added
- **Comprehensive Q CLI Debugging Approach Analysis**
  - Analyzed Q CLI's debugging methodology vs Kubently for scenario 13 (service selector mismatch)
  - Created general thoroughness principles documentation (KUBENTLY_GENERAL_THOROUGHNESS_PRINCIPLES.md)
  - Created implementation plan for achieving Q CLI-level thoroughness (KUBENTLY_IMPLEMENTATION_PLAN.md)
  - Key finding: Q CLI's thoroughness comes from unrestricted tool access, not specific K8s debugging prompts
  - Q CLI performs average of 4+ kubectl commands per investigation vs Kubently's 2
  - Thoroughness emerges from natural LLM investigation patterns when efficiency constraints are removed

### Planned Changes
- **Tool Consolidation**: Remove get_pod_logs and debug_resource tools, keeping only execute_kubectl
  - Matches Q CLI's single-tool approach (execute_bash)
  - Enables more natural investigation patterns
  - Reduces cognitive overhead for the LLM
  - Created detailed TOOL_CONSOLIDATION_PLAN.md with security safeguards
- Remove structured response format constraints in agent.py
- Update system prompt to encourage thorough multi-step investigation
- Add investigation tracking and verification patterns
- Prioritize completeness over efficiency (user requirement)
- Implement progressive disclosure and multi-step verification patterns

## [Unreleased] - 2025-09-15

### Added
- **Enhanced execute_kubectl tool to accept additional arguments via extra_args parameter**
  - Added comprehensive security whitelist for safe kubectl flags like -o yaml, -o jsonpath
  - Updated API models to support extra_args field in ExecuteCommandRequest
  - Modified API endpoint to process and pass extra_args to kubectl commands
  - Agent tool now supports viewing full YAML manifests and specific fields with jsonpath
- **Added Anthropic API Key Support**
  - Updated `deploy-test.sh` to handle ANTHROPIC_API_KEY from .env file
  - Fixed environment variable loading to use `source .env` for proper handling of special characters
  - Modified Helm api-deployment.yaml to mount anthropic-key from llm-api-keys secret
  - Fixed secret creation logic to delete and recreate the secret cleanly
  - Application already supports anthropic provider via cnoe-agent-utils library
  - Set LLM_PROVIDER to "anthropic-claude" in test-values.yaml

## [Unreleased] - 2025-09-15

### Changed
- **System Prompt Refactoring** - Replaced rigid, prescriptive approach with adaptive, principles-based guidance
  - Removed strict decision trees and mandatory 7-step debugging workflow
  - Eliminated excessive MUST/ALWAYS directives except for safety constraints
  - Replaced brittle cluster name pattern matching with intelligent context discovery
  - Reduced examples from 6 detailed scenarios to 1 conceptual approach
  - Added deviation protocol allowing agent to adapt to novel situations
  - Shifted from treating LLM as script interpreter to leveraging reasoning capabilities
  - Version bumped from 1 to 2 in `prompts/system.prompt.yaml`
  - Based on consensus analysis from Gemini 2.5 Pro and GPT-5 models
- **System Prompt Enhancement** - Added critical resource discovery section
  - Added explicit guidance to prevent assuming resource names match namespace names
  - Requires listing resources before attempting to debug "the service" or "the deployment"
  - Addresses issue found in test scenario 13 where agent assumed service name incorrectly
- **Deployment Fix** - Updated Helm chart with new system prompt
  - Copied updated system.prompt.yaml (version 2) to Helm chart directory
  - Previous deployments were using old prompt (version 1) from Helm chart's local copy
  - This explains why test scenario 13 failed even after prompt improvements
  - Added automatic prompt sync to deploy-test.sh before building images
  - Cleaned up references to removed judge.prompt.yaml in documentation

### Fixed
- **Test Automation** - Fixed spurious error at end of test-and-analyze execution
  - Removed unused `run-all-scenarios.sh` utility script from scenarios directory
  - Logic for running all scenarios already exists in `test_runner.py`
  - Prevents "Scenario 00 not found!" error after all tests complete


## [Unreleased] - 2025-09-14

### Added
- **System Prompt Enhancements**
  - Added comprehensive **Standard Debugging Workflow** section that defines a 7-step process for investigating issues
  - Added **Error Handling Strategy** section to ensure agent continues investigation despite permission errors
  - Added explicit rule to check Kubernetes events first when investigating namespace issues
  - Added specific instruction to avoid using `kubectl get all` due to reliability issues
  - Emphasized controller-aware debugging approach that doesn't assume pods exist

### Changed
- **Test Automation Analyzer**
  - Updated to use Gemini 2.5 Pro instead of Flash for deeper analysis and better reasoning
  - Enhanced analyzer to be aware of current system prompt (`prompts/system.prompt.yaml`)
  - Refocused analysis on THREE KEY AREAS:
    1. System prompt improvements with exact text and placement
    2. Additional tool implementations with function signatures
    3. Agent/node architecture improvements with concrete suggestions
  - Added new JSON response sections for tool implementations and architecture improvements
  - Enhanced markdown report generation to display all recommendation categories
  - Analyzer now provides EXTREMELY specific suggestions instead of generic recommendations

## [Unreleased] - 2025-09-13

### Changed
- **Test Automation**
  - Removed heuristic analysis fallback - Gemini API is now required for all analysis
  - Fixed tool call capture from A2A SSE stream - tool calls are now properly captured instead of being inferred
  - Removed query hints from test scenarios to improve agent's native diagnostic capabilities
  - Updated a2a_test_client.py to capture tool calls and thinking steps from SSE events
  - Modified test_runner.py to use captured tool calls instead of inferring from response patterns
  - Updated test_runner.py to use GeminiAnalyzer instead of its own heuristic analysis
  - Fixed scenario 14 YAML formatting issue that was preventing proper setup
  - Updated SSE parsing to match actual A2A protocol (tool calls are not exposed)
  - Modified analyzer prompt to work without tool call data
  - Added test-results-*/ directories to .gitignore
  - **MAJOR UPDATE**: Implemented tool call exposure in A2A protocol
    - Created tool_call_interceptor.py to capture all tool executions
    - Modified agent.py to track tool calls via interceptor
    - Updated agent_executor.py to emit tool calls as SSE status events
    - Enhanced test automation to parse actual tool calls from SSE stream
    - Analyzer now uses real tool call data instead of inferring from responses
  - Fixed test-and-analyze command to respect --scenario flag for single scenario analysis

## [Unreleased] - 2025-09-12

### Fixed
- **Test Scenarios**
  - Fixed namespace naming to avoid revealing the issue in the namespace name
  - Updated test runner to extract namespaces from kubectl commands in scenario files
  - Fallback to generic names like `test-ns-13` instead of descriptive names
  - Updated all scenario files to use proper setup/cleanup argument handling
  - Removed echo statements that revealed the issue from scenario execution
  - Fixed test runner hanging due to kubectl log streaming - temporarily disabled log capture
  - Improved root cause detection logic to better identify when agent finds selector/label mismatches
  - Verified tests pass with generic namespace names - agent correctly diagnoses issues
- **Node.js CLI Debug Mode**
  - Fixed hanging on second question - inlined async operations within setImmediate() to ensure proper closure scope
  - Fixed process exiting after first command - added both process.stdin.resume() and keepalive interval
  - Removed continuous mode logic that was causing immediate menu prompts
  - Simplified debug command to run single sessions as intended
  - Root cause: Node.js readline bug (nodejs/node#42454) with async operations in event handlers
- **A2A Test Script**
  - Fixed test-a2a.sh to properly parse A2A response format
  - Now correctly extracts text from both status-update and artifact-update messages
  - All tests now pass correctly when agent asks for cluster specification
- **Test Runner**
  - Fixed test_runner.py to use correct A2A method (message/stream instead of message/send)
  - Added proper Server-Sent Events (SSE) parsing for streaming responses
  - Fixed hanging issue when running test scenarios
  - Updated to use official A2A Python client library (a2a-sdk package) when available
- **A2A Protocol Standardization**
  - Investigated official A2A Python client (`a2a-sdk>=0.1.0`) but found it requires complex setup with AgentCard
  - Fixed package confusion: `a2a` package is unrelated cloud comparison tool, correct package is `a2a-sdk`
  - Updated test infrastructure to use direct httpx implementation following A2A protocol spec
  - Created a2a_test_client.py using httpx for simple protocol-compliant testing
  - Added support for Python A2A test client in test-a2a.sh with fallback to curl
  - Updated documentation to clarify Kubently uses the standard A2A protocol
  - Added official A2A JavaScript client (`@a2aproject/a2a-client`) to Node.js CLI
  - All A2A tests pass successfully with the simplified implementation

### Added
- **Unified Test Automation System**
  - **Single Test Runner** (`test_runner.py`)
    - Captures complete tool call data including parameters and outputs
    - Tracks agent thinking process (Diagnostician and Judge nodes)
    - Records multi-round execution patterns
    - Integrates with service logs for full context capture
    - Stores LLM prompts for analysis
    - Creates timestamped result folders for each run
  - **Actionable Analysis Engine** (`analyzer.py`)
    - Analyzes tests with full context including system prompts
    - Provides specific, actionable recommendations with exact text changes
    - Generates implementation roadmaps with priorities
    - Produces both Markdown and JSON reports for tracking
    - Uses Gemini 2.0 Flash Thinking model for deeper analysis
  - **Unified CLI** (`run_tests.sh`)
    - Single command interface: `./run_tests.sh test-and-analyze --api-key KEY`
    - Supports test-only, analyze-only, and combined modes
    - Automatic dependency management with virtual environments
    - Timestamped result folders for better organization
  - **Health Check Log Suppression**
    - Custom logging filter to suppress `/health` endpoint logs
    - Reduces log noise in production environments
    - Configurable via `logging_config.py`

### Improved
- Test analysis now provides actionable insights instead of generic heuristics
- Success metrics include specific targets (95% success rate, 8/10 efficiency)
- Report format includes executive summary and phased implementation plan
- Tool call tracking now includes timing, parameters, and outputs
- Analysis includes pattern recognition across multiple scenarios
- Results are organized in timestamped folders for easy comparison

### Changed
- Consolidated multiple test runners into single unified system
- Main application uses custom logging configuration for health check suppression
- Docker deployment includes log configuration parameter for uvicorn
- Archived old test automation files to `archive/` directory

## [Unreleased] - 2025-09-11

### Fixed
- **Node.js CLI Readline Async Race Condition Fix**
  - Fixed critical issue where second question would hang without processing
  - Root cause: Known Node.js readline bug with async operations causing missed line events
  - This is documented in nodejs/node#42454 - readline breaks async program flow
  - Solution implemented:
    - Refactored to avoid async in readline event handler
    - Moved async operations to separate function called from sync handler
    - Used setImmediate to ensure prompt shows in next event loop tick
    - Added debug logging to trace readline state
  - Created test script (test-readline-async-fix.js) to verify the fix
  
- **Node.js CLI Event Loop Issue - Initial Fix**
  - Fixed issue where CLI exits after single question/response
  - Root cause: Node.js event loop becomes empty after async operations complete
  - Solution implemented:
    - Added `process.stdin.resume()` to keep stdin stream active
    - Added keepalive interval to ensure event loop never becomes empty
    - Proper cleanup on exit with `process.stdin.pause()` and interval clearing
  - Added debug logging and better error handling for troubleshooting
  - Added check to prevent concurrent operations while one is in progress

- **Node.js CLI Debug Command - Continuous Mode**
  - Added continuous mode when debug command is invoked with global --api-url and --api-key flags
  - Users can now use `kubently debug` directly and get prompted to continue or exit after each session
  - Maintains backward compatibility: debug command without global flags still runs single session

- **Node.js CLI Interactive Mode**
  - Simplified interactive.ts with a clean loop that automatically returns to menu after each session
  - Added debug logging to detect unexpected exits

### Removed
- **Python CLI Retired**
  - Removed Python CLI implementation in favor of Node.js CLI for better performance and maintainability
  - Deleted `kubently-cli/python/` directory and all Python-related build files
  - Updated GitHub workflows to only build and test Node.js CLI
  - Updated documentation to reflect single CLI implementation
  - Node.js CLI is now the sole maintained CLI with all features including OAuth 2.0 support

### Changed
- **LLM Provider Migration to Google Gemini 2.5 Flash - Complete**
  - Successfully migrated from OpenAI GPT-4o to Google Gemini 2.5 Flash
  - Updated `.env` file to use `GOOGLE_API_KEY` (standard Gemini API, not Vertex AI)
  - Modified `deployment/helm/test-values.yaml` with:
    - `LLM_PROVIDER: "google-gemini"`
    - `GOOGLE_MODEL_NAME: "gemini-2.5-flash"` (for our code reference)
    - `GOOGLE_GEMINI_MODEL_NAME: "gemini-2.5-flash"` (for cnoe-agent-utils library)
  - Updated `deploy-test.sh` to handle Google API key secret creation
  - Enhanced Helm chart to support both Google and OpenAI API keys (flexible provider switching)
  - Maintained backward compatibility - system supports easy switching between providers

### Fixed
- **Google Gemini Tool Calling Issue Resolved**
  - Fixed Gemini tool calling incompatibility with LangChain ReAct agents
  - Added missing `langchain-google-genai>=2.0.0` dependency for proper Google Gemini support
  - Maintained cnoe-agent-utils LLMFactory usage for multi-provider compatibility
  - Fixed timeout issues in kubectl tool execution (increased to 20s and added 30s HTTP timeout)
  - Added error handling for empty Gemini responses
  - System now executes kubectl tools successfully with Google Gemini 2.5 Flash
  - Added debug logging to confirm model initialization across providers

- **Simplified Architecture to Single Agent**
  - Removed problematic two-node graph architecture (Diagnostician + Judge nodes)
  - Reverted to single ReAct agent using `create_react_agent` directly
  - Eliminated Judge node that was causing infinite loops and recursion limit issues
  - Fixed agent to properly ask for cluster selection when none is specified
  - Agent now correctly executes kubectl commands and returns actual pod listings
  - Simplified code significantly by removing complex graph routing and loop detection logic
  - Fixed chunk handling in agent_executor.py for proper streaming responses
  - Corrected create_react_agent parameter usage (using 'prompt' parameter)

## [Previous] - 2025-09-10

### Added - Black Box Architecture Refactoring
- **Configuration Abstraction Layer**
  - Created `ConfigProvider` protocol for configuration access
  - Implemented `EnvConfigProvider` to centralize environment variable access
  - Removed all direct `os.getenv()` calls from auth modules
  - Added `OIDCConfig`, `APIConfig`, and `AuthConfig` dataclasses
  - Configuration is now injected, not accessed globally

- **Authentication Factory Pattern**
  - Created `AuthFactory` to construct authentication stack
  - All dependencies are injected via constructor
  - Factory returns only the `AuthenticationService` facade
  - Support for test doubles via `build_for_testing` method
  - No concrete constructors in application code

- **Authentication Service Facade**
  - Added `AuthenticationService` protocol defining the interface
  - Implemented `DefaultAuthenticationService` hiding all complexity
  - Standardized `AuthResult` dataclass for all auth responses
  - API layer depends only on the protocol, not implementations
  - Auth modules are now truly replaceable black boxes

- **Dependency Injection Throughout**
  - `TokenValidator` protocol for JWT validation
  - `OIDCValidator` accepts config via constructor
  - `EnhancedAuthModule` receives all dependencies
  - No module creates its own dependencies
  - Complete inversion of control

- **CLI Refactoring to Single Responsibility**
  - `AuthDiscoveryClient` - Only fetches discovery JSON
  - `OAuthDeviceFlowClient` - Only handles OAuth protocol
  - `CliAuthUI` - Only handles user interaction
  - `CliConfigStore` - Only manages configuration persistence
  - `LoginController` - Orchestrates without implementation details
  - Each module is independently testable and replaceable

- **Migration Support**
  - Created `migrate-to-black-box.sh` script for safe migration
  - Automatic backup of original files
  - Validation checks for new architecture
  - Rollback instructions included
  - Zero downtime migration path

### Changed - Black Box Principles Applied
- **Removed Environment Variable Leakage**
  - All `os.getenv()` calls moved to `EnvConfigProvider`
  - Modules receive configuration via injection
  - No implementation details exposed in interfaces

- **Eliminated Direct Module Instantiation**
  - Modules no longer create their dependencies
  - All wiring happens in composition root (factory)
  - True dependency inversion achieved

- **Simplified API Layer**
  - `main.py` now uses `AuthenticationService` facade
  - No direct access to auth module internals
  - Clean separation of concerns

### Added - OAuth 2.0 / OIDC Authentication
- **Dual Authentication System**: Implemented support for both API keys (machines) and OAuth/JWT (humans)
  - Created `EnhancedAuthModule` to handle both authentication methods seamlessly
  - API keys continue to work for backward compatibility and service accounts
  - JWT tokens validated against OIDC provider's JWKS endpoint
  - User identity extracted from JWT claims for audit logging

- **Mock OAuth Provider for Testing**
  - Implemented full OAuth 2.0 mock provider with Device Authorization Grant support
  - Includes OIDC discovery, JWKS, device authorization, and token endpoints
  - Supports multiple test users (test@example.com, admin@example.com)
  - Generates valid RS256-signed JWT tokens for testing
  - Run with `./run-mock-oauth.sh` for local testing

- **CLI OAuth Login Command**
  - Added `kubently login` command for OAuth 2.0 Device Authorization flow
  - Supports both OAuth flow and API key authentication (--api-key flag for legacy)
  - Automatically opens browser for user authorization
  - Stores tokens securely in ~/.kubently/config.json
  - Token expiration checking and refresh token support (ready for implementation)

- **Enhanced CLI Authentication**
  - Updated all CLI commands to support dual authentication modes
  - Config class now manages OAuth tokens alongside API keys
  - Dynamic auth header injection based on configured method
  - Graceful fallback to API key when explicitly provided

- **Deployment Configuration**
  - Added OIDC configuration to Helm values and ConfigMaps
  - Environment variables for OIDC issuer, client ID, JWKS URI
  - Support for both mock provider (testing) and real OIDC providers (production)

- **Integration Testing**
  - Created comprehensive OAuth integration test suite
  - Tests device authorization flow end-to-end
  - Verifies JWT validation and dual authentication
  - Added `test-oauth-flow.sh` for complete testing workflow

## [Unreleased] - 2025-09-09

### Cleaned Up A2A Configuration
- **Removed port 8000 references** - A2A is now always mounted at `/a2a` on port 8080
- **Removed `a2a_standalone_enabled` config** - Standalone mode no longer supported
- **Removed `a2a_port` config** - A2A uses main API port
- **Fixed Helm templates** - Removed A2A port from service and deployment templates
- **Updated deploy-test.sh** - Removed port 8000 port-forwarding
- **Fixed unused import** - Removed unused `Optional` import from config module
- **Removed "Judge's Analysis" label** - Judge responses now appear as natural answers without revealing internal role

### Added
- **Multi-Node Graph Architecture: Implemented LangGraph for A2A agent**
  - Created new "Judge" system prompt for root cause analysis (`prompts/judge.prompt.yaml`)
  - Added `JudgeDecision` Pydantic model for structured Judge output
  - Implemented two-node cyclical graph: Diagnostician (data gathering) → Judge (synthesis/decision)
  - Added conditional routing based on Judge decisions (COMPLETE/INCOMPLETE)
  - Separated concerns between data gathering and synthesis/decision-making
  - Improved cyclical troubleshooting capability for complex debugging scenarios

- **Unified Capability Storage in Redis**
  - Created comprehensive plan for storing executor capabilities in Redis
  - Eliminates need for meta-command queries by using push-based model
  - Executors report full capabilities (kubectl verbs, cloud SDK tools) on startup
  - Capabilities stored with cluster data for instant agent access
  - Includes TTL-based expiration with heartbeat refresh mechanism
  - Supports both Kubernetes and cloud provider capability discovery
  - Designed with clear namespacing for future extensibility
  - Provides abstraction layer for potential migration if needed

- **Cloud SDK Executor Architecture: Black Box design for cloud operations**
  - Designed cloud executor architecture following Black Box principles for maintainability
  - Each executor module (AWS, GCP) is completely replaceable with clean interfaces
  - Uses native SDKs (boto3, google-cloud-python) instead of CLI for type safety
  - Defined simple data primitives (CloudTool, ExecutionResult) for all operations
  - Full Kubernetes service account configuration with IRSA/Workload Identity
  - Read-only IAM permissions for security (no write/delete operations)
  - Complete deployment guide with Helm charts and security best practices
  - Container security with non-root user, read-only filesystem
  - Monitoring with metrics, structured logging, and distributed tracing

### Changed
- **Agent Architecture: Refactored from single ReAct to multi-node graph**
  - Replaced single ReAct agent with LangGraph StateGraph implementation
  - Created `diagnostician_node_runnable` for tool execution and data gathering
  - Created `judge_node_runnable` for data synthesis and routing decisions
  - Implemented `judge_decision_router` for conditional graph navigation
  - Uses `GraphState` TypedDict for state management across nodes
  
- **Prompt Loader: Enhanced flexibility for multiple prompt files**
  - Modified `kubently/modules/config/prompts.py` to support different prompt files
  - Added `default_filename` parameter to `get_prompt()` function
  - Updated `_candidate_paths()` to accept filename parameter
  - Enables loading of specialized prompts (system.prompt.yaml, judge.prompt.yaml)

### Fixed
- **Judge Agent Initialization: Fixed infinite recursion loop**
  - Corrected Judge agent initialization to properly bind system prompt to LLM chain
  - Created proper LCEL chain: `prompt | llm.with_structured_output(JudgeDecision)`
  - Fixed Judge not receiving its instructions, causing infinite INCOMPLETE loops
  
- **Prompt Updates: Enhanced loop prevention**
  - Updated Diagnostician prompt to report facts instead of asking questions
  - Added CRITICAL LOOP PREVENTION RULES to Judge prompt
  - Fixed ConfigMap deployment to include both system.prompt.yaml and judge.prompt.yaml
  - Agent now properly exits loops when cluster specification is missing

## [Unreleased] - 2025-09-08

### Fixed
- **Node.js CLI: Restored arrow key functionality in interactive mode**
  - Added missing lib directory files (a2aClient.ts, adminClient.ts, config.ts, templates.ts) from worktree
  - Enhanced TTY detection in run.js with warnings for non-TTY environments
  - Verified readline interface properly configured with terminal:true for arrow key support

### Changed

- **Repository Structure: Unified CLI directory structure**
  - Reorganized repository to place both Python and Node.js CLIs under `kubently-cli/` directory
  - Python CLI moved from `kubently-cli/` to `kubently-cli/python/`
  - Node.js CLI moved from `nodejs-cli/` to `kubently-cli/nodejs/`
  - Added unified README.md in `kubently-cli/` directory explaining both implementations
  - Created GitHub Actions workflow to build and package both CLI clients
  - This structure better reflects that both are implementations of the same CLI tool

### Security
- **A2A Authentication: Added mandatory API key validation for debug sessions**
  - Created reusable AuthMiddleware module following black box architecture principles
  - Implemented configurable authentication middleware supporting multiple formats (JSON, JSON-RPC)
  - Validates X-API-Key header against stored API keys in Redis
  - Supports path exclusions for public endpoints (agent card)
  - Logs authentication attempts and failures for security monitoring
  - Ensures debug sessions cannot bypass authentication requirements
  - Protects against unauthorized access to Kubernetes cluster operations

### Added
- **Middleware Module: Reusable authentication middleware for FastAPI apps**
  - Black box design with clean, documented interface
  - Supports API key and Bearer token authentication schemes
  - Configurable header names, skip paths, and error formats
  - Factory functions for common authentication patterns
  - Can be used by any FastAPI application or sub-application
  - Completely replaceable without affecting dependent modules

## [Unreleased] - 2025-09-07

### Added
- **Enhanced Logging: Comprehensive structured logging for A2A agent debugging**
  - Added structured JSON logging with trace IDs for request correlation
  - Logs full LLM prompts including system prompt and message history
  - Captures raw tool calls from LLM with complete parameter details
  - Records tool inputs and outputs for all kubectl operations
  - Tracks intermediate and final agent responses
  - Logs errors with full context and stack traces
  - Conditional logging activated only when A2A_SERVER_DEBUG=true
  - Enables deep visibility into agent reasoning and execution cycle
  - Critical for prompt engineering and debugging agent behavior

### Fixed

- **CLI: Fixed cluster context memory by removing ALL client-side manipulation**
  - Removed all client-side context management logic that was interfering with agent's memory
  - TUI now acts as a pure "dumb terminal" passing raw commands to the agent without any modification
  - Agent's built-in conversation memory (via thread_id) now properly maintains cluster context
  - Even when cluster is specified at launch, no manipulation occurs - user can specify cluster in their first command if needed
  - Fixes the issue where agent was losing context between messages

### Changed
- **CLI Enhancement: Optional cluster selection in debug command**
  - Made cluster field optional when launching `kubently debug` command
  - Added `-s/--select` flag to immediately show cluster selection menu
  - Users can now launch debug without specifying a cluster for multi-cluster queries
  - Updated TUI to handle optional cluster_id gracefully
  - Enhanced user experience by allowing flexible cluster selection workflow

### Removed
- **CLI: Removed exec command**
  - Removed `kubently exec` command from CLI
  - Removed `execute_single_command` method from admin and client classes
  - Updated all documentation to remove references to exec command
  - Users should use `kubently debug` for all interactive operations

## [Unreleased] - 2025-09-07

### Added
- **Security Enhancement: TLS/SSL by Default**
  - Executor TLS configuration with SSL verification and CA certificate support
  - Enhanced health check endpoint with TLS status reporting
  - Security headers and HTTPS enforcement in ingress configurations
  - Self-signed certificate generation script for local development
  - TLS-enabled docker-compose setup with nginx proxy for development
  - Comprehensive TLS documentation in README with security best practices

### Changed
- **BREAKING CHANGE: HTTPS Required for Production**
  - All executor deployments now default to HTTPS URLs
  - SSL certificate verification enabled by default (`KUBENTLY_SSL_VERIFY=true`)
  - Ingress configurations enforce TLS with security headers and HSTS
  - Docker-compose updated to use TLS termination proxy
  - All documentation examples updated to use HTTPS endpoints
- **Security Hardening**
  - Added CA certificate mounting in executor deployments
  - Implemented TLS protocol restrictions (TLSv1.2, TLSv1.3 only)
  - Added comprehensive security headers (HSTS, X-Frame-Options, etc.)

## [Unreleased] - 2025-09-06

### Added
- GitHub Actions optimizations with path filtering
- CI workflow for continuous integration
- Workflow status badges in README

### Changed
- **Port Migration**: Updated default API port from 5000 to 8080 across entire codebase
  - Configuration files: `.env.example`, `docker-compose.yaml`, Helm `values.yaml`
  - Documentation: All references in README, DEPLOYMENT, WARP, and SYSTEM_DESIGN docs
  - Test scripts: Updated all test scripts to use port 8080
  - CLI: Updated default port in CLI package
  - Kind deployment: Updated port mappings in kind-deploy.sh
  - Default port in config module changed from 5000 to 8080

## [Unreleased] - 2025-09-06

### Deployment & Testing
- Successfully deployed Kubernetes executor to kind cluster
- Fixed executor authentication with Redis token storage
- Verified natural language queries work through A2A interface
- Tested kubently CLI with command: `kubently --api-url http://localhost:8000 --api-key test-api-key debug kind`

## [Unreleased] - 2025-09-06

### Major Refactoring
- **BREAKING**: Renamed "Agent" to "Executor" for cluster components to resolve naming ambiguity
  - The AI Agent (LangGraph-based intelligence) remains "Agent"
  - The cluster component (kubectl runner) is now "Executor"
  - Environment variables changed from `AGENT_TOKEN_*` to `EXECUTOR_TOKEN_*`
  - Docker image renamed from `kubently/agent` to `kubently/executor`
  - API endpoints changed from `/agent/*` to `/executor/*`
  - Kubernetes resources renamed from `kubently-agent` to `kubently-executor`
  - Python class renamed from `SSEKubentlyAgent` to `SSEKubentlyExecutor`
  - Module files renamed from `sse_agent.py` to `sse_executor.py`

## [2.1.0] - 2025-09-06

### Removed
- **BREAKING**: Removed legacy polling agent implementation (`kubently/modules/executor/agent.py`)
- **BREAKING**: Removed smart agent implementation (`kubently/modules/executor/smart_agent.py`) 
- Removed `/agent/commands/legacy` endpoint from main API server
- Removed conditional agent type logic from agent Dockerfile
- Removed `POLL_INTERVAL` environment variable from all configurations
- Removed `FAST_POLL_INTERVAL` environment variable from all configurations
- Removed `AGENT_TYPE` environment variable from all configurations

### Changed
- Agent Dockerfile now directly executes SSE agent (`kubently.modules.executor.sse_agent`)
- Simplified agent deployment by using only the modern SSE implementation
- Updated documentation to remove references to legacy polling mechanisms

### Technical Details
The codebase has fully migrated to an event-driven SSE + Redis Pub/Sub architecture. All cluster agents now use the `sse_agent.py` implementation exclusively, eliminating the complexity and dead code paths from the previous polling-based approach.

## [2.0.0] - 2025-09-06

### Added
- **SSE (Server-Sent Events) Architecture**: Complete rewrite of agent communication using SSE for real-time streaming
- **Redis Pub/Sub**: Command distribution across multiple API pods via Redis channels
- **Horizontal Scaling**: True horizontal scaling support with unlimited API pods
- **Instant Command Delivery**: ~50ms command delivery (10-20x improvement)
- **SSE Agent**: New agent implementation using persistent SSE connections
- **Comprehensive Documentation**: Added SYSTEM_DESIGN.md, DEPLOYMENT.md, E2E_TEST_RESULTS.md

### Changed
- **Architecture**: Moved from polling to SSE + Redis pub/sub
- **Performance**: Reduced command latency from ~1000ms to ~350ms total
- **Scalability**: Can now scale to 1000+ concurrent agents
- **Network Overhead**: 90% reduction in network traffic (no polling)
- **Agent Types**: Default agent type changed to `sse` (was `legacy`)

### Removed
- Connection Registry module (replaced by Redis pub/sub)
- Smart Queue module (no longer needed with SSE)
- Routing module (Redis handles distribution)
- Polling-based communication (replaced by SSE)
- Sticky session configuration (not needed with pub/sub)

### Fixed
- Agent commands now correctly routed in multi-pod deployments
- A2A calls no longer experience timeouts
- Eliminated polling delays during active sessions
- Fixed horizontal scaling issues with agent connections

### Performance Improvements
- Command delivery: 1000ms → 50ms (20x faster)
- Network overhead: 90% reduction
- CPU usage: 80% reduction (no polling)
- Supports 1000+ concurrent agents (was ~100)

### Technical Details
- Uses Server-Sent Events (SSE) for agent streaming
- Redis pub/sub channels: `agent-commands:{cluster_id}`
- Stateless API pods with all state in Redis
- Automatic reconnection on connection failures
- Graceful handling of pod restarts

## [1.0.0] - 2025-09-05

### Initial Release
- Core API implementation with FastAPI
- Agent with polling-based communication
- Redis for state management
- Session-based debugging
- Authentication system
- Basic horizontal scaling (limited)
- A2A communication support
- MCP tool exposure
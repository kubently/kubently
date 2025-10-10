# Changelog

## [2.1.2] - 2025-01-11

### Fixed
- **ESM Module Compatibility**: Migrated from CommonJS to ESM modules to support chalk v5 and other ESM-only packages
  - Updated TypeScript configuration to compile to ES2022 modules
  - Added .js extensions to all relative imports for ESM compatibility
  - Fixed package.json version reading to use ESM imports
- **Readline Session Persistence**: Fixed issue where CLI would appear to exit after responding to queries
  - Moved pendingOperation flag update to finally block to ensure proper cleanup
  - Ensured prompt is always shown after async operations complete
- **Box Drawing Alignment**: Fixed rightmost edge alignment issue in welcome banner
  - Corrected padding calculations for consistent box drawing characters

## [2.1.1] - 2025-01-08

### Fixed
- **Arrow Key Navigation**: Fixed arrow keys showing escape sequences (^[[A^[[B) instead of navigating history
  - Removed custom commandHistory array that was disconnected from readline's native history
  - Now uses readline's built-in history mechanism (rl.history) for proper arrow key support
  - History command now correctly displays readline's internal history
  - Removed unnecessary keypress event emitter configuration

## [2.1.0] - 2025-01-08

### Added
- **Interactive Mode**: Launch with `kubently --api-url localhost:8080 --api-key test-api-key` for menu-driven interface
- **Admin Operations Menu**: Separate menu for cluster management operations
- **Custom A2A Path**: Support for `--a2a-path` flag to customize A2A endpoint
- **Improved History Support**: Better arrow key navigation in debug mode with history size and deduplication

### Changed
- **Simplified Debug Display**: Removed cluster ID and TTL from debug session header
- **Unified Port Usage**: Now connects to single port (8080) with automatic /a2a path append for debug mode
- **Natural Language Only**: Removed direct kubectl command references - all operations through agent

### Removed
- **exec Command**: Removed direct kubectl exec command - operations should go through agent
- **TTL Option**: Removed session TTL display and configuration

### Fixed
- **Arrow Key Navigation**: Fixed directional arrow keys in readline interface
- **History Management**: Added proper history support with deduplication

## [2.0.1] - 2025-01-07

### Fixed
- **Critical Fix**: Debug command now properly maintains interactive session
  - Fixed issue where debug session would exit after processing one command
  - Properly handles async readline events to keep session alive
  - Ensures prompt is always shown after command processing
  - Improved promise handling to prevent premature session termination

## [2.0.0] - 2025-01-07

### Added
- **Complete Node.js/TypeScript rewrite** of the Kubently CLI
- Beautiful, modern terminal UI with colored output and ASCII art banner
- Interactive prompts using Inquirer.js for better user experience
- Loading spinners (ora) for all async operations
- Formatted tables for cluster listings
- Rich text formatting with chalk for clear visual feedback
- Real-time interactive A2A debugging session with readline interface
- Comprehensive TypeScript type safety throughout the codebase
- Modular command structure using Commander.js
- Support for multiple output formats (k8s, docker, helm, script)
- Session history tracking in debug mode
- Better error handling and user feedback

### Features
- **Administrative Commands**:
  - `kubently init` - Interactive configuration setup
  - `kubently cluster add` - Register new clusters with multiple output formats
  - `kubently cluster list` - Beautiful table view of all clusters
  - `kubently cluster status` - Detailed cluster status display
  - `kubently cluster remove` - Safe cluster removal with confirmation
  - `kubently exec` - Execute single kubectl commands

- **A2A Debug Mode**:
  - `kubently debug` - Rich interactive terminal interface
  - Real-time message streaming
  - Command history support
  - Session management with TTL
  - Natural language query support
  - kubectl command execution

### Technical Improvements
- Modern ES2022+ JavaScript features
- Async/await throughout for better performance
- Proper error handling with detailed messages
- Environment variable support (KUBENTLY_API_URL, KUBENTLY_API_KEY)
- Secure credential storage in ~/.kubently/config.json
- Comprehensive manifest generation (K8s, Docker Compose, Helm, Shell scripts)

### Changed
- Migrated from Python to Node.js/TypeScript
- Replaced textual TUI with native readline interface
- Improved user experience with interactive prompts
- Enhanced visual feedback with colors and spinners

### Dependencies
- axios - HTTP client
- chalk - Terminal colors
- commander - CLI framework
- figlet - ASCII art
- inquirer - Interactive prompts
- ora - Terminal spinners
- uuid - UUID generation
- TypeScript - Type safety

## [1.0.0] - Previous Python Version
- Original Python implementation using Typer and Rich
- Basic A2A protocol support
- Admin API integration
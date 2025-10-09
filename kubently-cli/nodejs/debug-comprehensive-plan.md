# Comprehensive Plan to Fix Readline Hanging Issue

## Problem Statement
The Node.js CLI hangs after processing the first command. The second command is typed but never processed.

## Investigation Strategy

### 1. Create Minimal Reproduction
Create the simplest possible readline implementation that reproduces the issue.

### 2. Git History Analysis
- Find the last commit where this worked
- Diff the working vs broken versions
- Identify the exact change that broke it

### 3. Event Loop Analysis
- Log every event handler call
- Track the state of pendingOperation
- Monitor when processCommand is called
- Check if the 'line' event is even firing for the second input

### 4. Common Node.js Readline Issues
Based on the known Node.js bug (nodejs/node#42454), the issues are:
- Async operations in event handlers
- Promise interactions with readline
- Event loop becoming blocked
- State not resetting properly

## Solution Approaches (in order of likelihood)

### Approach 1: Event Handler Isolation
Ensure the 'line' event handler completes synchronously:
```typescript
rl.on('line', (line) => {
  // ALL sync operations here
  setImmediate(() => {
    // Async operations in next tick
    processCommand(line);
  });
});
```

### Approach 2: State Management Fix
The pendingOperation flag might not be getting reset:
```typescript
// Ensure pendingOperation is ALWAYS reset
finally {
  pendingOperation = false;
  if (!isClosing) {
    rl.prompt();
  }
}
```

### Approach 3: Readline Recreation
Some have fixed similar issues by recreating the readline interface:
```typescript
// After each command, recreate the interface
rl.close();
rl = createNewReadlineInterface();
```

### Approach 4: Simple Event-Driven Architecture
Remove ALL complexity and use pure events:
```typescript
// No promises, no async/await in critical path
function setupReadline() {
  const rl = readline.createInterface({...});
  
  rl.on('line', handleLine);
  rl.on('close', handleClose);
  
  function handleLine(line) {
    // Process asynchronously without blocking
    processInBackground(line);
    rl.prompt(); // Immediately show prompt
  }
}
```

### Approach 5: Use a Battle-Tested REPL Library
Consider using libraries that have already solved these issues:
- Node.js built-in `repl` module
- `inquirer` for prompts
- `vorpal` for CLI interfaces

## Implementation Plan

1. **Create test harness** - A script that can reliably reproduce the hang
2. **Add extensive logging** - Log every function call, event, and state change
3. **Try each approach** - Start with the simplest (Approach 1) and work up
4. **Verify with tests** - Ensure the fix works in all scenarios:
   - Direct terminal use
   - Piped input
   - Programmatic testing
   - Multiple rapid commands

## Success Criteria
- CLI processes unlimited commands without hanging
- No setTimeout/setInterval hacks needed
- Clean, understandable code
- Works in all Node.js environments
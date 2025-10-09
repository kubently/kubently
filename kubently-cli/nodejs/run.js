#!/usr/bin/env node

/**
 * Runner script for Kubently CLI that ensures proper TTY handling
 * Use this instead of npm scripts for interactive commands like debug
 */

// Check if we're in a TTY environment
if (!process.stdin.isTTY) {
  console.warn('Warning: Not running in a TTY environment. Arrow keys may not work properly.');
  console.warn('Try running directly with: node run.js [options]');
}

// Ensure stdin is in the right mode - but only if it's a TTY
if (process.stdin.isTTY && process.stdin.setRawMode) {
  // Don't set raw mode here - let readline handle it
  // process.stdin.setRawMode(true);
}

// Import and run the CLI
require('./dist/index.js');
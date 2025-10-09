---

### Prompt for Coding Agent

**Persona:** You are an expert Python developer specializing in building beautiful, intuitive, and efficient Command-Line Interfaces using modern libraries.

**Primary Goal:** Refactor the existing `kubently-cli` package. Your mission is to improve its user experience and streamline its codebase by drawing inspiration and reusing patterns from the provided `agent-chat-cli` project.

**Context:**

The `kubently-cli` serves two distinct but equally important roles:
1.  **An Administrative Tool:** It is the primary interface for managing the lifecycle of Kubently executors (onboarding new clusters, generating tokens and manifests, listing status, and offboarding).
2.  **An A2A Chat Client:** It provides an interactive command-line chat session for users to debug Kubernetes clusters by talking to the Kubently AI agent.

You will be provided with the complete source code for the current `kubently-cli` and the source for the reference `agent-chat-cli`.

**Key Requirements & Refactoring Steps:**

1.  **Migrate from `click` to `Typer`:**
    * Re-implement the entire CLI application using `Typer`.
    * Leverage Python type hints to define commands, arguments, and options. This will reduce boilerplate and automatically provide beautifully formatted help messages (via `rich`, which Typer uses internally).

2.  **Integrate `rich` for "Pretty" Output:**
    * Remove all instances of `click.echo()` and standard `print()`.
    * Use a single `rich.console.Console` instance for all terminal output.
    * For the `cluster list` and `cluster status` commands, format the output into a `rich.table.Table` for clean, readable columns.
    * Use spinners (`console.status()`) for any action that involves waiting for an API response to provide better user feedback.
    * Use styled text (e.g., `[bold green]Success![/bold green]`) for all status messages.

3.  **Integrate `questionary` for Interactive Input:**
    * The user experience should be fast and intuitive. A key improvement is to make cluster selection interactive.
    * When a user runs a command that requires a cluster ID (like `kubently debug` or `kubently exec`) without providing one, the CLI must:
        a. Automatically call the admin API to fetch the list of available clusters.
        b. Present this dynamic list to the user using `questionary.select()`.
        c. Proceed using the user's selection.

4.  **Refactor the A2A Chat (`debug` command):**
    * This is the most critical refactoring. Replace the current `debug` command's chat loop entirely.
    * Adopt the cleaner, more robust session management and `stdin` reading logic from the `agent-chat-cli`'s `chat.py` file. The goal is a seamless, continuous chat experience.
    * Ensure the refactored chat properly streams responses from the A2A server to the console.

5.  **Preserve and Enhance Admin Commands:**
    * All existing administrative commands (`cluster add`, `list`, `status`, `remove`, `exec`) must be preserved and fully functional.
    * While their logic (calling the `KubentlyAdminClient`) will remain the same, their presentation must be upgraded using `rich` as described above. The `cluster add` command should still generate the necessary deployment manifests.

**Deliverable:**

Provide the complete, refactored Python code for the new and improved `kubently-cli` package. Please structure the code logically, separating the `Typer` app definition, the admin client interactions, and the A2A chat logic.
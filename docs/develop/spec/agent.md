# Agent Development Guide

This file provides guidance to code agents when working with code in this repository.

## Project Overview
- **Python 3.11 intelligent agent framework** with event-driven, plugin-based architecture
- **Package manager**: `uv`
- **Testing**: `pytest` with `pytest-asyncio` for async tests
- **Project Layout**: This project uses `src-layout` structure, where the source code is located in the `src/` directory

## Quick Commands

### Development Setup
```bash
uv sync                    # Install dependencies
```

### Testing
```bash
uv run pytest tests/                    # Run all tests
uv run pytest tests/one_dragon_agent/core/agent/  # Run specific module
```

### Code Quality
```bash
uv run black src/ tests/     # Format code
uv run mypy src/             # Type checking
uv run flake8 src/           # Linting
```

## Agent Operating Principles
- **File encoding**: Always use UTF-8 format when writing files to ensure proper character encoding support.
- **Keep Test Synchronized**: After modifying any module, you **must** update its test file in `tests/`. Ensure that modified code is under coverage and all tests pass.
- **Keep Documentation Synchronized**: After modifying any module, you **must** update its corresponding documentation file in `docs/develop/modules/`. Ensure the documentation accurately reflects the changes.
- **Git Workflow**: Your responsibility is to write and modify the code based on the user's request. Do not perform any `git` operations like `commit` or `push`. The user will handle all version control actions.

## Documentation Guidelines
- **Markdown(.md) files**: Use text descriptions or mermaid diagrams, avoid embedding actual code
- **Mermaid diagrams**: Use standard flowchart/state diagram syntax, avoid loop constructs and "loop" as variable name
  - **Node Text Quoting**: Node text must be wrapped in double quotes (`"`) to avoid parsing errors. For example, use `I["User Interface (CLI)"]` instead of `I[User Interface (CLI)]`.

## Coding Style
- **Docstrings**: All functions must have Google-style docstrings, written in English.
- **Type Hinting**: All variables and function signatures must include type hints.
- **Built-in Generics**: Use built-in generic types (`list`, `dict`) instead of imports from the `typing` module (`List`, `Dict`).
  - **Correct**: `my_list: list[str] = []`
  - **Incorrect**: 
    ```python
    from typing import List
    my_list: List[str] = []
    ```
- **Explicit Parent Class Constructor Calls**:
    - **Rule**: In all subclass `__init__` methods, you **must** call the parent class constructor directly (e.g., `ParentClass.__init__(self, ...)`). You **must not** use `super().__init__()`.
    - **Reason**: This is a mandatory project-specific coding standard to ensure code clarity and explicitness.
    - **Examples**:
      ```python
      # CORRECT:
      class MySubclass(MyBaseClass):
          def __init__(self, name, value):
              # Directly call the parent's __init__
              MyBaseClass.__init__(self, name)
              self.value = value
      ```
      ```python
      # INCORRECT:
      class MySubclass(MyBaseClass):
          def __init__(self, name, value):
              # Do not use super() for initialization
              super().__init__(name)
              self.value = value
      ```

## Testing Guidelines
- **Test Classes and Fixtures**: Test files must use a test class (prefixed with `Test`) to organize related test methods. You must use `pytest.fixture` to manage test dependencies and state (such as object instance creation and teardown) to improve code reusability and maintainability.
- **Import Convention**: Because the project uses a `src-layout`, import paths in test files must not include the `src` directory.
  - **Correct**: `from one_dragon_agent.core.agent import Agent`
  - **Incorrect**: `from src.one_dragon_agent.core.agent import Agent`
- **Single-Method Test Files**: Each Python test file should focus exclusively on testing a single method across various scenarios.
  - **Example**: To test the `execute_main_loop` method, create a file named `test_execute_main_loop.py`. This file should contain all test cases specifically for the `execute_main_loop` method.
- **Test File Path Convention**: The directory path for a test file must mirror the package path of the module under test.
  - **Example**: For the class `Agent` in the module `one_dragon_agent.core.agent.agent`, its test files should be placed in the `tests/one_dragon_agent/core/agent/agent/` directory. The test file itself should be named after the specific method being tested (e.g., `test_execute_main_loop.py`).
- **Async Test Timeout**: All asynchronous test methods must include a timeout setting (e.g., using `pytest.mark.timeout(seconds)`) to prevent tests from hanging indefinitely.
- **Temporary File**: Use `.temp` directory under current working directory to store temp files.

---
name: wsl-runtime-ops
description: Use when operating the WSL-native Apex repo, including environment hygiene, backend boot, frontend build, and Ollama reachability.
---

# wsl-runtime-ops

## Use this draft when
- syncing the Windows repo into WSL
- booting backend/frontend from the WSL-native tree
- checking Ollama, env files, or cross-boundary connectivity

## Core workflow
- prefer the WSL-native repo for runtime validation
- normalize LF line endings for shell-used env files
- verify Ollama reachability and model pinning in-session
- keep runtime proof separate from doc-only work

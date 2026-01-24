# Tracer Agent - Project Overview for AI coding assistant


# Sensitive Data
- Never commit API keys or secrets

# Test Writing Approach:
- Write tests always as integration tests, never use mock services. 
- Write tests in the same file as the code they are testing or if the file is too large, create a new file with the same name as the code file but with _test.py suffix in the same directory. For example:
    - `src/agent/nodes/frame_problem/frame_problem.py` -> `src/agent/nodes/frame_problem/frame_problem_test.py`


# Best Practices
- Always run linters before committing 
- Always test your changes with make test
- Follow Go conventions: Use gofmt, follow project structure
- Check for security implications: Review security-sensitive changes carefully
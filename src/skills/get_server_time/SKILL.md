---
name: get_server_time
description: Use this skill when the user asks to find out the current local time on the server where the agent is running, along with the configured timezone.
---

# get_server_time

When you need to get the local server time and timezone, do not guess or write a complex script. Instead, use your `run_command` tool to execute the standard bash `date` command:

```bash
date
```

The output will contain the current local time and the timezone abbreviation (e.g., `MSK`, `UTC`, `PDT`).

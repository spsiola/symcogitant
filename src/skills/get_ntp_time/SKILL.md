---
name: get_ntp_time
description: Use this skill when the user asks to fetch the highly accurate current UTC time from an external NTP server (e.g., pool.ntp.org).
---

# get_ntp_time

When the user asks you to get the current time from an NTP server, use your `run_command` tool to execute the following Python one-liner. It creates a raw UDP socket to query `pool.ntp.org` on port 123 and unpacks the 32-bit timestamp:

```bash
python3 -c "import socket, struct, time; client=socket.socket(socket.AF_INET, socket.SOCK_DGRAM); client.settimeout(5.0); client.sendto(b'\x1b' + 47 * b'\0', ('pool.ntp.org', 123)); data, _ = client.recvfrom(1024); t = struct.unpack('!12I', data)[10] - 2208988800; print(f'NTP Time: {time.ctime(t)} UTC')"
```

Parse the output of this command and return it to the user.

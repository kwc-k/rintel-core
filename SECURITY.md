# Security policy

Rintel is local-first and binds to loopback by default. It is not an internet-facing multi-user service. Do not expose its port to an untrusted network or grant it access to repositories you do not intend it to read. Build and execution actions use server-owned profiles; browser input is not an arbitrary-shell authority.

For a suspected vulnerability, use GitHub's private vulnerability reporting for this repository if available. If that option is unavailable, open a public issue containing **only** a request for a private contact channel; do not publish exploit steps, private source, secrets, or affected-user details. Include the Rintel version, OS, impact, and a minimal reproduction privately. We will acknowledge and triage reports as maintainers are available; no fixed response-time SLA is promised.

Supported security-fix target: the latest tagged release on `main`. Historical experimental artifacts and untagged development snapshots are not supported release targets.

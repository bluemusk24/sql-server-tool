# AD/Kerberos authentication spike (issue #7)

**Status:** DEFER (spike not executable in this sandbox)
**Date:** 2026-10-09
**Result:** The experiment could **not** be executed. No domain-joined SQL
Server, AD domain, keytab, SPN, or Kerberos credentials are reachable from the
environment this spike ran in, so no connection attempt was possible. This
document records the honest blocker and the exact prerequisites + steps a future
run needs, per the acceptance criteria in issue #7.

## Outcome

**Blocker: environment cannot supply the required AD/Kerberos artifacts.**

Exact blocker text observed while validating prerequisites:

```
ls: cannot access '/etc/krb5.conf': No such file or directory
ls: cannot access '/etc/krb5.keytab': No such file or directory
ls: cannot access '/home/bluemusk/*.keytab': No such file or directory
odbcinst: SQLGetPrivateProfileString failed with Unable to find component name.
no reachable domain host / domain-joined SQL Server in this sandbox
```

- No Kerberos configuration (`/etc/krb5.conf`) and no default or user keytab
  (`KTNAME` not set).
- No ODBC Driver 18 registered with `odbcinst`; the driver is a hard prerequisite
  for the Kerberos `Trusted_Connection=Yes` / `Authentication=1` path.
- No domain controller or AD-joined SQL Server end point is reachable from this
  sandbox. Without a reachable domain-joined SQL Server, `kinit`/keytab steps can
  not even be exercised, and no SPN can be located.

Per the grooming contract ("if credentials or SPNs cannot be obtained in the
environment, record that as the blocker and recommend defer, do not fake a
pass"), the spike stops here. **No pass was faked and no connection claim is
made.**

## What the experiment would have proven

Whether the target Linux deployment environment can open a SQL Server
connection using Windows/AD integrated authentication via ODBC + Kerberos
(keytab + SPN) with **no SQL username/password**, and run `SELECT 1`.

## Prerequisites before a future run

These come from the acceptance criteria and from the ODBC Driver 18 + MIT
Kerberos documentation. A future run needs **all** of them:

### Target Linux environment setup

- Domain-joined Linux host (or host with Kerberos configured to reach the AD
  domain). We use the target deployment environment, not a developer box.
- MIT Kerberos client (`krb5-user` / `libkrb5-3`) with `/etc/krb5.conf`
  populated with the correct AD realm and KDC.
- Microsoft ODBC Driver 18 for SQL Server installed and registered:
  - `/opt/microsoft/msodbc/18/lib/libmsodbcsql-18.*.*.*.so` present;
  - verified via `odbcinst -q -d` (should list `ODBC Driver 18 for SQL Server`).
  - ODBC Driver 18 requires `SSL_VERIFY` on by default; ensure the target host's
    CA trust includes the SQL Server certificate, or the run fails on the TLS
    handshake before Kerberos is even attempted.
- Network path: target host must reach both the SQL Server TCP endpoint and an
  AD domain controller / KDC (UDP+TCP 88).

### `kinit` / keytab prerequisites

- A service/account in AD with an SPN registered for the SQL Server service
  (`MSSQLSvc/<fqdn>:<port>`; modern setups prefer `MSSQLSvc/<fqdn>`).
- A keytab file for the SQL Server SPN's AD account, placed on the target Linux
  host with restricted permissions (e.g. `chmod 600`), and `KTNAME=FILE:
  /path/to/sql.keytab` exported so `kinit` / the ODBC driver find it.
- Kerberos configuration that resolves the realm: `/etc/krb5.conf` realm/KDC
  entries, or a joined domain so `kinit -k @<REALM>` resolves automatically.
- Never commit the keytab or the principal names.

### SQL Server side

- SQL Server instance running with Kerberos available (Windows AD-joined server).
- SPN registered for the service account (registration is normally automatic for
  the SQL Server service account when the server is domain-joined, but must be
  verified).
- A Windows/AD login for the service principal with **read-only** rights
  sufficient for diagnostics, e.g.:
  - membership in a role granting `CONNECT` + `VIEW SERVER STATE` + `VIEW
    DEFINITION` (the diagnostics run read-only queries only), or an analogous
    least-privilege role;
  - no requirement to be `sysadmin` for the read-only spike.

### ODBC Driver 18 connection options (KRB5 / integrated auth)

Driver 18 supports two ways to request Kerberos auth from Linux:

1. `Trusted_Connection=Yes` (classic UnixODBC integrated-auth keyword) — or
2. `Authentication=1` (driver-18 Kerberos authentication keyword; value `ActiveDirectoryIntegrated`).

Recommended connection string for the spike:

```
Driver=ODBC Driver 18 for SQL Server;Server=<sql_fqdn>,<port>;Encrypt=yes;\
TrustServerCertificate=no;Trusted_Connection=yes
```

or, with the driver-18 `Authentication` keyword:

```
Driver=ODBC Driver 18 for SQL Server;Server=<sql_fqdn>,<port>;Encrypt=yes;\
TrustServerCertificate=no;Authentication=ActiveDirectoryIntegrated
```

`Trusted_Connection=yes` / `ActiveDirectoryIntegrated` must bypass the SQL
username/password path entirely — if a username/password is supplied, the run is
not evidence of integrated auth. The keytab must be in scope of the environment
the driver runs under.

## Exact steps a future run should take

1. Preflight environment: confirm `/etc/krb5.conf`, `klist` of the keytab
   (`klist -kt /path/to/sql.keytab`), realm/KDC reachability, `odbcinst -q -d`
   shows `ODBC Driver 18 for SQL Server`.
2. Acquire a TGT: `kinit -kt /path/to/sql.keytab <sql_principal>@<REALM>`;
   verify with `klist`.
3. Verify the SPN resolves for the target host: `kinit` the SPN-owning principal
   or confirm it via `kvno MSSQLSvc/<sql_fqdn>:<port>` (or
   `MSSQLSvc/<sql_fqdn>`).
4. Open the read-only connection with the connection string above (no SQL
   user/password) and run `SELECT 1`.
5. Record exact driver options used, keytab/SPN prerequisites satisfied, minimum
   SQL login permissions in place, any flakiness (e.g. time skew, DNS search
   suffix, failed `kvno` for the SPN), and the exact error text of the step that
   failed, if any.
6. Write `_docs/ad-auth-spike.md` with the evidence and end with **ship** or
   **defer**.

## Recommendation: DEFER for the POC

Recommendation is **defer** for the POC. This spike's acceptance criterion
requires proof against a real domain-joined SQL Server using a keytab + SPN with
no SQL credentials, and that environment is simply not available here: no
`/etc/krb5.conf`, no keytab, no registered ODBC Driver 18, and no reachable
domain host. plan.md §6 commits the MVP to "support both Windows/Active
Directory and SQL Server authentication for SQL Server connections", but the
deployment environment that this auth mode depends on — an organization-approved
cloud host with a secure network path to candidate SQL Servers — is still
"awaiting security/organizational decisions" per plan.md §10. Until a domain-
joined candidate host and AD credentials/SPN exist, any "ship" decision would be
an unproven claim, so AD integrated auth stays out of the POC scope (issue #28
remains blocked/deferred) and the SQL-auth path from #6 is the shippable
authentication mode. Reopen this spike when a domain-joined SQL Server and
keytab/SPN are reachable from the target Linux environment.

## Redaction note

No FQDNs, IPs, principal names, or keytab paths are included in this document;
placeholders (`<sql_fqdn>`, `<REALM>`, `/path/to/sql.keytab`) are used in their
place per plan.md §5.
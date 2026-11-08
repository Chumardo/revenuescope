# RevenueScope security and privacy review

Scope: the synthetic portfolio demo, its source and pinned dependencies, and the local publication boundary. This review covers the prepared project and Streamlit framework settings. A hosted deployment would need its own verification after approval.

## Data and runtime boundaries

| Surface | Review result |
| --- | --- |
| Public data | The DuckDB snapshot is generated from a fixed seed. Customer and order keys are synthetic identifiers. The schema stores revenue records and segment attributes and has no fields for customer names, emails, payment details, or addresses. The app labels the dataset **Synthetic demo** on every page. |
| File access | The Streamlit entrypoint resolves fixed artifact locations relative to the project. The DuckDB connection opens the snapshot read-only. Date, basis, and segment controls select data within that snapshot. Visitors cannot select server paths or execute SQL. |
| Evidence | Investigation JSON is parsed and checked against the selected ledger and locked scoring outputs. The Evaluation loader checks the development artifact hash and reconciles the pooled and per-seed statistics before returning display data. It renders summary metrics rather than the raw benchmark manifest. |
| Uploads | The app exposes no upload widget, import control, or path input. Streamlit 1.67 still registers its own session upload route. The configuration enables CORS and XSRF protection and sets `server.maxUploadSize = 0` to reject nonempty uploads. See [Streamlit upload configuration](https://docs.streamlit.io/develop/api-reference/configuration/config.toml#server). |
| Credentials | The demo needs an empty Secrets configuration. It uses no application credentials, remote databases, or third-party service calls at runtime. The package install requires access to its package index. |
| HTML and errors | The app's HTML masthead and stylesheet are local, static templates. Streamlit and Plotly render financial values and case metadata. The configuration disables static serving, usage statistics, and detailed client errors. |

A local Chrome probe against the clean extracted release found no uploader widget. A one-byte synthetic multipart PUT returned HTTP 413 with an active browser session and its valid XSRF token. The same request returned HTTP 400 with an invalid session and a valid token, and HTTP 403 with an active session and no token. The probe kept session identifiers and tokens out of its log.

## Git and publication boundary

The project directory sits inside a parent workspace repository whose refs and object store contain unrelated material. The public source should be built from a file allowlist in an independent checkout with fresh history. This removes parent refs, the object store, reflogs, configuration, and unrelated workspace files from the publication surface.

The project ignore rules cover environment files, `.streamlit/secrets.toml`, virtual environments, caches, and local generated directories. Those rules are convenience controls. The reviewed release allowlist determines which fixed synthetic runtime artifacts enter the release. Raw audit and replay outputs, verification manifests, temporary fixtures, and build outputs remain local unless the release manifest explicitly lists a required synthetic artifact. A local scan should inspect the actual publication archive, including image metadata and database schema, for the approved file set.

Bind developer preview servers to loopback. Streamlit includes its runtime script path in session metadata, so the hosted demo should start from the clean publication checkout rather than a developer workspace or a tunnel to a local server.

The publication archive has an explicit file allowlist and a SHA-256 manifest. The local release scan checks every archive member for recognizable secret signatures and developer path markers, checks the synthetic database against the approved logical digest, and inspects PNG metadata. Archive validation results are recorded with the local release handoff. This scope covers the files reviewed for publication and the local demo settings.

## Dependency review

The review queried each exact version's PyPI JSON `vulnerabilities` field on 2026-11-08. Requests contained only public package names and versions. The 37 runtime pins, including the Windows-specific marker, and four test pins returned empty advisory arrays. The runtime manifest contains the direct analytics and UI dependencies and their pinned transitive dependencies. The separate test manifest adds pytest and its test dependencies. `setuptools` is a build dependency. [PyPI JSON API](https://docs.pypi.org/api/json/)

The proposed `setuptools==80.9.0` build pin returned two distinct active advisories. One concerns source distribution exclusions under Unicode normalization. The other concerns path traversal in legacy wheel installation on Windows. The replacement `setuptools==84.2.1` satisfies both patched thresholds and returned an empty advisory array. [Source distribution advisory](https://github.com/advisories/GHSA-h35f-9h28-mq5c) · [Legacy Windows wheel advisory](https://github.com/advisories/GHSA-xmj3-9gf7-h52w) · [Replacement release metadata](https://pypi.org/pypi/setuptools/84.2.1/json)

PyPI lists a non-yanked compatible wheel for all 36 Linux-applicable runtime pins on x86-64 CPython 3.12 with glibc 2.31 or newer. This checks published package metadata. The first hosted build remains the end-to-end Linux installation check.

The advisory check covers what the selected index published for those versions at review time. Future releases should repeat it against the final lock. Framework routes, hosting limits, session behavior, and operating-system security remain relevant to a public deployment. The demo's fixed synthetic snapshot avoids risks associated with processing private financial data. A private operational adaptation would need its own access, logging, upload, and retention design.

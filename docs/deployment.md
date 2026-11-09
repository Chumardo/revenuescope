# RevenueScope deployment guide

The [public RevenueScope demo](https://revenuescope-demo.streamlit.app/) runs on Streamlit Community Cloud from the [published repository](https://github.com/Chumardo/revenuescope). It reads a bundled synthetic DuckDB snapshot without credentials. Use a fresh directory to check the contents and startup of a new release.

## Local release check

From a clean checkout, build and verify an archive before extracting it. Choose new paths if a previous check used these destinations.

```sh
python scripts/build_release.py build output/revenuescope-source.zip
python scripts/build_release.py verify output/revenuescope-source.zip
python -m zipfile -e output/revenuescope-source.zip output/release-check
cd output/release-check
```

The verifier checks the archive allowlist, each embedded SHA-256 entry, and the bundled asset hashes. The root `release-manifest.sha256` tracked by Git records the original archive from `57170be`. The manifest inside each new ZIP describes that archive, including the current README and MIT license. Two builds with the same source bytes and Python and zlib versions produce the same ZIP.

1. Create and activate a fresh Python 3.12 virtual environment. On Windows, use a short absolute path for the environment if the extraction path is deeply nested.
2. Run `python -m pip install -r requirements.txt`, then `python -m pip check` and `python -m streamlit run demo_app.py --server.address 127.0.0.1`.
3. Open all four pages. Switch revenue bases, open a flagged date, and confirm the detail retains the selected basis. Check for the synthetic-data label and the absence of upload controls.
4. Confirm 731 posted dates, **$4,461,841.22** in posted revenue, and **31** posted flags. Evaluation should show **7 of 15** eligible events detected and **7 of 88** alert episodes matched (**7.95%** event precision).

The prepared snapshot avoids generating data or replaying the benchmark at startup. `demo_assets/` contains the synthetic database, posted and retrospective investigation evidence, the development lock and held-out display summary, and provenance. The packaged evidence retains aggregate metrics and the locked policy while omitting the detailed benchmark answer key and exploratory scans. `scripts/build_public_assets.py` prepares that allowlisted set from reviewed development inputs. Run the generation and benchmark commands separately when verifying reproducibility.

## Hosted configuration

| Setting | Value |
| --- | --- |
| GitHub repository | `Chumardo/revenuescope` |
| Default branch | `main` |
| Visibility | Public synthetic demo |
| License | [MIT](../LICENSE) |
| Streamlit entrypoint | `demo_app.py` at repository root |
| Dependency file | `requirements.txt` at repository root |
| Streamlit settings | Use the release-root `.streamlit/config.toml`. Supply loopback address only for local testing. |
| Python version | 3.12 in Advanced settings |
| Secrets | Leave blank |
| App subdomain | `revenuescope-demo.streamlit.app` |

Community Cloud starts from the selected repository and branch, installs dependencies in a fresh Python environment, and runs the specified entrypoint from the repository root. Keep the approved `demo_assets/` files in that tree. The source workspace's shared Git history, local output directories, and virtual environment stay outside the published repository. [File organization](https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/file-organization) · [Dependency installation](https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/app-dependencies) · [Deployment settings](https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/deploy)

## Hosted smoke test

After an approved deployment, record the Git commit, inspect the build and startup logs, and repeat the local checks on the hosted URL at desktop and mobile widths. Keep the matching archive and manifest with the release record.

Community Cloud can sleep after inactivity. Local smoke tests cover the package and application startup. Hosted concurrency and transport-level upload rejection require separate verification.

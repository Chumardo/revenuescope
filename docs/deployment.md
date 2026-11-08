# RevenueScope deployment guide

This guide describes the proposed setup for the approved release snapshot. The app is a read-only synthetic demo with local DuckDB files and no credentials. Keep the first launch independent of the source workspace and its generated development reports.

## Local release check

1. Unpack the release snapshot into a new directory. Verify the supplied SHA-256 manifest before installing dependencies.
2. Create a fresh Python 3.12 virtual environment. Install `requirements.txt` and run `python -m streamlit run demo_app.py --server.address 127.0.0.1` from the release root.
3. Open all four pages, switch between revenue bases, open a flagged date, and confirm the detail retains the selected basis. Confirm the visible synthetic-data label and that the interface offers no upload control.
4. Confirm 731 posted dates, the **$4,461,841.22** posted total, **31** posted flags, and Evaluation's **7 of 15** eligible events and **7.95%** posted-day event precision. Run the same checks on the actual hosted URL after an approved deployment.

The prepared snapshot avoids generating data or replaying the benchmark at startup. `demo_assets/` contains the synthetic database, posted and retrospective investigation evidence, the development lock and held-out display summary, and provenance. The packaged evidence retains aggregate metrics and the locked policy while omitting the detailed benchmark answer key and exploratory scans. `scripts/build_public_assets.py` prepares that allowlisted set from reviewed development inputs. Run the generation and benchmark commands separately when verifying reproducibility.

## Proposed publication configuration

| Setting | Proposed value |
| --- | --- |
| GitHub repository | A new standalone `revenuescope` repository under the owner's approved account |
| Default branch | `main` |
| Visibility | Public after approval and manifest review |
| Initial history | A fresh commit from the allowlisted release tree with approved author metadata |
| License | MIT recommended. Confirm the license, copyright holder, and year before adding `LICENSE`. |
| Streamlit entrypoint | `demo_app.py` at repository root |
| Dependency file | `requirements.txt` at repository root |
| Streamlit settings | Use the release-root `.streamlit/config.toml`. Supply loopback address only for local testing. |
| Python version | 3.12 in Advanced settings |
| Secrets | Leave blank |
| App subdomain | Select an approved available name after publication approval |

Community Cloud starts from the selected repository and branch, installs dependencies in a fresh Python environment, and runs the specified entrypoint from the repository root. Keep the approved `demo_assets/` files in that tree. The source workspace's shared Git history, local output directories, and virtual environment stay outside the published repository. [File organization](https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/file-organization) · [Dependency installation](https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/app-dependencies) · [Deployment settings](https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/deploy)

## After approval

Create the standalone repository from the reviewed snapshot. Verify the published tree against the manifest and use that reviewed commit for the initial deployment. In Community Cloud, select the repository, `main`, and `demo_app.py`. Set Python 3.12, leave Secrets blank, and inspect the build and startup logs. Verify the four pages, navigation, benchmark disclosure, and lack of upload controls from the hosted URL at desktop and mobile widths. Record the hosted startup result before sharing a demo link.

Hosted build success, cold-start behavior, and concurrency require verification on the chosen host. Local smoke evidence covers the packaged files and local environment.

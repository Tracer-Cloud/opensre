# OpenSRE Docs (Mintlify)

This directory contains the Mintlify documentation site for OpenSRE.

## What lives here

- Product and platform docs rendered by Mintlify
- Setup and onboarding guidance for docs contributors
- Integrations, environments, investigations, and testing-oriented guides
- Docs configuration and navigation in `docs.json`

## Local development

Prerequisites:

- Node.js 20+
- npm

Install dependencies from the docs directory:

```bash
npm install
```

Start local docs preview:

```bash
npm run dev
```

The script runs Mintlify via `npx`, so no global `mint` install is required. Mintlify serves the docs locally (usually on `http://localhost:3000`).

## Safe docs update workflow

1. Create or update `.mdx` content in `docs/`.
2. If you add a new page, register it in `docs/docs.json` navigation.
3. Run local validation:

```bash
npm run check
```

4. Start preview and click through changed pages:

```bash
npm run dev
```

5. Open a PR with:
   - validation evidence (`npm run check` output)
   - a short screen recording of local docs flow or published result

## Source-of-truth and sync guidance

- `docs/` is the source of truth for the Mintlify docs site.
- Root repo docs such as `README.md`, `CONTRIBUTING.md`, and `SETUP.md` remain the source of truth for repository-level contribution and setup policy.
- If a change impacts both product behavior and contributor experience, update both the relevant root doc and the Mintlify page in the same PR.

## CI and publishing model

- GitHub Actions enforces validation for docs changes (`docs-ci` workflow).
- Mintlify GitHub App handles publishing after changes land on the default branch.
- PRs without passing docs validation and proof artifacts should not be merged.

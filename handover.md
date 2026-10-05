
# Workshop Program Card Generator  
## Technical Handover & System Overview

This repository implements a low-traffic web service that allows users to upload custom SVG artwork and receive a ready-to-manufacture PCB Gerber ZIP, with that artwork merged onto the **front solder mask** layer of a fixed PCB design.

The system is deliberately simple, deterministic, and conservative.  
Artwork is **non-electrical**, and SVGs must already conform to strict rules.

This document describes the **current, working architecture**, not earlier experiments.

---

## What the system does (in one sentence)

> Takes 1 or 4 user-supplied SVGs, places them at fixed positions on a known PCB solder-mask layer, generates a proof image, and returns a zipped Gerber set.

---

## High-level Architecture

The system has four components:

1. **Static frontend** (Cloudflare Pages)  
2. **API layer** (Cloudflare Pages Functions)  
3. **Job runner** (GitHub Actions)  
4. **Storage** (Cloudflare R2 + KV)

Crucially:
- **All heavy work happens in GitHub Actions**
- Workers/Functions only orchestrate jobs and serve files

---

## Live Deployment

- Public site + API:  
  **https://pcg.musicthing.co.uk**

- Cloudflare R2 bucket (internal):  
  `pcb-art-jobs`

---

## Repository Layout (authoritative)

```

Workshop-Program-Card-Generator/
├─ pages/
│  └─ index.html                  # Static UI
│
├─ functions/                     # Cloudflare Pages Functions
│  └─ api/
│     ├─ job.ts                   # POST /api/job
│     └─ job/
│        ├─ [id].ts               # GET /api/job/:id
│        └─ [id]/
│           ├─ update.ts          # POST /api/job/:id/update
│           ├─ proof.png.ts       # GET proof image
│           └─ bundle.zip.ts      # GET Gerber ZIP
│
├─ tools/
│  └─ append_svg_to_mask_gts.py   # Core SVG → Gerber logic
│
├─ templates/
│  └─ shell/
│     ├─ Gerbers/                # Fixed PCB shell (committed)
│     │  ├─ 4-up-blank-F_Mask.gts
│     │  ├─ 4-up-blank-F_Cu.gtl
│     │  └─ ...
│     ├─ bom.csv
│     ├─ positions.csv
│     └─ readme.md
│
├─ .github/
│  └─ workflows/
│     ├─ r2_smoketest.yml
│     └─ build_job.yml            # Main job runner
│
└─ handover.md

```

---

## Cloudflare Pages configuration (IMPORTANT)

This project **must** be configured as follows:

- **Root directory:** repository root  
- **Build command:** none  
- **Build output directory:** none / empty  

Reason:
- Pages Functions are only detected when `functions/` exists at repo root.
- If the root directory is set to `pages/`, Functions are silently ignored and `/api/*` routes fall through to static files.

This configuration is now confirmed working.

---

## Data Flow

### 1. Job creation (frontend → API)

- User uploads **exactly 1 or 4 SVG files**
- Frontend sends `POST /api/job` with multipart form data
- API function:
  - Validates file count
  - Generates a `job_id`
  - Stores SVGs in R2 under:

```

jobs/<job_id>/in/slot1.svg
jobs/<job_id>/in/slot2.svg
jobs/<job_id>/in/slot3.svg
jobs/<job_id>/in/slot4.svg

```

- If only one SVG is uploaded, it is duplicated into all four slots
- Job metadata is written to KV
- A GitHub Actions workflow is dispatched

---

### 2. Job execution (GitHub Actions)

The `build_job.yml` workflow:

1. Checks out this repository
2. Copies fixed Gerbers from:
```

templates/shell/Gerbers/

````
3. Downloads SVG inputs from R2
4. Runs the Python tool:

```bash
python tools/append_svg_to_mask_gts.py \
--base-gts work/gerbers/4-up-blank-F_Mask.gts \
--out-gts  work/gerbers/4-up-blank-F_Mask.gts \
--svg slot1.svg --place 62.93,30.4  --rotate 0 \
--svg slot2.svg --place 62.93,43.4  --rotate 0 \
--svg slot3.svg --place 86.65,30.45 --rotate 180 \
--svg slot4.svg --place 86.67,43.45 --rotate 180
````

5. Generates a PNG proof
6. Zips the full Gerber set
7. Uploads outputs to R2:

```
jobs/<job_id>/out/bundle.zip
jobs/<job_id>/out/proof.png
```

8. Calls:

```
POST /api/job/<job_id>/update
```

to mark the job as complete

---

### 3. Polling & download (frontend)

* Frontend polls `GET /api/job/:id`
* When status becomes `done`:

  * Proof image available at `/api/job/:id/proof.png`
  * Gerber ZIP available at `/api/job/:id/bundle.zip`

---

## R2 Storage Layout

```
pcb-art-jobs/
└─ jobs/
   └─ <job_id>/
      ├─ in/
      │  ├─ slot1.svg
      │  ├─ slot2.svg
      │  ├─ slot3.svg
      │  └─ slot4.svg
      └─ out/
         ├─ proof.png
         └─ bundle.zip
```

Jobs are fully isolated and safe to run concurrently.

---

## SVG Input Requirements (strict)

SVGs **must**:

* Have a valid `viewBox`
* Declare a physical size or use the workflow's fixed 19 × 11 mm fallback
* Use visible filled vector shapes (paths and SVG basic shapes are supported)
* Convert text and strokes to outlined filled shapes
* Assume usage as **solder-mask openings only**

The converter applies nested transforms, inherited visibility/paint properties,
simple embedded CSS classes, `preserveAspectRatio`, and `evenodd`/`nonzero` fill
rules. Geometry outside the SVG canvas is clipped like it is in a normal SVG
renderer. All visible artwork is included regardless of layer or group names.
Reference outlines and guide layers must be hidden or deleted before export.
SVG features that cannot be translated faithfully fail
with a human-readable correction. The production workflow also requires the
resolved physical size to be exactly 19 × 11 mm.

---

## Core Python Tool

### `append_svg_to_mask_gts.py`

Responsibilities:

* Parse SVG paths and basic shapes correctly
* Respect nested transforms, `viewBox`, physical units and aspect-ratio mapping
* Ignore non-rendered geometry (`display:none`, hidden visibility, zero opacity)
* Handle SVG ↔ Gerber Y-axis inversion
* Preserve compound-path holes using the effective SVG fill rule
* Reject artwork that escapes the SVG canvas
* Convert paths to Gerber regions
* Append regions into an existing `.gts` file
* Support:

  * Multiple SVGs
  * Arbitrary placement
  * Rotation about SVG centre

Explicitly **not supported**:

* Fonts or text rendering
* Boolean path operations
* Stroke expansion
* CSS stylesheets or CSS transforms
* `<use>`, nested SVG viewports, clipping, masks or filters
* Raster images or foreign content

Unsupported features fail with an actionable error so malformed output is not
mistaken for a successful build.

---

## Secrets & Bindings

### Cloudflare

* **R2 bucket binding:** `JOBS_BUCKET`
* **KV namespace binding:** `JOBS_KV`

### Secrets

* `GITHUB_TOKEN` (fine-grained PAT)
* `GITHUB_OWNER`
* `GITHUB_REPO`
* `GITHUB_WORKFLOW_FILE`
* `RUNNER_SHARED_SECRET`

---

## GitHub Token Permissions (minimal)

Fine-grained PAT:

* Repository access:
  `Workshop-Program-Card-Generator`
* Permissions:

  * **Actions: Read & write**
  * Metadata: Read (required)
  * Contents: No access
  * Workflows: No access

This token can dispatch workflows but cannot modify the repo.

---

## Concurrency & Cost Model

* Jobs are isolated by ID
* GitHub Actions handles concurrency safely
* Expected load: ~1 job/hour
* Costs:

  * Pages + Functions: effectively free
  * R2 storage/bandwidth: negligible
  * GitHub Actions: well within free tier

---

## Known Sharp Edges

* Pages root directory misconfiguration silently disables Functions
* Browser preflight catches common authoring problems, while the Python
  converter remains the authoritative validator and reports its exact error
  back to the page
* Proof rendering is functional, not polished
* No automatic cleanup of old jobs (yet)

---

## Design Philosophy

* Deterministic
* Boring
* Manufacturing-first
* No “magic”
* No UI-side creativity

This is a **manufacturing pipeline**, not a graphics editor.

```

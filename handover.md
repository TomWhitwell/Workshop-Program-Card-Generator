
# Workshop Program Card Generator

**Handover / Architecture Notes**

This project provides a very low-traffic web service that allows users to upload custom SVG artwork and receive a ready-to-manufacture PCB Gerber ZIP, with the artwork merged onto the **front solder mask** layer of a fixed PCB design.

The system is intentionally simple, deterministic, and robust. There is **no electrical relevance** to the artwork; users must adhere to fixed SVG rules.

---

## High-level Architecture

The system is split into three responsibilities:

1. **Static UI (Cloudflare Pages)**

   * Upload 1 or 4 SVG files
   * Poll job status
   * Display PNG proof
   * Download final ZIP

2. **Job Orchestration API (Cloudflare Worker)**

   * Accept uploads
   * Store inputs in R2
   * Create a `job_id`
   * Trigger GitHub Actions runner
   * Track job status

3. **Job Runner (GitHub Actions)**

   * Fetch SVGs from R2
   * Merge artwork into Gerbers using Python
   * Generate proof image
   * Bundle output ZIP
   * Upload results back to R2

All heavy computation happens in **GitHub Actions**, not Workers.

---

## Repository Structure (current)

```
Workshop-Program-Card-Generator/
├─ tools/
│  └─ append_svg_to_mask_gts.py        # Core SVG → Gerber logic
│
├─ templates/
│  └─ shell/
│     └─ Gerbers/                      # Fixed PCB shell (committed)
│        ├─ 4-up-blank-F_Mask.gts
│        ├─ 4-up-blank-F_Cu.gtl
│        ├─ ...
│
├─ .github/
│  └─ workflows/
│     ├─ r2_smoketest.yml              # Verifies R2 credentials
│     └─ build_job.yml                 # Main job runner
│
└─ (future)
   ├─ worker/                          # Cloudflare Worker API
   └─ pages/                           # Static upload UI
```

Key principle: **the shell Gerbers live in the repo** so the runner is deterministic and doesn’t depend on external templates.

---

## Data Flow

### 1. Job creation

* User uploads SVG(s) via the UI
* Worker generates a unique `job_id`
* Worker writes inputs to R2:

```
jobs/<job_id>/in/slot1.svg
jobs/<job_id>/in/slot2.svg
jobs/<job_id>/in/slot3.svg
jobs/<job_id>/in/slot4.svg
```

(If only one SVG is uploaded, it is duplicated across slots.)

### 2. Job execution

* Worker triggers GitHub Action via `workflow_dispatch`, passing `job_id`
* GitHub Action:

  1. Copies `templates/shell/Gerbers/` → `work/gerbers/`
  2. Downloads SVG inputs from R2
  3. Runs `append_svg_to_mask_gts.py` to modify:

     ```
     work/gerbers/4-up-blank-F_Mask.gts
     ```
  4. Generates a proof PNG
  5. Zips the Gerbers

### 3. Job output

Results are written back to R2:

```
jobs/<job_id>/out/bundle.zip
jobs/<job_id>/out/proof.png
```

Worker updates job status so the UI can present download links.

---

## GitHub Actions: `build_job.yml`

Core responsibilities:

* Python + awscli installed via `pip` (no apt)
* Uses R2 via S3-compatible API
* Uses **fixed placement coordinates + rotations**

Current “golden” placement example:

```bash
python tools/append_svg_to_mask_gts.py \
  --base-gts work/gerbers/4-up-blank-F_Mask.gts \
  --out-gts  work/gerbers/4-up-blank-F_Mask.gts \
  --svg slot1.svg --place 62.93,30.4  --rotate 0 \
  --svg slot2.svg --place 62.93,43.4  --rotate 0 \
  --svg slot3.svg --place 86.65,30.45 --rotate 180 \
  --svg slot4.svg --place 86.67,43.45 --rotate 180
```

The mask file is modified **in place**.

---

## Core Python Tool: `append_svg_to_mask_gts.py`

### Responsibilities

* Parse SVG paths
* Correctly handle:

  * viewBox
  * mm scaling
  * Y-axis inversion (SVG vs Gerber)
  * fill rules (nonzero / evenodd)
  * nested shapes (holes)
* Convert paths to Gerber regions
* Append artwork into an existing `.gts` file
* Support:

  * multiple SVGs
  * multiple placements
  * per-SVG rotation about centre

### Explicitly *not* supported

* Fonts
* Text rendering
* Boolean ops
* Error recovery
* Arbitrary scaling or snapping

SVGs must be **pre-prepared** in Illustrator/Inkscape.

---

## SVG Input Requirements (important)

Users must supply SVGs that:

* Have explicit physical dimensions (e.g. `width="19mm" height="11mm"`)
* Use paths only (no text)
* Are filled shapes (not strokes)
* Use consistent winding
* Assume artwork will be used **only as solder mask opening**

The script deliberately assumes correctness — no validation is performed.

---

## R2 Storage Layout

Single bucket: `pcb-art-jobs`

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
         ├─ bundle.zip
         └─ proof.png
```

This design guarantees:

* Perfect isolation between jobs
* Safe concurrency
* Easy cleanup

---

## Concurrency & Safety

* Each job has a unique prefix → no collisions
* GitHub Actions handles concurrent jobs natively
* R2 is effectively infinite for this scale
* Traffic is assumed to be **very low** (≈ 1 job/hour max)

No locking, queues, or rate limiting required.

---

## Cost Model

* Cloudflare Pages: free
* Cloudflare Workers: pennies/month at this scale
* R2: negligible (tiny files, low traffic)
* GitHub Actions: free tier sufficient

---

## Known “Sharp Edges”

* YAML `on:` syntax in this repo **only reliably works** with:

  ```yaml
  on: [workflow_dispatch]
  ```

  (multi-line mapping caused parsing errors here)
* Proof generation is currently a placeholder
* SVG correctness is assumed, not enforced

---

## Obvious Next Steps

1. **Worker API**

   * `POST /api/job`
   * `GET /api/job/:id`
   * `POST /api/job/:id/update`

2. **Proof rendering**

   * Likely approach:

     * Generate a simple “proof SVG”
     * Rasterize using `resvg` in Actions

3. **Static UI**

   * Plain HTML + JS
   * No framework required

4. **Optional**

   * Auto-cleanup old jobs in R2
   * Add job expiry timestamps

---

## Design Philosophy (intentional)

* Deterministic
* Boring
* Minimal
* No magic
* No clever heuristics

This is a **manufacturing pipeline**, not a graphics app.

---

If you want, next I can:

* Turn this into a proper `HANDOVER.md` file ready to commit
* Sketch the Worker API in TypeScript
* Design the upload UI HTML
* Or freeze the current state as a tagged release

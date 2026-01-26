export interface Env {
  JOBS_BUCKET: R2Bucket;
  JOBS_KV: KVNamespace;

  GITHUB_TOKEN: string;
  GITHUB_OWNER: string;
  GITHUB_REPO: string;
  GITHUB_WORKFLOW_FILE: string;

  RUNNER_SHARED_SECRET: string;
}

function json(data: unknown, status = 200) {
  return new Response(JSON.stringify(data), {
    status,
    headers: { "content-type": "application/json" },
  });
}

function uuid() {
  // Good enough for job ids in this context.
  return crypto.randomUUID();
}

async function dispatchGithubWorkflow(env: Env, jobId: string) {
  const url =
    `https://api.github.com/repos/${env.GITHUB_OWNER}/${env.GITHUB_REPO}` +
    `/actions/workflows/${env.GITHUB_WORKFLOW_FILE}/dispatches`;

  const res = await fetch(url, {
    method: "POST",
    headers: {
      "authorization": `Bearer ${env.GITHUB_TOKEN}`,
      "accept": "application/vnd.github+json",
      "content-type": "application/json",
      "user-agent": "pcg-musicthing",
    },
    body: JSON.stringify({
      ref: "main",
      inputs: { job_id: jobId },
    }),
  });

  if (!res.ok) {
    const t = await res.text();
    throw new Error(`GitHub dispatch failed: ${res.status} ${t}`);
  }
}

export const onRequestPost: PagesFunction<Env> = async (ctx) => {
  const req = ctx.request;
  const env = ctx.env;

  const ct = req.headers.get("content-type") || "";
  if (!ct.toLowerCase().includes("multipart/form-data")) {
    return json({ error: "Expected multipart/form-data" }, 400);
  }

  const form = await req.formData();
  const files = form.getAll("svgs").filter((v) => v instanceof File) as File[];

  if (!(files.length === 1 || files.length === 4)) {
    return json({ error: "Upload exactly 1 or 4 SVG files." }, 400);
  }

  // Basic sanity: size + type-ish check
  for (const f of files) {
    if (f.size > 5_000_000) return json({ error: "SVG too large (max 5MB each)." }, 400);
    const name = (f.name || "").toLowerCase();
    if (!name.endsWith(".svg")) return json({ error: "All files must be .svg" }, 400);
  }

  const jobId = uuid();

  // Normalize to 4 slots (replicate if only 1)
  const slotFiles: File[] =
    files.length === 1 ? [files[0], files[0], files[0], files[0]] : files;

  // Store inputs to R2
  await Promise.all(
    slotFiles.map(async (f, i) => {
      const key = `jobs/${jobId}/in/slot${i + 1}.svg`;
      await env.JOBS_BUCKET.put(key, f.stream(), {
        httpMetadata: { contentType: "image/svg+xml" },
      });
    })
  );

  // Record initial job status
  await env.JOBS_KV.put(
    `job:${jobId}`,
    JSON.stringify({
      jobId,
      status: "queued",
      createdAt: new Date().toISOString(),
      error: null,
    }),
    { expirationTtl: 60 * 60 * 24 } // 24h
  );

  // Dispatch GitHub runner
  try {
    await env.JOBS_KV.put(`job:${jobId}`, JSON.stringify({
      jobId,
      status: "running",
      createdAt: new Date().toISOString(),
      error: null,
    }), { expirationTtl: 60 * 60 * 24 });

    await dispatchGithubWorkflow(env, jobId);
  } catch (e: any) {
    await env.JOBS_KV.put(`job:${jobId}`, JSON.stringify({
      jobId,
      status: "error",
      createdAt: new Date().toISOString(),
      error: String(e?.message || e),
    }), { expirationTtl: 60 * 60 * 24 });

    return json({ jobId, status: "error", error: String(e?.message || e) }, 500);
  }

  return json({ jobId, status: "running" }, 200);
};

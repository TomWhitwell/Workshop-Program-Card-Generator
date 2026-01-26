export interface Env { JOBS_KV: KVNamespace; }

function json(data: unknown, status = 200) {
  return new Response(JSON.stringify(data), {
    status,
    headers: { "content-type": "application/json" },
  });
}

export const onRequestGet: PagesFunction<Env> = async (ctx) => {
  const id = ctx.params.id as string;
  const raw = await ctx.env.JOBS_KV.get(`job:${id}`);
  if (!raw) return json({ error: "Not found" }, 404);

  const job = JSON.parse(raw);

  // convenience URLs (same origin)
  if (job.status === "done") {
    job.proofUrl = `/api/job/${id}/proof.png`;
    job.zipUrl = `/api/job/${id}/bundle.zip`;
  }
  return json(job);
};

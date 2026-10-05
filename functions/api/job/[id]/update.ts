export interface Env {
  JOBS_KV: KVNamespace;
  RUNNER_SHARED_SECRET: string;
}

function json(data: unknown, status = 200) {
  return new Response(JSON.stringify(data), {
    status,
    headers: { "content-type": "application/json" },
  });
}

export const onRequestPost: PagesFunction<Env> = async (ctx) => {
  const id = ctx.params.id as string;

  const secret = ctx.request.headers.get("x-runner-secret") || "";
  if (secret !== ctx.env.RUNNER_SHARED_SECRET) {
    return json({ error: "Forbidden" }, 403);
  }

  const body = await ctx.request.json().catch(() => null) as any;
  if (!body || (body.status !== "done" && body.status !== "error")) {
    return json({ error: "Invalid body" }, 400);
  }

  const record = {
    jobId: id,
    status: body.status,
    createdAt: body.createdAt || new Date().toISOString(),
    finishedAt: new Date().toISOString(),
    error: body.error || null,
    detailsUrl: typeof body.detailsUrl === "string" ? body.detailsUrl : null,
  };

  await ctx.env.JOBS_KV.put(`job:${id}`, JSON.stringify(record), {
    expirationTtl: 60 * 60 * 24,
  });

  return json({ ok: true });
};

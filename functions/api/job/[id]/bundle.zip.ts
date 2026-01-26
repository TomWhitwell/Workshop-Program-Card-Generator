export interface Env { JOBS_BUCKET: R2Bucket; }

export const onRequestGet: PagesFunction<Env> = async (ctx) => {
  const id = ctx.params.id as string;
  const key = `jobs/${id}/out/bundle.zip`;

  const obj = await ctx.env.JOBS_BUCKET.get(key);
  if (!obj) return new Response("Not found", { status: 404 });

  const headers = new Headers();
  obj.writeHttpMetadata(headers);
  headers.set("content-type", "application/zip");
  headers.set("content-disposition", `attachment; filename="pcg-${id}.zip"`);
  headers.set("cache-control", "no-store");

  return new Response(obj.body, { headers });
};

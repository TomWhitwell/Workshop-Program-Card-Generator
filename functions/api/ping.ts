export const onRequestGet: PagesFunction = async () => {
  return new Response("pong", { headers: { "content-type": "text/plain" } });
};

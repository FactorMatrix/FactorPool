export async function api(path, body) {
  const response = await fetch(
    `/api${path}`,
    body === undefined
      ? {}
      : {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        },
  );
  let data;
  try { data = await response.json(); }
  catch { throw new Error(response.ok ? "服务返回的数据无法解析，请重试" : `请求失败 ${response.status}`); }
  if (!response.ok)
    throw new Error(data.error || `请求失败 ${response.status}`);
  return data;
}

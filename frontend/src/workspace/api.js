export async function read(action, params = {}, signal) {
  const query = new URLSearchParams(params);
  const response = await fetch(`/api/${action}${query.size ? `?${query}` : ''}`, {signal, headers:{Accept:'application/json'}});
  let value;
  try { value = await response.json(); } catch { throw new Error(`读取失败（HTTP ${response.status}）`); }
  if (!response.ok) {
    const error = new Error(response.status === 403 ? '访问已撤回或无权读取' : response.status === 404 ? '对象不存在或已删除' : value.error || `读取失败（HTTP ${response.status}）`);
    error.status = response.status;
    throw error;
  }
  return value;
}

export async function write(action, body, params = {}) {
  const query = new URLSearchParams(params);
  const response = await fetch(`/api/${action}${query.size ? `?${query}` : ''}`, {
    method:'POST',
    headers: body instanceof File ? {'Content-Type':body.type || 'application/octet-stream'} : {'Content-Type':'application/json'},
    body: body instanceof File ? body : JSON.stringify(body),
  });
  let value;
  try { value = await response.json(); } catch { throw new Error(`请求失败（HTTP ${response.status}）`); }
  if (!response.ok) {
    const error = new Error(response.status === 403 ? '访问已撤回或无权操作' : response.status === 409 ? `版本或输入冲突：${value.error || '请刷新后重试'}` : value.error || `请求失败（HTTP ${response.status}）`);
    error.status = response.status;
    throw error;
  }
  return value;
}

export function sameRef(a,b) {
  return a?.resource_id === b?.resource_id && a?.snapshot_id === b?.snapshot_id && a?.parse_id === b?.parse_id;
}
export function refKey(ref) { return `${ref.resource_id}:${ref.snapshot_id}:${ref.parse_id}`; }

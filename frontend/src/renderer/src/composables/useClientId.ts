/* 会话 client_id：localStorage 持久（服务端会话 dict 的钥匙，来自 main.js 原逻辑） */
const CLIENT_KEY = 'herb_agent_client_id'

export function useClientId(): string {
  let id = localStorage.getItem(CLIENT_KEY)
  if (!id) {
    id =
      (crypto.randomUUID && crypto.randomUUID()) ||
      'c' + Date.now() + Math.random().toString(16).slice(2)
    localStorage.setItem(CLIENT_KEY, id)
  }
  return id
}

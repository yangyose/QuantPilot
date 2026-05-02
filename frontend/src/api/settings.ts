import client from './client'
import type { ImportResponse, UserConfigHistory, UserConfigItem } from '@/types/api'

export async function getSettings(): Promise<UserConfigItem[]> {
  const res = await client.get('/api/v1/settings')
  return res.data.data as UserConfigItem[]
}

export async function putSetting(
  config_key: string,
  config_value: Record<string, unknown>,
  change_note?: string,
): Promise<void> {
  await client.put('/api/v1/settings', { config_key, config_value, change_note })
}

export async function getConfigHistory(): Promise<UserConfigHistory[]> {
  const res = await client.get('/api/v1/settings/config-history')
  return (res.data.data?.items ?? []) as UserConfigHistory[]
}

export async function revertConfig(id: number): Promise<void> {
  await client.post(`/api/v1/settings/config-history/${id}/revert`)
}

// ─────────────────── Phase 10 §6.9 YAML 导入/导出 ───────────────────

/** 导出当前 user_config 为 YAML 文本（返回文件内容，前端触发下载）。 */
export async function exportSettingsYaml(): Promise<string> {
  const res = await client.get('/api/v1/settings/export', {
    responseType: 'text',
    transformResponse: [(data) => data],  // 避免 axios 尝试 JSON 解析
  })
  return res.data as string
}

/** 导入 YAML：dry_run=true 仅预览差异；false 则应用 + 返回差异。 */
export async function importSettingsYaml(
  yaml_content: string,
  dry_run: boolean,
): Promise<ImportResponse> {
  const res = await client.post('/api/v1/settings/import', { yaml_content, dry_run })
  return res.data.data as ImportResponse
}

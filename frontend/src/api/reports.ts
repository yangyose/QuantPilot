import client from './client'
import type { Report } from '@/types/api'

export interface ReportListParams {
  report_type?: string
  limit?: number
  offset?: number
}

export interface GenerateReportBody {
  start_date: string
  end_date: string
}

export async function getReports(params?: ReportListParams): Promise<Report[]> {
  const res = await client.get('/api/v1/reports', { params })
  return (res.data.data?.items ?? []) as Report[]
}

export async function getReport(id: number): Promise<Report> {
  const res = await client.get(`/api/v1/reports/${id}`)
  return res.data.data as Report
}

export async function generateReport(body: GenerateReportBody): Promise<Report> {
  const res = await client.post('/api/v1/reports/generate', body)
  return res.data.data as Report
}

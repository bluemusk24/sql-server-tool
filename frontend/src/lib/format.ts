/**
 * Display helpers shared by the pages that render an investigation's
 * target server.
 */

/**
 * `host:port` for display — except named instances, which carry no port
 * of their own (SQL Browser discovers it), so `HOST\INSTANCE` renders
 * bare and never as `HOST\INST:1433` (#37).
 */
export function formatServerTarget(host: string, port: number): string {
  return host.includes("\\") ? host : `${host}:${port}`;
}

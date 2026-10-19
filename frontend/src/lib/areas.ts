/**
 * Human titles for every issue area, mirroring the backend's
 * `app.analysis.AREA_TITLES`.
 *
 * Kept in one place so the report page and the fleet view label
 * areas identically — including the refresh & replication area the
 * analysis topic centers on.
 */

export const AREA_TITLES: Record<string, string> = {
  refresh: "Database refresh & replication",
  availability: "Database availability",
  blocking: "Blocking",
  storage: "Storage pressure",
  errors: "SQL Server errors",
  deadlocks: "Deadlocks",
  cpu: "High CPU",
  agent_jobs: "SQL Agent jobs",
  queries: "Slow / resource-intensive queries",
};

export function areaTitle(area: string): string {
  return AREA_TITLES[area] ?? area;
}

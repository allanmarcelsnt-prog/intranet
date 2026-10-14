export function archiveDeadline(savedAt: Date): Date {
  const result = new Date(savedAt);
  const day = result.getUTCDate();
  result.setUTCDate(1); result.setUTCMonth(result.getUTCMonth() + 1);
  const last = new Date(Date.UTC(result.getUTCFullYear(), result.getUTCMonth() + 1, 0)).getUTCDate();
  result.setUTCDate(Math.min(day, last));
  return result;
}
export function isArchiveExpired(archived: boolean, savedAt: string | null, now: Date): boolean {
  return archived && savedAt !== null && archiveDeadline(new Date(savedAt)).getTime() <= now.getTime();
}
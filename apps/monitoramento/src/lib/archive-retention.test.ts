import { it, expect } from 'vitest';
import { archiveDeadline, isArchiveExpired } from './archive-retention';
it('retains archives for one calendar month after saving', () => {
  expect(archiveDeadline(new Date('2026-10-06T18:08:00Z')).toISOString()).toBe('2026-11-06T18:08:00.000Z');
  expect(isArchiveExpired(true, '2026-10-06T18:08:00Z', new Date('2026-11-06T18:07:59Z'))).toBe(false);
  expect(isArchiveExpired(true, '2026-10-06T18:08:00Z', new Date('2026-11-06T18:08:00Z'))).toBe(true);
});
it('never expires unarchived records or legacy archives without a saved-copy timestamp', () => {
  expect(isArchiveExpired(false, '2025-01-01T00:00:00Z', new Date('2026-10-06'))).toBe(false);
  expect(isArchiveExpired(true, null, new Date('2026-10-06'))).toBe(false);
});
it('clamps end-of-month dates rather than counting thirty days', () => {
  expect(archiveDeadline(new Date('2026-01-31T12:00:00Z')).toISOString()).toBe('2026-02-28T12:00:00.000Z');
});
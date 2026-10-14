import { describe, expect, it } from 'vitest';
import { canOperate, canChangeOperator } from './access';
describe('operator access', () => {
  it('new operators need administrator approval', () => { expect(canOperate('pending')).toBe(false); expect(canOperate('approved')).toBe(true); });
  it('former employees cannot operate', () => { expect(canOperate('removed')).toBe(false); });
  it('only administrators manage other accounts', () => { expect(canChangeOperator(false, 'a', 'b')).toBe(false); expect(canChangeOperator(true, 'a', 'b')).toBe(true); expect(canChangeOperator(true, 'a', 'a')).toBe(false); });
});
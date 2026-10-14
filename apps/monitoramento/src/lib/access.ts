export type AccessStatus = 'pending' | 'approved' | 'removed';
export const canOperate = (status: string | undefined) => status === 'approved';
export const canChangeOperator = (admin: boolean, actor: string, target: string) => admin && actor !== target;
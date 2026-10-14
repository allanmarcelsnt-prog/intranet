import { createServerFn } from '@tanstack/react-start';
import { z } from 'zod';
import { requireSupabaseAuth } from '@/integrations/supabase/auth-middleware';
export const removeOperator = createServerFn({ method: 'POST' })
  .middleware([requireSupabaseAuth])
  .inputValidator((input: unknown) => z.object({ userId: z.string().uuid() }).parse(input))
  .handler(async ({ data, context }) => {
    const { data: admin, error } = await context.supabase.rpc('has_role', { _user_id: context.userId, _role: 'admin' });
    if (error || !admin || context.userId === data.userId) throw new Error('Apenas administradores podem remover outros operadores.');
    // Block RLS access first, including already-issued tokens, even if Auth removal fails.
    const blocked = await context.supabase.rpc('set_operator_access', { _user_id: data.userId, _status: 'removed' });
    if (blocked.error) throw new Error(blocked.error.message);
    const { supabaseAdmin } = await import('@/integrations/supabase/client.server');
    const result = await supabaseAdmin.auth.admin.deleteUser(data.userId, true);
    if (result.error) throw new Error('Acesso bloqueado; a remoção da conta precisa ser tentada novamente.');
    return { ok: true };
  });